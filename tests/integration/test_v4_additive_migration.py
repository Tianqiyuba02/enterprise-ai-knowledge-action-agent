import hashlib
import json
import os
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from isolated_postgres import (
    isolated_settings_for_engine,
    isolated_test_engine,
    restore_app_database_url,
)
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import create_knowledge_session_factory
from app.ingestion.models import EmbeddingProfile, IngestionOutcome
from app.ingestion.repository import KnowledgeIngestionRepository
from app.ingestion.service import KnowledgeIngestionService
from app.workflow.calendar import (
    V4_CALENDAR_JURISDICTION,
    V4_CALENDAR_VERSION,
    VIC_2026_STATEWIDE_HOLIDAYS,
)
from app.workflow.domain import (
    ActionType,
    ActorType,
    ChallengeStatus,
    LeaveRequestStatus,
    LeaveType,
    WorkflowState,
)

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]


class _DeterministicCorpusEmbedder:
    profile = EmbeddingProfile()

    def embed_documents(
        self,
        contents: Sequence[str],
        *,
        title: str,
    ) -> tuple[tuple[float, ...], ...]:
        assert title
        return tuple(
            tuple([float(index + 1) / 100] * self.profile.dimension)
            for index, _content in enumerate(contents)
        )


@pytest.fixture(scope="session")
def additive_engine() -> Iterator[Engine]:
    with isolated_test_engine(
        prefix="knowledge_agent_v4_additive",
        migration_target="0001_v2_knowledge",
    ) as engine:
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0001_v2_knowledge"
            )
            assert connection.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0

        settings = isolated_settings_for_engine(engine)
        service = KnowledgeIngestionService(
            repository=KnowledgeIngestionRepository(create_knowledge_session_factory(engine)),
            embedder=_DeterministicCorpusEmbedder(),
        )
        results = service.ingest_directory(Path("corpus/v2"))
        assert len(results) == 13
        assert all(result.outcome is IngestionOutcome.INSERTED for result in results)
        assert sum(result.chunk_count for result in results) == 47
        it_guide = next(result for result in results if result.doc_code == "SOP-IT-003")
        assert it_guide.version == "1.0"
        assert it_guide.chunk_count == 5

        previous = os.environ.get("APP_DATABASE_URL")
        os.environ["APP_DATABASE_URL"] = settings.database_url.get_secret_value()
        try:
            command.upgrade(AlembicConfig("alembic.ini"), "head")
        finally:
            restore_app_database_url(previous)
        yield engine


@pytest.fixture
def connection(additive_engine: Engine) -> Iterator[Connection]:
    with additive_engine.connect() as database_connection:
        transaction = database_connection.begin()
        try:
            yield database_connection
        finally:
            transaction.rollback()


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _insert_action(connection: Connection) -> uuid.UUID:
    action_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO action_workflows (
                action_id, owner_subject_id, owner_employee_id, jurisdiction,
                action_type, current_revision
            ) VALUES (
                :action_id, :subject_id, :employee_id, :jurisdiction,
                :action_type, 1
            )
            """
        ),
        {
            "action_id": action_id,
            "subject_id": "subj_test",
            "employee_id": "EMP-1001",
            "jurisdiction": "AU-VIC",
            "action_type": ActionType.SUBMIT_ANNUAL_LEAVE.value,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO action_revisions (
                revision_id, action_id, revision, state, draft_payload, draft_hash,
                authority_snapshot_hash, business_request_key, ruleset_version,
                calendar_version, action_expires_at
            ) VALUES (
                :revision_id, :action_id, 1, :state, '{}'::jsonb, :draft_hash,
                :authority_hash, :business_key, 'v4-annual-leave-1',
                :calendar_version, :expires_at
            )
            """
        ),
        {
            "revision_id": uuid.uuid4(),
            "action_id": action_id,
            "state": WorkflowState.AWAITING_CONFIRMATION.value,
            "draft_hash": _sha(f"draft:{action_id}"),
            "authority_hash": _sha(f"authority:{action_id}"),
            "business_key": f"business-{action_id}",
            "calendar_version": V4_CALENDAR_VERSION,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        },
    )
    return action_id


def test_alembic_head_includes_applied_v4_migrations(additive_engine: Engine) -> None:
    with additive_engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "0008_v5_m3_public_demo"


def test_v2_corpus_and_holiday_seed_survive_additive_upgrade(additive_engine: Engine) -> None:
    inspector = inspect(additive_engine)
    table_names = set(inspector.get_table_names())
    assert {
        "documents",
        "document_chunks",
        "public_holidays",
        "action_workflows",
        "action_revisions",
        "confirmation_challenges",
        "action_audit_events",
        "leave_requests",
        "it_tickets",
    } <= table_names
    assert "workflow_outbox" not in table_names
    assert "action_execution_ledger" not in table_names
    assert "checkpoints" not in table_names
    assert "checkpoint_blobs" not in table_names
    assert "checkpoint_writes" not in table_names
    assert "checkpoint_migrations" not in table_names
    assert "action_thread_map" not in table_names
    assert "reconciliation_probes" not in table_names

    with additive_engine.connect() as connection:
        documents = connection.execute(text("SELECT count(*) FROM documents")).scalar_one()
        chunks = connection.execute(text("SELECT count(*) FROM document_chunks")).scalar_one()
        it_guide = connection.execute(
            text(
                """
                SELECT d.status, d.embedding_model_id, d.embedding_dimension,
                       count(dc.id) AS section_count,
                       array_agg(dc.anchor ORDER BY dc.chunk_index) AS anchors
                FROM documents d
                JOIN document_chunks dc ON dc.document_id = d.id
                WHERE d.doc_code = 'SOP-IT-003' AND d.version = '1.0'
                GROUP BY d.id
                """
            )
        ).one()
        holidays = connection.execute(
            text(
                """
                SELECT holiday_date, holiday_name
                FROM public_holidays
                WHERE jurisdiction = :jurisdiction AND calendar_version = :calendar_version
                ORDER BY holiday_date
                """
            ),
            {
                "jurisdiction": V4_CALENDAR_JURISDICTION,
                "calendar_version": V4_CALENDAR_VERSION,
            },
        ).all()

    assert documents == 13
    assert chunks == 47
    assert it_guide.status == "approved"
    assert it_guide.embedding_model_id == "gemini-embedding-2"
    assert it_guide.embedding_dimension == 768
    assert it_guide.section_count == 5
    assert "urgency-guidance" in it_guide.anchors
    assert len(holidays) == 14
    assert [(row.holiday_date, row.holiday_name) for row in holidays] == list(
        VIC_2026_STATEWIDE_HOLIDAYS
    )


def test_m2_schema_accepts_a_second_immutable_revision(connection: Connection) -> None:
    action_id = _insert_action(connection)
    connection.execute(
        text(
            """
            INSERT INTO action_revisions (
                revision_id, action_id, revision, state, draft_payload, draft_hash,
                authority_snapshot_hash, business_request_key, ruleset_version,
                calendar_version, action_expires_at
            ) VALUES (
                :revision_id, :action_id, 2, :state, '{}'::jsonb, :draft_hash,
                :authority_hash, :business_key, 'it-support-v1',
                'not-applicable', :expires_at
            )
            """
        ),
        {
            "revision_id": uuid.uuid4(),
            "action_id": action_id,
            "state": WorkflowState.SUPERSEDED.value,
            "draft_hash": _sha(f"draft-2:{action_id}"),
            "authority_hash": _sha(f"authority-2:{action_id}"),
            "business_key": f"revision-2-{action_id}",
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        },
    )
    connection.execute(
        text("UPDATE action_workflows SET current_revision = 2 WHERE action_id = :action_id"),
        {"action_id": action_id},
    )
    assert (
        connection.execute(
            text("SELECT current_revision FROM action_workflows WHERE action_id = :action_id"),
            {"action_id": action_id},
        ).scalar_one()
        == 2
    )


def test_superseded_is_a_valid_m2_revision_state(connection: Connection) -> None:
    action_id = _insert_action(connection)
    connection.execute(
        text("UPDATE action_revisions SET state = 'SUPERSEDED' WHERE action_id = :action_id"),
        {"action_id": action_id},
    )
    assert (
        connection.execute(
            text("SELECT state FROM action_revisions WHERE action_id = :action_id"),
            {"action_id": action_id},
        ).scalar_one()
        == WorkflowState.SUPERSEDED.value
    )


def test_confirmation_challenges_have_no_plaintext_token_column(additive_engine: Engine) -> None:
    columns = {
        column["name"] for column in inspect(additive_engine).get_columns("confirmation_challenges")
    }
    assert "token_hash" in columns
    assert {"token", "plaintext_token", "confirmation_token"}.isdisjoint(columns)


def test_only_one_active_challenge_per_revision(connection: Connection) -> None:
    action_id = _insert_action(connection)
    payload = {
        "action_id": action_id,
        "subject_id": "subj_test",
        "session_id": "sess_test",
        "draft_hash": _sha(f"draft:{action_id}"),
        "issued_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + timedelta(minutes=10),
        "status": ChallengeStatus.ACTIVE.value,
    }
    connection.execute(
        text(
            """
            INSERT INTO confirmation_challenges (
                challenge_id, action_id, revision, owner_subject_id,
                confirmation_session_id, draft_hash, token_hash, status, issued_at, expires_at
            ) VALUES (
                :challenge_id, :action_id, 1, :subject_id, :session_id, :draft_hash,
                :token_hash, :status, :issued_at, :expires_at
            )
            """
        ),
        {**payload, "challenge_id": uuid.uuid4(), "token_hash": _sha("token-a")},
    )
    with pytest.raises(IntegrityError):
        connection.execute(
            text(
                """
                INSERT INTO confirmation_challenges (
                    challenge_id, action_id, revision, owner_subject_id,
                    confirmation_session_id, draft_hash, token_hash, status, issued_at, expires_at
                ) VALUES (
                    :challenge_id, :action_id, 1, :subject_id, :session_id, :draft_hash,
                    :token_hash, :status, :issued_at, :expires_at
                )
                """
            ),
            {**payload, "challenge_id": uuid.uuid4(), "token_hash": _sha("token-b")},
        )


def test_retired_execution_tables_are_absent(additive_engine: Engine) -> None:
    inspector = inspect(additive_engine)
    columns = {column["name"] for column in inspector.get_columns("action_workflows")}
    leave_columns = {column["name"] for column in inspector.get_columns("leave_requests")}
    assert "langgraph_thread_id" not in columns
    assert "execution_key" not in leave_columns
    assert "workflow_outbox" not in inspector.get_table_names()
    assert "action_execution_ledger" not in inspector.get_table_names()


def test_leave_request_business_key_is_unique(connection: Connection) -> None:
    first = _insert_action(connection)
    second = _insert_action(connection)
    now = datetime.now(UTC)
    connection.execute(
        text(
            """
            INSERT INTO leave_requests (
                leave_request_id, employee_id, leave_type, start_date, end_date,
                requested_hours, status, submitted_at, business_request_key,
                source_action_id, source_action_revision, calendar_version, ruleset_version
            ) VALUES (
                :leave_request_id, 'EMP-1001', :leave_type, :start_date, :end_date,
                :hours, :status, :submitted_at, :business_key,
                :action_id, 1, :calendar_version, 'v4-annual-leave-1'
            )
            """
        ),
        {
            "leave_request_id": uuid.uuid4(),
            "leave_type": LeaveType.ANNUAL.value,
            "start_date": date(2026, 9, 1),
            "end_date": date(2026, 9, 1),
            "hours": Decimal("7.60"),
            "status": LeaveRequestStatus.SUBMITTED.value,
            "submitted_at": now,
            "business_key": "leave-business-shared",
            "action_id": first,
            "calendar_version": V4_CALENDAR_VERSION,
        },
    )
    with pytest.raises(IntegrityError):
        connection.execute(
            text(
                """
                INSERT INTO leave_requests (
                    leave_request_id, employee_id, leave_type, start_date, end_date,
                    requested_hours, status, submitted_at, business_request_key,
                    source_action_id, source_action_revision, calendar_version, ruleset_version
                ) VALUES (
                    :leave_request_id, 'EMP-1001', :leave_type, :start_date, :end_date,
                    :hours, :status, :submitted_at, :business_key,
                    :action_id, 1, :calendar_version, 'v4-annual-leave-1'
                )
                """
            ),
            {
                "leave_request_id": uuid.uuid4(),
                "leave_type": LeaveType.ANNUAL.value,
                "start_date": date(2026, 9, 2),
                "end_date": date(2026, 9, 2),
                "hours": Decimal("7.60"),
                "status": LeaveRequestStatus.SUBMITTED.value,
                "submitted_at": now,
                "business_key": "leave-business-shared",
                "action_id": second,
                "calendar_version": V4_CALENDAR_VERSION,
            },
        )


def test_audit_event_can_be_inserted_in_transaction(connection: Connection) -> None:
    action_id = _insert_action(connection)
    connection.execute(
        text(
            """
            INSERT INTO action_audit_events (
                event_id, action_id, revision, event_type, actor_type, actor_subject_id,
                from_state, to_state, safe_metadata
            ) VALUES (
                :event_id, :action_id, 1, 'created', :actor_type, 'subj_test',
                NULL, :to_state, CAST(:metadata AS jsonb)
            )
            """
        ),
        {
            "event_id": uuid.uuid4(),
            "action_id": action_id,
            "actor_type": ActorType.SYSTEM.value,
            "to_state": WorkflowState.AWAITING_CONFIRMATION.value,
            "metadata": json.dumps({"action_type": ActionType.SUBMIT_ANNUAL_LEAVE.value}),
        },
    )
    count = connection.execute(
        text("SELECT count(*) FROM action_audit_events WHERE action_id = :action_id"),
        {"action_id": action_id},
    ).scalar_one()
    assert count == 1


def test_source_action_id_is_unique(connection: Connection) -> None:
    first = _insert_action(connection)
    second = _insert_action(connection)
    now = datetime.now(UTC)
    payload = {
        "leave_type": LeaveType.ANNUAL.value,
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 1),
        "hours": Decimal("7.60"),
        "status": LeaveRequestStatus.SUBMITTED.value,
        "submitted_at": now,
        "calendar_version": V4_CALENDAR_VERSION,
        "action_id": first,
    }
    connection.execute(
        text(
            """
            INSERT INTO leave_requests (
                leave_request_id, employee_id, leave_type, start_date, end_date,
                requested_hours, status, submitted_at, business_request_key,
                source_action_id, source_action_revision, calendar_version, ruleset_version
            ) VALUES (
                :leave_request_id, 'EMP-1001', :leave_type, :start_date, :end_date,
                :hours, :status, :submitted_at, :business_key,
                :action_id, 1, :calendar_version, 'v4-annual-leave-1'
            )
            """
        ),
        {
            **payload,
            "leave_request_id": uuid.uuid4(),
            "business_key": "leave-source-a",
        },
    )
    with pytest.raises(IntegrityError):
        connection.execute(
            text(
                """
                INSERT INTO leave_requests (
                    leave_request_id, employee_id, leave_type, start_date, end_date,
                    requested_hours, status, submitted_at, business_request_key,
                    source_action_id, source_action_revision, calendar_version, ruleset_version
                ) VALUES (
                    :leave_request_id, 'EMP-1001', :leave_type, :start_date, :end_date,
                    :hours, :status, :submitted_at, :business_key,
                    :action_id, 1, :calendar_version, 'v4-annual-leave-1'
                )
                """
            ),
            {
                **payload,
                "leave_request_id": uuid.uuid4(),
                "business_key": "leave-source-b",
                "action_id": first,
                "start_date": date(2026, 9, 2),
                "end_date": date(2026, 9, 2),
            },
        )
    assert second


def test_final_occupancy_index_covers_three_states(
    additive_engine: Engine, connection: Connection
) -> None:
    first = _insert_action(connection)
    connection.execute(
        text(
            """
            UPDATE action_revisions
            SET business_request_key = 'shared-occupancy-key'
            WHERE action_id = :action_id
            """
        ),
        {"action_id": first},
    )
    with additive_engine.connect() as inspect_connection:
        definition = inspect_connection.execute(
            text(
                """
                SELECT pg_get_indexdef(indexrelid)
                FROM pg_index
                JOIN pg_class ON pg_class.oid = pg_index.indexrelid
                WHERE pg_class.relname = 'uq_action_revisions_final_occupying_business_request_key'
                """
            )
        ).scalar_one()
    for state in (
        "AWAITING_CONFIRMATION",
        "CONFIRMED",
        "SUCCEEDED",
    ):
        assert state in definition
    assert "EXECUTING" not in definition
    assert "UNKNOWN_OUTCOME" not in definition
    assert "RECONCILING" not in definition
    assert "EXPIRED" not in definition
    second = _insert_action(connection)
    with pytest.raises(IntegrityError):
        connection.execute(
            text(
                """
                UPDATE action_revisions
                SET business_request_key = 'shared-occupancy-key'
                WHERE action_id = :action_id
                """
            ),
            {"action_id": second},
        )


def test_final_three_state_occupancy_index_exists(additive_engine: Engine) -> None:
    inspector = inspect(additive_engine)
    index_names = {index["name"] for index in inspector.get_indexes("action_revisions")}
    assert "uq_action_revisions_occupying_business_request_key" not in index_names
    assert "uq_action_revisions_final_occupying_business_request_key" in index_names
    with additive_engine.connect() as connection:
        definition = connection.execute(
            text(
                """
                SELECT pg_get_indexdef(indexrelid)
                FROM pg_index
                JOIN pg_class ON pg_class.oid = pg_index.indexrelid
                WHERE pg_class.relname = 'uq_action_revisions_final_occupying_business_request_key'
                """
            )
        ).scalar_one()
    assert "EXECUTING" not in definition
    assert "AWAITING_CONFIRMATION" in definition
    assert "CONFIRMED" in definition
    assert "SUCCEEDED" in definition


def test_phase1a_invariants_halt_nonterminal_with_leave(connection: Connection) -> None:
    from app.workflow.occupancy import collect_phase1a_invariant_violations

    action_id = _insert_action(connection)
    now = datetime.now(UTC)
    connection.execute(
        text(
            """
            INSERT INTO leave_requests (
                leave_request_id, employee_id, leave_type, start_date, end_date,
                requested_hours, status, submitted_at, business_request_key,
                source_action_id, source_action_revision, calendar_version, ruleset_version
            ) VALUES (
                :leave_request_id, 'EMP-1001', :leave_type, :start_date, :end_date,
                :hours, :status, :submitted_at, :business_key,
                :action_id, 1, :calendar_version, 'v4-annual-leave-1'
            )
            """
        ),
        {
            "leave_request_id": uuid.uuid4(),
            "leave_type": LeaveType.ANNUAL.value,
            "start_date": date(2026, 9, 1),
            "end_date": date(2026, 9, 1),
            "hours": Decimal("7.60"),
            "status": LeaveRequestStatus.SUBMITTED.value,
            "submitted_at": now,
            "business_key": f"business-{action_id}",
            "action_id": action_id,
            "calendar_version": V4_CALENDAR_VERSION,
        },
    )
    findings = collect_phase1a_invariant_violations(connection)
    assert any("AWAITING_CONFIRMATION" in item and "source-linked" in item for item in findings)
    assert any("same-business-key" in item for item in findings)


def test_phase1a_invariants_pass_on_empty_action_tables(connection: Connection) -> None:
    from app.workflow.occupancy import collect_phase1a_invariant_violations

    assert collect_phase1a_invariant_violations(connection) == ()

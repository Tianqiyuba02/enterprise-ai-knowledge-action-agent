import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from langgraph.checkpoint.base import empty_checkpoint
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.workflow.authority import AuthoritySnapshot, CanonicalDraft
from app.workflow.calendar import V4_CALENDAR_VERSION
from app.workflow.canonical import business_request_key
from app.workflow.checkpointing import open_postgres_checkpointer
from app.workflow.domain import ActionType, LeaveType, WorkflowState
from app.workflow.errors import (
    OrchestrationAuthorityError,
    ThreadBindingError,
    WorkflowOwnershipError,
)
from app.workflow.orchestration import WorkflowOrchestrationService
from app.workflow.workflow_repository import NewWorkflowRevision, WorkflowRepository

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]

ALEX_SUBJECT = "subj_9f2c4e81a6b047d3"
SAM_SUBJECT = "subj_1a8e5c03d7f249b6"


def _replace_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[KnowledgeSettings]:
    live = load_knowledge_settings()
    admin_url = live.database_url.get_secret_value()
    database_name = f"knowledge_agent_v4_wf_{uuid.uuid4().hex[:12]}"
    isolated_url = _replace_database(admin_url, database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        monkeypatch.setenv("APP_DATABASE_URL", isolated_url)
        command.upgrade(AlembicConfig("alembic.ini"), "head")
        yield load_knowledge_settings()
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database_name AND pid <> pg_backend_pid()
                    """
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


@pytest.fixture
def engine(isolated_settings: KnowledgeSettings) -> Iterator[Engine]:
    engine = create_knowledge_engine(isolated_settings)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_knowledge_session_factory(engine)


def _create_action(
    session: Session,
    *,
    owner_subject_id: str,
    owner_employee_id: str,
    start: date,
) -> tuple[UUID, str]:
    snapshot = AuthoritySnapshot(
        employee_id=owner_employee_id,
        jurisdiction="AU-VIC",
        work_days=("monday", "tuesday", "wednesday", "thursday", "friday"),
        hours_per_day=Decimal("7.60"),
        timezone="Australia/Melbourne",
        trusted_base_balance_hours=Decimal("76.00"),
        committed_submitted_hours=Decimal("0.00"),
        effective_available_hours=Decimal("76.00"),
        calendar_version=V4_CALENDAR_VERSION,
        ruleset_version="v4-annual-leave-1",
    )
    draft = CanonicalDraft(
        action_type=ActionType.SUBMIT_ANNUAL_LEAVE.value,
        leave_type=LeaveType.ANNUAL.value,
        start_date=start,
        end_date=start,
        requested_hours=Decimal("7.60"),
        projected_balance_hours=Decimal("68.40"),
        readiness="ready",
        reason="Family visit",
        calendar_version=V4_CALENDAR_VERSION,
        ruleset_version="v4-annual-leave-1",
        authority_snapshot_hash=snapshot.fingerprint(),
    )
    workflow, _revision = WorkflowRepository().create_workflow_and_revision(
        session,
        NewWorkflowRevision(
            owner_subject_id=owner_subject_id,
            owner_employee_id=owner_employee_id,
            jurisdiction="AU-VIC",
            action_type=ActionType.SUBMIT_ANNUAL_LEAVE,
            state=WorkflowState.AWAITING_CONFIRMATION,
            draft_payload={"leave_type": "annual", "reason": "Family visit"},
            draft_hash=draft.fingerprint(),
            authority_snapshot_hash=snapshot.fingerprint(),
            business_request_key=business_request_key(
                employee_id=owner_employee_id,
                leave_type="annual",
                start_date=start,
                end_date=start,
            ),
            ruleset_version="v4-annual-leave-1",
            calendar_version=V4_CALENDAR_VERSION,
            action_expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    return workflow.action_id, workflow.langgraph_thread_id


def _count(connection, table: str) -> int:
    return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            "leave_requests": _count(connection, "leave_requests"),
            "ledger": _count(connection, "action_execution_ledger"),
            "outbox": _count(connection, "workflow_outbox"),
            "holidays": _count(connection, "public_holidays"),
            "documents": _count(connection, "documents"),
        }


def test_resume_is_wake_only_and_postgres_confirms(
    isolated_settings: KnowledgeSettings,
    engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    service = WorkflowOrchestrationService(session_factory)
    with session_factory() as session:
        action_id, thread_id = _create_action(
            session,
            owner_subject_id=ALEX_SUBJECT,
            owner_employee_id="EMP-1001",
            start=date(2026, 9, 1),
        )
        session.commit()

    started = service.start(
        action_id=action_id,
        owner_subject_id=ALEX_SUBJECT,
        settings=isolated_settings,
    )
    assert started["__interrupt__"][0].value == {
        "action_id": str(action_id),
        "revision": 1,
        "waiting_for": "out_of_band_confirmation",
    }
    assert started["langgraph_thread_id"] == thread_id

    lying_resume = service.resume(
        action_id=action_id,
        owner_subject_id=ALEX_SUBJECT,
        resume_payload={"confirmed": True, "authorized": True, "execute": True},
        settings=isolated_settings,
    )
    assert lying_resume["__interrupt__"][0].value["waiting_for"] == "out_of_band_confirmation"
    with session_factory() as session:
        assert WorkflowRepository().get_revision(session, action_id).state == (
            WorkflowState.AWAITING_CONFIRMATION.value
        )

    with session_factory() as session:
        WorkflowRepository().apply_revision_state(
            session,
            action_id=action_id,
            state=WorkflowState.CONFIRMED,
        )
        session.commit()

    confirmed = service.resume(
        action_id=action_id,
        owner_subject_id=ALEX_SUBJECT,
        resume_payload={"confirmed": False},
        settings=isolated_settings,
    )
    assert "__interrupt__" not in confirmed
    assert confirmed["observed_state"] == WorkflowState.CONFIRMED.value

    duplicate = service.resume(
        action_id=action_id,
        owner_subject_id=ALEX_SUBJECT,
        resume_payload={"execute": True},
        settings=isolated_settings,
    )
    assert duplicate["observed_state"] == WorkflowState.CONFIRMED.value
    counts = _counts(engine)
    assert counts["leave_requests"] == 0
    assert counts["ledger"] == 0
    assert counts["outbox"] == 0
    assert counts["holidays"] == 14


def test_process_restart_recovers_same_thread(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        action_id, thread_id = _create_action(
            session,
            owner_subject_id=ALEX_SUBJECT,
            owner_employee_id="EMP-1001",
            start=date(2026, 9, 2),
        )
        session.commit()

    first = WorkflowOrchestrationService(session_factory)
    started = first.start(
        action_id=action_id,
        owner_subject_id=ALEX_SUBJECT,
        settings=isolated_settings,
    )
    assert started["__interrupt__"]
    del first

    second = WorkflowOrchestrationService(session_factory)
    recovered = second.resume(
        action_id=action_id,
        owner_subject_id=ALEX_SUBJECT,
        resume_payload={"confirmed": True},
        settings=isolated_settings,
    )
    assert recovered["langgraph_thread_id"] == thread_id
    assert recovered["__interrupt__"][0].value["action_id"] == str(action_id)

    with session_factory() as session:
        WorkflowRepository().apply_revision_state(
            session,
            action_id=action_id,
            state=WorkflowState.CONFIRMED,
        )
        session.commit()

    finished = second.resume(
        action_id=action_id,
        owner_subject_id=ALEX_SUBJECT,
        resume_payload={},
        settings=isolated_settings,
    )
    assert "__interrupt__" not in finished
    assert finished["observed_state"] == WorkflowState.CONFIRMED.value


def test_owner_and_thread_isolation(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    service = WorkflowOrchestrationService(session_factory)
    with session_factory() as session:
        alex_id, alex_thread = _create_action(
            session,
            owner_subject_id=ALEX_SUBJECT,
            owner_employee_id="EMP-1001",
            start=date(2026, 9, 3),
        )
        _sam_id, sam_thread = _create_action(
            session,
            owner_subject_id=SAM_SUBJECT,
            owner_employee_id="EMP-1002",
            start=date(2026, 9, 4),
        )
        session.commit()

    with pytest.raises(WorkflowOwnershipError):
        service.start(
            action_id=alex_id,
            owner_subject_id=SAM_SUBJECT,
            settings=isolated_settings,
        )
    with pytest.raises(ThreadBindingError):
        service.load_owner_action(
            action_id=alex_id,
            owner_subject_id=ALEX_SUBJECT,
            requested_thread_id=sam_thread,
        )

    started = service.start(
        action_id=alex_id,
        owner_subject_id=ALEX_SUBJECT,
        settings=isolated_settings,
    )
    assert started["langgraph_thread_id"] == alex_thread
    with open_postgres_checkpointer(isolated_settings) as checkpointer:
        foreign = checkpointer.get({"configurable": {"thread_id": sam_thread}})
        own = checkpointer.get({"configurable": {"thread_id": alex_thread}})
    assert foreign is None
    assert own is not None


def test_corrupt_checkpoint_is_orchestration_failure(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    service = WorkflowOrchestrationService(session_factory)
    with session_factory() as session:
        action_id, thread_id = _create_action(
            session,
            owner_subject_id=ALEX_SUBJECT,
            owner_employee_id="EMP-1001",
            start=date(2026, 9, 7),
        )
        session.commit()

    service.start(
        action_id=action_id,
        owner_subject_id=ALEX_SUBJECT,
        settings=isolated_settings,
    )
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    with open_postgres_checkpointer(isolated_settings) as checkpointer:
        checkpointer.put(config, empty_checkpoint(), {}, {})

    with pytest.raises(OrchestrationAuthorityError):
        service.resume(
            action_id=action_id,
            owner_subject_id=ALEX_SUBJECT,
            settings=isolated_settings,
        )


def test_missing_checkpoint_is_orchestration_failure(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    service = WorkflowOrchestrationService(session_factory)
    with session_factory() as session:
        action_id, _thread_id = _create_action(
            session,
            owner_subject_id=ALEX_SUBJECT,
            owner_employee_id="EMP-1001",
            start=date(2026, 9, 5),
        )
        session.commit()

    with pytest.raises(OrchestrationAuthorityError):
        service.resume(
            action_id=action_id,
            owner_subject_id=ALEX_SUBJECT,
            settings=isolated_settings,
        )


def test_cancelled_postgres_state_overrides_cached_observation(
    isolated_settings: KnowledgeSettings,
    engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    service = WorkflowOrchestrationService(session_factory)
    with session_factory() as session:
        action_id, _thread_id = _create_action(
            session,
            owner_subject_id=ALEX_SUBJECT,
            owner_employee_id="EMP-1001",
            start=date(2026, 9, 6),
        )
        session.commit()

    service.start(
        action_id=action_id,
        owner_subject_id=ALEX_SUBJECT,
        settings=isolated_settings,
    )
    with session_factory() as session:
        WorkflowRepository().apply_revision_state(
            session,
            action_id=action_id,
            state=WorkflowState.CANCELLED,
        )
        session.commit()

    result = service.resume(
        action_id=action_id,
        owner_subject_id=ALEX_SUBJECT,
        resume_payload={"confirmed": True},
        settings=isolated_settings,
    )
    assert result["observed_state"] == WorkflowState.CANCELLED.value
    assert "__interrupt__" not in result
    tables = set(inspect(engine).get_table_names())
    assert {"documents", "document_chunks", "action_workflows", "checkpoints"} <= tables
    assert _counts(engine)["leave_requests"] == 0

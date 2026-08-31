import hashlib
import os
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.agent.leave_models import LeavePreparationStatus, LeaveRequestDraft
from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.errors import ActionCreationIdentityError
from app.identity import AuthenticatedEmployeeContext
from app.workflow.action_creation import (
    AUDIT_ACTION_PREPARED,
    PREPARE_CONTENTION_ATTEMPTS,
    ActionCreationDisposition,
    ActionCreationFailpoints,
    ActionCreationService,
)
from app.workflow.canonical import business_request_key
from app.workflow.domain import V4_REVISION, ChallengeStatus, WorkflowState
from app.workflow.executable_preparation import V4ExecutablePreparationService
from app.workflow.execution import ExecutionReservationService, ReservationOutcome
from app.workflow.time import database_now

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]

ALEX = DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"]
JORDAN = DEMO_IDENTITY_BINDINGS["demo-v1-3b8e6d50"]


def _replace_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[KnowledgeSettings]:
    live = load_knowledge_settings()
    admin_url = live.database_url.get_secret_value()
    database_name = f"knowledge_agent_v4_ac_{uuid.uuid4().hex[:12]}"
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


@pytest.fixture
def service(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> ActionCreationService:
    return ActionCreationService(session_factory, isolated_settings)


def _draft(
    *,
    start: date = date(2026, 10, 21),
    end: date | None = None,
    requested_hours: Decimal = Decimal("80.00"),
    reason: str | None = "Family visit",
) -> LeaveRequestDraft:
    return LeaveRequestDraft(
        leave_type="annual",
        start_date=start,
        end_date=end or start,
        scheduled_work_days=2,
        requested_hours=requested_hours,
        current_balance_hours=Decimal("76.00"),
        projected_balance_hours=Decimal("-4.00"),
        preparation_status=LeavePreparationStatus.READY,
        reason=reason,
        public_holiday_check_required=True,
        non_executing=True,
    )


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def _force_state(
    session_factory: sessionmaker[Session],
    action_id: UUID,
    state: WorkflowState,
    *,
    ttl_expired: bool = False,
) -> None:
    with session_factory() as session:
        session.execute(
            text("UPDATE action_revisions SET state = :state WHERE action_id = :action_id"),
            {"state": state.value, "action_id": action_id},
        )
        if ttl_expired:
            session.execute(
                text(
                    """
                    UPDATE action_revisions
                    SET action_expires_at = clock_timestamp() - interval '1 hour',
                        confirmed_at = clock_timestamp() - interval '2 hours',
                        confirmed_expires_at = clock_timestamp() - interval '1 hour'
                    WHERE action_id = :action_id
                    """
                ),
                {"action_id": action_id},
            )
        elif state is WorkflowState.CONFIRMED:
            session.execute(
                text(
                    """
                    UPDATE action_revisions
                    SET confirmed_at = clock_timestamp(),
                        confirmed_expires_at = clock_timestamp() + interval '1 hour'
                    WHERE action_id = :action_id
                    """
                ),
                {"action_id": action_id},
            )
        session.commit()


def test_valid_prepared_result_creates_awaiting_confirmation_action(
    service: ActionCreationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
    isolated_settings: KnowledgeSettings,
) -> None:
    v3 = _draft(requested_hours=Decimal("80.00"))
    created = service.create_or_reuse(ALEX, v3)
    assert created.disposition is ActionCreationDisposition.CREATED
    assert created.state == WorkflowState.AWAITING_CONFIRMATION.value
    assert created.revision == V4_REVISION
    assert created.confirmation_required is True
    assert created.action_id is not None
    assert created.draft is not None
    assert created.draft["requested_hours"] == "7.60"
    assert created.draft["requested_hours"] != format(v3.requested_hours, "f")
    assert created.action_type == "submit_annual_leave"
    with session_factory() as session:
        now = database_now(session)
        workflow = session.execute(
            text(
                """
                SELECT owner_employee_id, owner_subject_id, jurisdiction, langgraph_thread_id
                FROM action_workflows WHERE action_id = :action_id
                """
            ),
            {"action_id": created.action_id},
        ).one()
        revision = session.execute(
            text(
                """
                SELECT revision, state, draft_hash, authority_snapshot_hash,
                       business_request_key, calendar_version, ruleset_version
                FROM action_revisions WHERE action_id = :action_id
                """
            ),
            {"action_id": created.action_id},
        ).one()
        live = V4ExecutablePreparationService().prepare(
            session,
            context=ALEX,
            start_date=v3.start_date,
            end_date=v3.end_date,
            reason=v3.reason,
        )
    assert workflow.owner_employee_id == ALEX.employee_id
    assert workflow.owner_subject_id == ALEX.subject_id
    assert workflow.jurisdiction == ALEX.jurisdiction
    assert workflow.langgraph_thread_id is None
    assert revision.revision == 1
    assert revision.draft_hash == live.draft.fingerprint()
    assert revision.authority_snapshot_hash == live.snapshot.fingerprint()
    assert revision.business_request_key == live.business_request_key
    assert created.draft == live.payload()
    expected_ttl = now + timedelta(seconds=isolated_settings.v4_action_ttl_seconds)
    assert abs((created.action_expires_at - expected_ttl).total_seconds()) < 5
    assert "employee_id" not in created.draft
    assert "subject_id" not in created.draft
    assert "session_id" not in created.draft
    assert _count(engine, "confirmation_challenges") == 0
    assert _count(engine, "action_execution_ledger") == 0
    assert _count(engine, "leave_requests") == 0
    assert _count(engine, "action_workflows") == 1
    assert _count(engine, "action_revisions") == 1
    with session_factory() as session:
        audits = (
            session.execute(
                text("SELECT event_type FROM action_audit_events WHERE action_id = :action_id"),
                {"action_id": created.action_id},
            )
            .scalars()
            .all()
        )
    assert AUDIT_ACTION_PREPARED in audits


def test_holiday_adjusted_draft_differs_from_v3_preview(
    service: ActionCreationService,
) -> None:
    v3 = _draft(start=date(2026, 9, 24), end=date(2026, 9, 25), requested_hours=Decimal("15.20"))
    created = service.create_or_reuse(ALEX, v3)
    assert created.disposition is ActionCreationDisposition.CREATED
    assert created.draft is not None
    assert created.draft["requested_hours"] == "7.60"
    assert created.draft["scheduled_work_days"] == 1
    assert created.draft["start_date"] == "2026-09-24"
    assert created.draft["end_date"] == "2026-09-25"


@pytest.mark.parametrize(
    ("context", "field"),
    [
        (
            AuthenticatedEmployeeContext(
                employee_id=ALEX.employee_id,
                session_id=ALEX.session_id,
                jurisdiction=ALEX.jurisdiction,
            ),
            "subject_id",
        ),
        (
            AuthenticatedEmployeeContext(
                employee_id=ALEX.employee_id,
                subject_id=ALEX.subject_id,
                jurisdiction=ALEX.jurisdiction,
            ),
            "session_id",
        ),
        (
            AuthenticatedEmployeeContext(
                employee_id=ALEX.employee_id,
                subject_id=ALEX.subject_id,
                session_id=ALEX.session_id,
            ),
            "jurisdiction",
        ),
    ],
)
def test_missing_trusted_identity_creates_no_action(
    service: ActionCreationService,
    engine: Engine,
    context: AuthenticatedEmployeeContext,
    field: str,
) -> None:
    with pytest.raises(ActionCreationIdentityError):
        service.create_or_reuse(context, _draft())
    assert _count(engine, "action_workflows") == 0
    assert field


@pytest.mark.parametrize(
    ("start", "end", "reason"),
    [
        (date(2027, 1, 4), date(2027, 1, 4), "calendar_uncovered"),
        (date(2026, 10, 24), date(2026, 10, 25), "no_scheduled_work"),
        (date(2026, 10, 5), date(2026, 10, 19), "insufficient_balance"),
    ],
)
def test_non_executable_preparation_creates_no_action(
    service: ActionCreationService,
    engine: Engine,
    start: date,
    end: date,
    reason: str,
) -> None:
    result = service.create_or_reuse(ALEX, _draft(start=start, end=end))
    assert result.disposition is ActionCreationDisposition.NOT_CREATED
    assert result.action_id is None
    assert result.ineligibility_reason == reason
    assert _count(engine, "action_workflows") == 0
    assert _count(engine, "action_revisions") == 0
    assert _count(engine, "action_audit_events") == 0
    assert _count(engine, "leave_requests") == 0


def test_failed_create_transaction_rolls_back_all_rows(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    service = ActionCreationService(
        session_factory,
        isolated_settings,
        failpoints=ActionCreationFailpoints(
            raise_after_revision_before_commit=RuntimeError("forced")
        ),
    )
    with pytest.raises(RuntimeError, match="forced"):
        service.create_or_reuse(ALEX, _draft())
    assert _count(engine, "action_workflows") == 0
    assert _count(engine, "action_revisions") == 0
    assert _count(engine, "action_audit_events") == 0


def test_repeated_prepare_reuses_live_awaiting_and_confirmed(
    service: ActionCreationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    first = service.create_or_reuse(ALEX, _draft())
    second = service.create_or_reuse(ALEX, _draft())
    assert first.action_id == second.action_id
    assert second.disposition is ActionCreationDisposition.REUSED_EXISTING
    assert _count(engine, "action_workflows") == 1
    _force_state(session_factory, first.action_id, WorkflowState.CONFIRMED)
    third = service.create_or_reuse(ALEX, _draft())
    assert third.action_id == first.action_id
    assert third.disposition is ActionCreationDisposition.REUSED_EXISTING
    assert third.state == WorkflowState.CONFIRMED.value
    assert _count(engine, "action_workflows") == 1


@pytest.mark.skip(reason="retired after simplified execution cutover")
@pytest.mark.parametrize(
    "state",
    [
        WorkflowState.EXECUTING,
        WorkflowState.UNKNOWN_OUTCOME,
        WorkflowState.RECONCILING,
    ],
)
def test_in_flight_request_creates_no_replacement(
    service: ActionCreationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
    state: WorkflowState,
) -> None:
    first = service.create_or_reuse(ALEX, _draft())
    _force_state(session_factory, first.action_id, state)
    again = service.create_or_reuse(ALEX, _draft())
    assert again.action_id == first.action_id
    assert again.disposition is ActionCreationDisposition.RETURNED_IN_FLIGHT
    assert again.state == state.value
    assert _count(engine, "action_workflows") == 1


def test_succeeded_request_creates_no_duplicate_submission(
    service: ActionCreationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    first = service.create_or_reuse(ALEX, _draft())
    _force_state(session_factory, first.action_id, WorkflowState.SUCCEEDED)
    again = service.create_or_reuse(ALEX, _draft())
    assert again.action_id == first.action_id
    assert again.disposition is ActionCreationDisposition.RETURNED_SUCCEEDED
    assert _count(engine, "action_workflows") == 1


@pytest.mark.parametrize(
    "state",
    [
        WorkflowState.CANCELLED,
        WorkflowState.EXPIRED,
        WorkflowState.STALE,
        WorkflowState.EXECUTION_FAILED,
    ],
)
def test_replaceable_terminal_may_create_new_action(
    service: ActionCreationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
    state: WorkflowState,
) -> None:
    first = service.create_or_reuse(ALEX, _draft(start=date(2026, 10, 22)))
    _force_state(session_factory, first.action_id, state)
    again = service.create_or_reuse(ALEX, _draft(start=date(2026, 10, 22)))
    assert again.disposition is ActionCreationDisposition.CREATED
    assert again.action_id != first.action_id
    assert again.revision == 1
    assert _count(engine, "action_workflows") == 2


def test_concurrent_identical_creation_produces_one_live_action(
    service: ActionCreationService,
    engine: Engine,
) -> None:
    start = threading.Barrier(2)
    results: list = []

    def worker() -> None:
        start.wait()
        results.append(service.create_or_reuse(ALEX, _draft(start=date(2026, 10, 23))))

    threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert {item.action_id for item in results} == {results[0].action_id}
    dispositions = {item.disposition for item in results}
    assert ActionCreationDisposition.CREATED in dispositions
    assert ActionCreationDisposition.REUSED_EXISTING in dispositions
    assert _count(engine, "action_workflows") == 1


def test_wrong_employee_cannot_reuse_another_action(
    service: ActionCreationService,
    engine: Engine,
) -> None:
    alex = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 4)))
    jordan = service.create_or_reuse(JORDAN, _draft(start=date(2026, 11, 4)))
    assert jordan.disposition is ActionCreationDisposition.CREATED
    assert jordan.action_id != alex.action_id
    assert _count(engine, "action_workflows") == 2
    assert alex.draft is not None and jordan.draft is not None
    assert alex.draft["authority_snapshot_hash"] != jordan.draft["authority_snapshot_hash"]
    expected_alex = business_request_key(
        employee_id=ALEX.employee_id,
        leave_type="annual",
        start_date=date(2026, 11, 4),
        end_date=date(2026, 11, 4),
    )
    with engine.connect() as connection:
        keys = connection.execute(
            text("SELECT business_request_key FROM action_revisions")
        ).scalars()
    assert expected_alex in set(keys)


def test_occupancy_conflict_loser_uses_fresh_transaction_and_reuses(
    service: ActionCreationService,
    engine: Engine,
) -> None:
    first = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 6)))
    again = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 6)))
    assert again.disposition is ActionCreationDisposition.REUSED_EXISTING
    assert again.action_id == first.action_id
    assert _count(engine, "action_workflows") == 1
    assert _count(engine, "action_revisions") == 1


def test_loser_locks_authoritative_action_revision(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    created = ActionCreationService(session_factory, isolated_settings).create_or_reuse(
        ALEX, _draft(start=date(2026, 11, 9))
    )
    hold = threading.Event()
    locked = threading.Event()
    service = ActionCreationService(
        session_factory,
        isolated_settings,
        failpoints=ActionCreationFailpoints(
            hold_after_occupying_lock=hold,
            signal_occupying_lock=locked,
        ),
    )
    results: list = []

    def loser() -> None:
        results.append(service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 9))))

    thread = threading.Thread(target=loser)
    thread.start()
    assert locked.wait(timeout=5)
    blocked: list[str] = []

    def blocker() -> None:
        with session_factory() as session:
            session.execute(
                text("SELECT state FROM action_revisions WHERE action_id = :id FOR UPDATE"),
                {"id": created.action_id},
            )
            blocked.append("acquired")
            session.rollback()

    waiter = threading.Thread(target=blocker)
    waiter.start()
    deadline = time.monotonic() + 5.0
    waiting = 0
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND wait_event_type = 'Lock'
                      AND pid <> pg_backend_pid()
                    """
                )
            ).scalar_one()
        if waiting:
            break
        time.sleep(0.01)
    hold.set()
    thread.join(timeout=10)
    waiter.join(timeout=10)
    assert waiting
    assert results[0].disposition is ActionCreationDisposition.REUSED_EXISTING
    assert blocked == ["acquired"]


def test_owner_mismatch_after_conflict_fails_closed(
    service: ActionCreationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    first = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 10)))
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_workflows
                SET owner_employee_id = :employee_id, owner_subject_id = :subject_id
                WHERE action_id = :action_id
                """
            ),
            {
                "employee_id": JORDAN.employee_id,
                "subject_id": JORDAN.subject_id,
                "action_id": first.action_id,
            },
        )
        session.commit()
    again = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 10)))
    assert again.disposition is ActionCreationDisposition.NOT_CREATED
    assert again.action_id is None
    assert again.ineligibility_reason == "authority_inconsistent"
    assert _count(engine, "action_workflows") == 1


def test_draft_mismatch_after_conflict_fails_closed(
    service: ActionCreationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    first = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 11)))
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_revisions
                SET draft_hash = :draft_hash
                WHERE action_id = :action_id
                """
            ),
            {
                "draft_hash": hashlib.sha256(b"tampered-draft").hexdigest(),
                "action_id": first.action_id,
            },
        )
        session.commit()
    again = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 11)))
    assert again.disposition is ActionCreationDisposition.NOT_CREATED
    assert again.action_id is None
    assert again.ineligibility_reason == "authority_inconsistent"
    assert _count(engine, "action_workflows") == 1


def test_expired_awaiting_is_normalized_and_retried(
    service: ActionCreationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    first = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 12)))
    _force_state(
        session_factory, first.action_id, WorkflowState.AWAITING_CONFIRMATION, ttl_expired=True
    )
    with session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO confirmation_challenges (
                    challenge_id, action_id, revision, owner_subject_id,
                    confirmation_session_id, draft_hash, token_hash, status,
                    issued_at, expires_at
                )
                SELECT :challenge_id, action_id, 1, :subject_id, :session_id,
                       draft_hash, :token_hash, :status, clock_timestamp(),
                       clock_timestamp() + interval '10 minutes'
                FROM action_revisions WHERE action_id = :action_id
                """
            ),
            {
                "challenge_id": uuid.uuid4(),
                "subject_id": ALEX.subject_id,
                "session_id": ALEX.session_id,
                "token_hash": hashlib.sha256(b"challenge-token").hexdigest(),
                "status": ChallengeStatus.ACTIVE.value,
                "action_id": first.action_id,
            },
        )
        session.commit()
    again = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 12)))
    assert again.disposition is ActionCreationDisposition.CREATED
    assert again.action_id != first.action_id
    assert _count(engine, "action_workflows") == 2
    with session_factory() as session:
        old_state = session.execute(
            text("SELECT state FROM action_revisions WHERE action_id = :id"),
            {"id": first.action_id},
        ).scalar_one()
        challenge_status = session.execute(
            text("SELECT status FROM confirmation_challenges WHERE action_id = :id"),
            {"id": first.action_id},
        ).scalar_one()
    assert old_state == WorkflowState.EXPIRED.value
    assert challenge_status == ChallengeStatus.SUPERSEDED.value


def test_expired_confirmed_is_normalized_and_retried(
    service: ActionCreationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    first = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 13)))
    _force_state(session_factory, first.action_id, WorkflowState.CONFIRMED, ttl_expired=True)
    again = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 13)))
    assert again.disposition is ActionCreationDisposition.CREATED
    assert again.action_id != first.action_id
    with session_factory() as session:
        old_state = session.execute(
            text("SELECT state FROM action_revisions WHERE action_id = :id"),
            {"id": first.action_id},
        ).scalar_one()
    assert old_state == WorkflowState.EXPIRED.value


@pytest.mark.parametrize(
    "state",
    [
        WorkflowState.EXECUTING,
        WorkflowState.UNKNOWN_OUTCOME,
        WorkflowState.RECONCILING,
    ],
)
@pytest.mark.skip(reason="retired after simplified execution cutover")
def test_legacy_unresolved_expired_ttl_stays_occupying(
    service: ActionCreationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
    state: WorkflowState,
) -> None:
    first = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 16)))
    _force_state(session_factory, first.action_id, state, ttl_expired=True)
    again = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 16)))
    assert again.disposition is ActionCreationDisposition.RETURNED_IN_FLIGHT
    assert again.action_id == first.action_id
    assert again.state == state.value
    assert _count(engine, "action_workflows") == 1
    with session_factory() as session:
        persisted = session.execute(
            text("SELECT state FROM action_revisions WHERE action_id = :id"),
            {"id": first.action_id},
        ).scalar_one()
    assert persisted == state.value


def test_bounded_contention_returns_retryable_conflict(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    first = ActionCreationService(session_factory, isolated_settings).create_or_reuse(
        ALEX, _draft(start=date(2026, 11, 17))
    )
    service = ActionCreationService(
        session_factory,
        isolated_settings,
        failpoints=ActionCreationFailpoints(force_empty_occupant=True),
    )
    result = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 17)))
    assert result.disposition is ActionCreationDisposition.RETRYABLE_CONFLICT
    assert result.action_id is None
    assert result.ineligibility_reason == "retryable_conflict"
    assert PREPARE_CONTENTION_ATTEMPTS >= 1
    assert _count(engine, "action_workflows") == 1
    assert first.action_id is not None


@pytest.mark.skip(reason="retired after simplified execution cutover")
def test_legacy_reservation_remains_compatible(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    service: ActionCreationService,
) -> None:
    created = service.create_or_reuse(ALEX, _draft(start=date(2026, 11, 18)))
    _force_state(session_factory, created.action_id, WorkflowState.CONFIRMED)
    reserved = ExecutionReservationService(session_factory, isolated_settings).reserve(
        action_id=created.action_id,
        revision=V4_REVISION,
        worker_id="workflow-worker:phase1a",
    )
    assert reserved.outcome is ReservationOutcome.RESERVED
    assert reserved.permit is not None

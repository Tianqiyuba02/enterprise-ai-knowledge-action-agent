import os
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.workflow.authority import AuthoritySnapshot, CanonicalDraft
from app.workflow.calendar import V4_CALENDAR_VERSION
from app.workflow.canonical import business_request_key
from app.workflow.domain import ActionType, ExecutionLedgerStatus, LeaveType, WorkflowState
from app.workflow.errors import ExecutionFenceError
from app.workflow.executable_preparation import (
    READINESS_READY,
    V4ExecutablePreparationService,
    serialize_canonical_draft,
)
from app.workflow.execution import ExecutionReservationService, ReservationOutcome
from app.workflow.executor import (
    BusinessOutcome,
    ExecutorFailpoints,
    LeaveSubmissionExecutor,
)
from app.workflow.finalization import ExecutionFinalizationService, FinalizationFailpoints
from app.workflow.leave_query_repository import LeaveQueryRepository
from app.workflow.time import database_now
from app.workflow.workflow_repository import NewWorkflowRevision, WorkflowRepository

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]

ALEX = DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"]
WORKER_A = "workflow-worker:fence-a"
WORKER_B = "workflow-worker:fence-b"


def _replace_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[KnowledgeSettings]:
    live = load_knowledge_settings()
    admin_url = live.database_url.get_secret_value()
    database_name = f"knowledge_agent_v4_fn_{uuid.uuid4().hex[:12]}"
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


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def _workflow_state(engine: Engine, action_id) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT state FROM action_revisions WHERE action_id = :action_id"),
            {"action_id": action_id},
        ).scalar_one()


def _persist_action(
    session: Session,
    *,
    start: date,
    calendar_version: str | None = None,
    draft: CanonicalDraft | None = None,
) -> uuid.UUID:
    if draft is None:
        prepared = V4ExecutablePreparationService().prepare(
            session,
            context=ALEX,
            start_date=start,
            end_date=start,
            reason="Family visit",
        )
        draft = prepared.draft
        payload = prepared.payload()
        draft_hash = draft.fingerprint()
        authority = prepared.snapshot.fingerprint()
        key = prepared.business_request_key
        ruleset_version = draft.ruleset_version
        stored_calendar = calendar_version or draft.calendar_version
    else:
        payload = serialize_canonical_draft(draft, scheduled_work_days=1)
        draft_hash = draft.fingerprint()
        authority = draft.authority_snapshot_hash
        key = business_request_key(
            employee_id=ALEX.employee_id,
            leave_type=draft.leave_type,
            start_date=draft.start_date,
            end_date=draft.end_date,
        )
        ruleset_version = draft.ruleset_version
        stored_calendar = calendar_version or draft.calendar_version
    now = database_now(session)
    workflow, revision = WorkflowRepository().create_workflow_and_revision(
        session,
        NewWorkflowRevision(
            owner_subject_id=ALEX.subject_id or "",
            owner_employee_id=ALEX.employee_id,
            jurisdiction=ALEX.jurisdiction or "AU-VIC",
            action_type=ActionType.SUBMIT_ANNUAL_LEAVE,
            state=WorkflowState.CONFIRMED,
            draft_payload=payload,
            draft_hash=draft_hash,
            authority_snapshot_hash=authority,
            business_request_key=key,
            ruleset_version=ruleset_version,
            calendar_version=stored_calendar,
            action_expires_at=now + timedelta(hours=1),
        ),
    )
    revision.confirmed_at = now
    revision.confirmed_expires_at = now + timedelta(hours=1)
    return workflow.action_id


def _reserve(
    reservation: ExecutionReservationService,
    session_factory: sessionmaker[Session],
    *,
    start: date,
    worker_id: str = WORKER_A,
):
    with session_factory() as session:
        action_id = _persist_action(session, start=start)
        session.commit()
    result = reservation.reserve(action_id=action_id, revision=1, worker_id=worker_id)
    assert result.outcome is ReservationOutcome.RESERVED
    assert result.permit is not None
    return action_id, result.permit


def _force_reconciling(session_factory: sessionmaker[Session], action_id) -> None:
    with session_factory() as session:
        session.execute(
            text("UPDATE action_revisions SET state = :state WHERE action_id = :action_id"),
            {"state": WorkflowState.RECONCILING.value, "action_id": action_id},
        )
        session.execute(
            text(
                """
                UPDATE action_execution_ledger
                SET status = :status
                WHERE action_id = :action_id
                """
            ),
            {"status": ExecutionLedgerStatus.RECONCILING.value, "action_id": action_id},
        )
        session.commit()


def test_reconciling_submit_performs_zero_mutation(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    executor = LeaveSubmissionExecutor(session_factory, isolated_settings)
    action_id, permit = _reserve(reservation, session_factory, start=date(2026, 9, 21))
    _force_reconciling(session_factory, action_id)
    result = executor.submit(permit)
    assert result.outcome is BusinessOutcome.EXECUTION_AUTHORITY_LOST
    assert result.failure_kind == "revision_not_executable"
    assert _count(engine, "leave_requests") == 0
    assert _workflow_state(engine, action_id) == WorkflowState.RECONCILING.value


def test_unknown_outcome_submit_performs_zero_mutation(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    finalizer = ExecutionFinalizationService(session_factory, isolated_settings)
    executor = LeaveSubmissionExecutor(session_factory, isolated_settings)
    action_id, permit = _reserve(reservation, session_factory, start=date(2026, 9, 22))
    from app.workflow.executor import ExecutorResult

    state = finalizer.finalize(
        permit,
        ExecutorResult(BusinessOutcome.OUTCOME_UNKNOWN, execution_key=permit.execution_key),
    )
    assert state == WorkflowState.UNKNOWN_OUTCOME.value
    result = executor.submit(permit)
    assert result.outcome is BusinessOutcome.EXECUTION_AUTHORITY_LOST
    assert result.failure_kind == "revision_not_executable"
    assert _count(engine, "leave_requests") == 0
    assert _workflow_state(engine, action_id) == WorkflowState.UNKNOWN_OUTCOME.value


def test_stale_caller_cannot_reload_current_owner_permit(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    action_id, permit = _reserve(reservation, session_factory, start=date(2026, 9, 23))
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_execution_ledger
                SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE action_id = :action_id
                """
            ),
            {"action_id": action_id},
        )
        session.commit()
    taken = reservation.takeover_expired_lease(action_id=action_id, revision=1, worker_id=WORKER_B)
    assert taken.lease_owner_id == WORKER_B
    with pytest.raises(ExecutionFenceError, match="does not own"):
        reservation.reload_permit(action_id=action_id, revision=1, worker_id=WORKER_A)
    owned = reservation.reload_permit(action_id=action_id, revision=1, worker_id=WORKER_B)
    assert owned.lease_generation == taken.lease_generation
    already = reservation.reserve(action_id=action_id, revision=1, worker_id=WORKER_A)
    assert already.outcome is ReservationOutcome.ALREADY_RESERVED
    assert already.permit is None


def test_stale_generation_is_authority_loss_not_business_failure(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    executor = LeaveSubmissionExecutor(session_factory, isolated_settings)
    finalizer = ExecutionFinalizationService(session_factory, isolated_settings)
    action_id, stale = _reserve(reservation, session_factory, start=date(2026, 9, 24))
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_execution_ledger
                SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE action_id = :action_id
                """
            ),
            {"action_id": action_id},
        )
        session.commit()
    reservation.takeover_expired_lease(action_id=action_id, revision=1, worker_id=WORKER_B)
    result = executor.submit(stale)
    assert result.outcome is BusinessOutcome.EXECUTION_AUTHORITY_LOST
    assert result.failure_kind == "stale_generation"
    state = finalizer.finalize(stale, result)
    assert state == WorkflowState.EXECUTING.value
    assert _count(engine, "leave_requests") == 0
    assert _workflow_state(engine, action_id) == WorkflowState.EXECUTING.value


def test_expired_lease_is_authority_loss_not_automatic_business_failure(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    executor = LeaveSubmissionExecutor(session_factory, isolated_settings)
    finalizer = ExecutionFinalizationService(session_factory, isolated_settings)
    action_id, permit = _reserve(reservation, session_factory, start=date(2026, 10, 5))
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_execution_ledger
                SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE action_id = :action_id
                """
            ),
            {"action_id": action_id},
        )
        session.commit()
    result = executor.submit(permit)
    assert result.outcome is BusinessOutcome.EXECUTION_AUTHORITY_LOST
    assert result.failure_kind == "lease_expired"
    state = finalizer.finalize(permit, result)
    assert state == WorkflowState.EXECUTING.value
    assert _count(engine, "leave_requests") == 0
    assert _workflow_state(engine, action_id) != WorkflowState.EXECUTION_FAILED.value


def test_unauthorized_caller_cannot_report_applied_from_existing_row(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    executor = LeaveSubmissionExecutor(session_factory, isolated_settings)
    action_id, permit = _reserve(reservation, session_factory, start=date(2026, 10, 6))
    assert executor.submit(permit).outcome is BusinessOutcome.APPLIED
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_execution_ledger
                SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE action_id = :action_id
                """
            ),
            {"action_id": action_id},
        )
        session.commit()
    reservation.takeover_expired_lease(action_id=action_id, revision=1, worker_id=WORKER_B)
    stale = executor.submit(permit)
    assert stale.outcome is BusinessOutcome.EXECUTION_AUTHORITY_LOST
    assert stale.leave_request_id is None
    assert stale.failure_kind == "stale_generation"
    assert _count(engine, "leave_requests") == 1


def test_cross_action_adopted_existing_preserves_source_action_id(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    executor = LeaveSubmissionExecutor(session_factory, isolated_settings)
    first_id, first_permit = _reserve(reservation, session_factory, start=date(2026, 10, 7))
    created = executor.submit(first_permit)
    assert created.resolution == "CREATED"
    with session_factory() as session:
        session.execute(
            text("UPDATE action_revisions SET state = :state WHERE action_id = :action_id"),
            {"action_id": first_id, "state": WorkflowState.SUCCEEDED.value},
        )
        session.commit()
    with session_factory() as session:
        with pytest.raises(IntegrityError):
            _persist_action(session, start=date(2026, 10, 7))
            session.commit()
        session.rollback()
    with session_factory() as session:
        row = LeaveQueryRepository().find_by_execution_key(session, first_permit.execution_key)
        assert row is not None
        assert row.source_action_id == first_id
    assert _count(engine, "leave_requests") == 1


def test_calendar_version_drift_is_not_executable(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    snapshot = AuthoritySnapshot(
        employee_id=ALEX.employee_id,
        jurisdiction="AU-VIC",
        work_days=("monday", "tuesday", "wednesday", "thursday", "friday"),
        hours_per_day=Decimal("7.60"),
        timezone="Australia/Melbourne",
        trusted_base_balance_hours=Decimal("76.00"),
        committed_submitted_hours=Decimal("0.00"),
        effective_available_hours=Decimal("76.00"),
        calendar_version="AU-VIC-2025-v1",
        ruleset_version="v4-annual-leave-1",
    )
    draft = CanonicalDraft(
        action_type=ActionType.SUBMIT_ANNUAL_LEAVE.value,
        leave_type=LeaveType.ANNUAL.value,
        start_date=date(2026, 9, 28),
        end_date=date(2026, 9, 28),
        requested_hours=Decimal("7.60"),
        projected_balance_hours=Decimal("68.40"),
        readiness=READINESS_READY,
        reason="Family visit",
        calendar_version="AU-VIC-2025-v1",
        ruleset_version="v4-annual-leave-1",
        authority_snapshot_hash=snapshot.fingerprint(),
    )
    with session_factory() as session:
        action_id = _persist_action(
            session,
            start=date(2026, 9, 28),
            calendar_version="AU-VIC-2025-v1",
            draft=draft,
        )
        session.commit()
    result = reservation.reserve(action_id=action_id, revision=1, worker_id=WORKER_A)
    assert result.outcome is ReservationOutcome.STALE
    assert result.permit is None
    assert _workflow_state(engine, action_id) == WorkflowState.STALE.value
    assert _count(engine, "leave_requests") == 0
    assert V4_CALENDAR_VERSION != "AU-VIC-2025-v1"


def test_reconciliation_absence_versus_concurrent_submit_cannot_diverge(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    action_id, permit = _reserve(reservation, session_factory, start=date(2026, 9, 29))
    start = threading.Barrier(2)
    outcomes: list[str] = []

    def classify() -> None:
        start.wait()
        state = ExecutionFinalizationService(
            session_factory, isolated_settings
        ).classify_and_finalize(permit, WORKER_A)
        outcomes.append(state)

    def submit() -> None:
        start.wait()
        result = LeaveSubmissionExecutor(session_factory, isolated_settings).submit(permit)
        outcomes.append(result.outcome.value)

    workers = [threading.Thread(target=classify), threading.Thread(target=submit)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    state = _workflow_state(engine, action_id)
    leave_count = _count(engine, "leave_requests")
    assert not (leave_count > 0 and state == WorkflowState.EXECUTION_FAILED.value)
    if leave_count > 0:
        assert state == WorkflowState.SUCCEEDED.value
    if state == WorkflowState.EXECUTION_FAILED.value:
        assert leave_count == 0


def test_reconciling_window_cannot_admit_a_late_insert(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    executor = LeaveSubmissionExecutor(session_factory, isolated_settings)
    action_id, permit = _reserve(reservation, session_factory, start=date(2026, 9, 30))
    _force_reconciling(session_factory, action_id)
    entered = threading.Event()
    submit_result: list[BusinessOutcome] = []
    late_thread: threading.Thread | None = None

    def after_absence() -> None:
        def late_submit() -> None:
            entered.set()
            submit_result.append(executor.submit(permit).outcome)

        nonlocal late_thread
        late_thread = threading.Thread(target=late_submit)
        late_thread.start()
        entered.wait()

    classifying = ExecutionFinalizationService(
        session_factory,
        isolated_settings,
        failpoints=FinalizationFailpoints(after_absence_observed=after_absence),
    )
    state = classifying.classify_and_finalize(permit, WORKER_A)
    assert late_thread is not None
    late_thread.join(timeout=10)
    assert late_thread.is_alive() is False
    assert state == WorkflowState.EXECUTION_FAILED.value
    assert _count(engine, "leave_requests") == 0
    assert submit_result == [BusinessOutcome.EXECUTION_AUTHORITY_LOST]
    assert _workflow_state(engine, action_id) == WorkflowState.EXECUTION_FAILED.value


def test_attack7_reconcile_absence_cannot_finalize_failed_after_concurrent_insert(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    """Historical interleaving: observe absence, concurrent insert, then fail.

    After the Stage 4C fix this committed pair is unreachable:
    leave_request exists AND workflow == EXECUTION_FAILED.
    """

    reservation = ExecutionReservationService(session_factory, isolated_settings)
    action_id, permit = _reserve(reservation, session_factory, start=date(2026, 10, 8))
    _force_reconciling(session_factory, action_id)
    start = threading.Barrier(2)
    finished = threading.Barrier(2)

    def classify() -> None:
        start.wait()
        ExecutionFinalizationService(session_factory, isolated_settings).classify_and_finalize(
            permit, WORKER_A
        )
        finished.wait()

    def delayed_submit() -> None:
        start.wait()
        LeaveSubmissionExecutor(session_factory, isolated_settings).submit(permit)
        finished.wait()

    workers = [threading.Thread(target=classify), threading.Thread(target=delayed_submit)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    state = _workflow_state(engine, action_id)
    leave_count = _count(engine, "leave_requests")
    assert not (leave_count > 0 and state == WorkflowState.EXECUTION_FAILED.value)
    if leave_count > 0:
        assert state == WorkflowState.SUCCEEDED.value
    if state == WorkflowState.EXECUTION_FAILED.value:
        assert leave_count == 0


def test_attack7_submit_wins_first_resolves_succeeded(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    action_id, permit = _reserve(reservation, session_factory, start=date(2026, 10, 9))
    classify_state: list[str] = []
    classify_thread: threading.Thread | None = None

    def hold_after_insert() -> None:
        def classify() -> None:
            classify_state.append(
                ExecutionFinalizationService(
                    session_factory, isolated_settings
                ).classify_and_finalize(permit, WORKER_A)
            )

        nonlocal classify_thread
        classify_thread = threading.Thread(target=classify)
        classify_thread.start()
        _wait_until_backend_waiting_for_lock(engine)

    submitted = LeaveSubmissionExecutor(
        session_factory,
        isolated_settings,
        failpoints=ExecutorFailpoints(hold_after_insert_before_commit=hold_after_insert),
    ).submit(permit)
    assert submitted.outcome is BusinessOutcome.APPLIED
    assert classify_thread is not None
    classify_thread.join(timeout=10)
    assert classify_thread.is_alive() is False
    assert classify_state == [WorkflowState.SUCCEEDED.value]
    assert _count(engine, "leave_requests") == 1
    assert _workflow_state(engine, action_id) == WorkflowState.SUCCEEDED.value


def _wait_until_backend_waiting_for_lock(engine: Engine) -> None:
    deadline = time.monotonic() + 5.0
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
            return
        time.sleep(0.01)
    raise AssertionError("classify never blocked on submit's uncommitted row locks")

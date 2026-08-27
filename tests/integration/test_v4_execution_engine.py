import os
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.identity import AuthenticatedEmployeeContext
from app.workflow.authority import AuthoritySnapshot, CanonicalDraft
from app.workflow.calendar import V4_CALENDAR_VERSION
from app.workflow.canonical import business_request_key
from app.workflow.domain import (
    ActionType,
    ExecutionLedgerStatus,
    LeaveType,
    WorkflowState,
)
from app.workflow.errors import WorkflowIntegrityError
from app.workflow.executable_preparation import (
    READINESS_READY,
    V4ExecutablePreparationService,
    serialize_canonical_draft,
)
from app.workflow.execution import ExecutionPermit, ExecutionReservationService, ReservationOutcome
from app.workflow.executor import (
    BusinessOutcome,
    ExecutorFailpoints,
    LeaveSubmissionExecutor,
)
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
WORKER_A = "workflow-worker:a"
WORKER_B = "workflow-worker:b"


def _replace_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[KnowledgeSettings]:
    live = load_knowledge_settings()
    admin_url = live.database_url.get_secret_value()
    database_name = f"knowledge_agent_v4_ex_{uuid.uuid4().hex[:12]}"
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
def reservation(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> ExecutionReservationService:
    return ExecutionReservationService(session_factory, isolated_settings)


@pytest.fixture
def executor(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> LeaveSubmissionExecutor:
    return LeaveSubmissionExecutor(session_factory, isolated_settings)


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def _snapshot(*, employee_id: str = "EMP-1001") -> AuthoritySnapshot:
    return AuthoritySnapshot(
        employee_id=employee_id,
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


def _persist_action(
    session: Session,
    *,
    context: AuthenticatedEmployeeContext = ALEX,
    start: date = date(2026, 9, 1),
    end: date | None = None,
    state: WorkflowState = WorkflowState.CONFIRMED,
    confirmed_expires_at: datetime | None = None,
    draft: CanonicalDraft | None = None,
    payload: dict | None = None,
    draft_hash: str | None = None,
    authority_snapshot_hash: str | None = None,
) -> UUID:
    end = end or start
    if draft is None:
        prepared = V4ExecutablePreparationService().prepare(
            session,
            context=context,
            start_date=start,
            end_date=end,
            reason="Family visit",
        )
        draft = prepared.draft
        stored_payload = prepared.payload()
        stored_hash = draft.fingerprint()
        stored_authority = prepared.snapshot.fingerprint()
        key = prepared.business_request_key
        calendar_version = draft.calendar_version
        ruleset_version = draft.ruleset_version
    else:
        stored_payload = payload or serialize_canonical_draft(draft, scheduled_work_days=1)
        stored_hash = draft_hash or draft.fingerprint()
        stored_authority = authority_snapshot_hash or draft.authority_snapshot_hash
        key = business_request_key(
            employee_id=context.employee_id,
            leave_type=LeaveType.ANNUAL.value,
            start_date=start,
            end_date=end,
        )
        calendar_version = draft.calendar_version
        ruleset_version = draft.ruleset_version
    now = database_now(session)
    workflow, revision = WorkflowRepository().create_workflow_and_revision(
        session,
        NewWorkflowRevision(
            owner_subject_id=context.subject_id or "",
            owner_employee_id=context.employee_id,
            jurisdiction=context.jurisdiction or "AU-VIC",
            action_type=ActionType.SUBMIT_ANNUAL_LEAVE,
            state=state,
            draft_payload=stored_payload,
            draft_hash=stored_hash,
            authority_snapshot_hash=stored_authority,
            business_request_key=key,
            ruleset_version=ruleset_version,
            calendar_version=calendar_version,
            action_expires_at=now + timedelta(hours=1),
        ),
    )
    if state is WorkflowState.CONFIRMED:
        revision.confirmed_at = now
        revision.confirmed_expires_at = confirmed_expires_at or now + timedelta(hours=1)
    return workflow.action_id


def test_confirmed_reserves_exactly_one_execution(
    reservation: ExecutionReservationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    with session_factory() as session:
        action_id = _persist_action(session)
        session.commit()
    first = reservation.reserve(action_id=action_id, revision=1, worker_id=WORKER_A)
    second = reservation.reserve(action_id=action_id, revision=1, worker_id=WORKER_B)
    assert first.outcome is ReservationOutcome.RESERVED
    assert first.permit is not None
    assert second.outcome is ReservationOutcome.ALREADY_RESERVED
    assert second.permit is not None
    assert first.permit.execution_key == second.permit.execution_key
    assert first.permit.lease_generation == 1
    assert _count(engine, "action_execution_ledger") == 1
    with session_factory() as session:
        state = session.execute(
            text("SELECT state FROM action_revisions WHERE action_id = :action_id"),
            {"action_id": action_id},
        ).scalar_one()
    assert state == WorkflowState.EXECUTING.value


def test_expired_confirmed_does_not_reserve(
    reservation: ExecutionReservationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    with session_factory() as session:
        action_id = _persist_action(
            session,
            confirmed_expires_at=datetime.now(UTC) - timedelta(seconds=5),
        )
        session.commit()
    result = reservation.reserve(action_id=action_id, revision=1, worker_id=WORKER_A)
    assert result.outcome is ReservationOutcome.EXPIRED
    assert result.permit is None
    assert _count(engine, "action_execution_ledger") == 0


def test_stale_revalidation_and_integrity_errors(
    reservation: ExecutionReservationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    snapshot = _snapshot()
    stale_draft = CanonicalDraft(
        action_type=ActionType.SUBMIT_ANNUAL_LEAVE.value,
        leave_type=LeaveType.ANNUAL.value,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
        requested_hours=Decimal("15.20"),
        projected_balance_hours=Decimal("60.80"),
        readiness=READINESS_READY,
        reason="Family visit",
        calendar_version=V4_CALENDAR_VERSION,
        ruleset_version="v4-annual-leave-1",
        authority_snapshot_hash=snapshot.fingerprint(),
    )
    with session_factory() as session:
        stale_id = _persist_action(session, start=date(2026, 9, 1), draft=stale_draft)
        broken_id = _persist_action(
            session,
            start=date(2026, 9, 2),
            draft=stale_draft,
            payload={"leave_type": "annual", "reason": "Family visit"},
            draft_hash=stale_draft.fingerprint(),
            authority_snapshot_hash=snapshot.fingerprint(),
        )
        session.commit()
    stale = reservation.reserve(action_id=stale_id, revision=1, worker_id=WORKER_A)
    assert stale.outcome is ReservationOutcome.STALE
    assert _count(engine, "action_execution_ledger") == 0
    with pytest.raises(WorkflowIntegrityError):
        reservation.reserve(action_id=broken_id, revision=1, worker_id=WORKER_A)
    assert _count(engine, "action_execution_ledger") == 0
    assert _count(engine, "leave_requests") == 0


def test_unresolved_business_key_blocks_second_reservation(
    reservation: ExecutionReservationService,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        first_id = _persist_action(session, start=date(2026, 9, 1))
        second_id = _persist_action(session, start=date(2026, 9, 1))
        session.commit()
    first = reservation.reserve(action_id=first_id, revision=1, worker_id=WORKER_A)
    second = reservation.reserve(action_id=second_id, revision=1, worker_id=WORKER_B)
    assert first.outcome is ReservationOutcome.RESERVED
    assert second.outcome is ReservationOutcome.BLOCKED_UNRESOLVED
    assert second.retryable is True
    assert second.permit is None


def test_concurrent_same_business_key_has_one_winner(
    reservation: ExecutionReservationService,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        first_id = _persist_action(session, start=date(2026, 9, 8))
        second_id = _persist_action(session, start=date(2026, 9, 8))
        session.commit()
    barrier = threading.Barrier(2)
    outcomes: list[ReservationOutcome] = []

    def reserve(action_id: UUID, worker_id: str) -> None:
        barrier.wait()
        result = reservation.reserve(action_id=action_id, revision=1, worker_id=worker_id)
        outcomes.append(result.outcome)

    workers = [
        threading.Thread(target=reserve, args=(first_id, WORKER_A)),
        threading.Thread(target=reserve, args=(second_id, WORKER_B)),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert outcomes.count(ReservationOutcome.RESERVED) == 1
    assert outcomes.count(ReservationOutcome.BLOCKED_UNRESOLVED) == 1


def test_stale_generation_performs_zero_mutation(
    reservation: ExecutionReservationService,
    executor: LeaveSubmissionExecutor,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    with session_factory() as session:
        action_id = _persist_action(session, start=date(2026, 9, 3))
        session.commit()
    reserved = reservation.reserve(action_id=action_id, revision=1, worker_id=WORKER_A)
    assert reserved.permit is not None
    stale_permit = reserved.permit
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
    assert taken.lease_generation == 2
    assert taken.lease_owner_id == WORKER_B
    assert taken.execution_key == stale_permit.execution_key
    blocked = executor.submit(stale_permit)
    assert blocked.outcome is BusinessOutcome.DEFINITELY_NOT_APPLIED
    assert blocked.failure_kind == "stale_generation"
    assert _count(engine, "leave_requests") == 0
    applied = executor.submit(taken)
    assert applied.outcome is BusinessOutcome.APPLIED
    assert _count(engine, "leave_requests") == 1


def test_execution_key_replay_and_cross_action_dedupe(
    reservation: ExecutionReservationService,
    executor: LeaveSubmissionExecutor,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    with session_factory() as session:
        first_id = _persist_action(session, start=date(2026, 9, 4))
        session.commit()
    first = reservation.reserve(action_id=first_id, revision=1, worker_id=WORKER_A)
    assert first.permit is not None
    first_result = executor.submit(first.permit)
    replay = executor.submit(first.permit)
    assert first_result.outcome is replay.outcome is BusinessOutcome.APPLIED
    assert first_result.leave_request_id == replay.leave_request_id
    assert _count(engine, "leave_requests") == 1
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_revisions
                SET state = :state
                WHERE action_id = :action_id
                """
            ),
            {"action_id": first_id, "state": WorkflowState.SUCCEEDED.value},
        )
        second_id = _persist_action(session, start=date(2026, 9, 4))
        session.commit()
    second = reservation.reserve(action_id=second_id, revision=1, worker_id=WORKER_B)
    assert second.permit is not None
    adopted = executor.submit(second.permit)
    assert adopted.outcome is BusinessOutcome.APPLIED
    assert adopted.leave_request_id == first_result.leave_request_id
    assert _count(engine, "leave_requests") == 1


def test_overlap_and_insufficient_balance_are_definite_failures(
    reservation: ExecutionReservationService,
    executor: LeaveSubmissionExecutor,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    with session_factory() as session:
        first_id = _persist_action(session, start=date(2026, 9, 1), end=date(2026, 9, 3))
        overlap_id = _persist_action(session, start=date(2026, 9, 2), end=date(2026, 9, 4))
        expensive_id = _persist_action(session, start=date(2026, 10, 5), end=date(2026, 10, 16))
        session.commit()
        first = reservation.reserve(action_id=first_id, revision=1, worker_id=WORKER_A)
        overlap = reservation.reserve(action_id=overlap_id, revision=1, worker_id=WORKER_A)
        expensive = reservation.reserve(action_id=expensive_id, revision=1, worker_id=WORKER_A)
        assert first.permit is not None
        assert overlap.permit is not None
        assert expensive.permit is not None
        assert executor.submit(first.permit).outcome is BusinessOutcome.APPLIED
        overlap_result = executor.submit(overlap.permit)
        assert overlap_result.outcome is BusinessOutcome.DEFINITELY_NOT_APPLIED
        assert overlap_result.failure_kind == "overlap"
        expensive_result = executor.submit(expensive.permit)
    assert expensive_result.outcome is BusinessOutcome.DEFINITELY_NOT_APPLIED
    assert expensive_result.failure_kind == "insufficient_balance"
    assert _count(engine, "leave_requests") == 1


def test_concurrent_balance_and_overlap_serialize(
    reservation: ExecutionReservationService,
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    with session_factory() as session:
        first_id = _persist_action(session, start=date(2026, 9, 1), end=date(2026, 9, 8))
        second_id = _persist_action(session, start=date(2026, 10, 5), end=date(2026, 10, 12))
        overlap_a = _persist_action(session, start=date(2026, 11, 9), end=date(2026, 11, 11))
        overlap_b = _persist_action(session, start=date(2026, 11, 10), end=date(2026, 11, 12))
        session.commit()
    first = reservation.reserve(action_id=first_id, revision=1, worker_id=WORKER_A)
    second = reservation.reserve(action_id=second_id, revision=1, worker_id=WORKER_B)
    overlap_first = reservation.reserve(action_id=overlap_a, revision=1, worker_id=WORKER_A)
    overlap_second = reservation.reserve(action_id=overlap_b, revision=1, worker_id=WORKER_B)
    assert first.permit is not None
    assert second.permit is not None
    assert overlap_first.permit is not None
    assert overlap_second.permit is not None

    def run_pair(left: ExecutionPermit, right: ExecutionPermit) -> list[BusinessOutcome]:
        barrier = threading.Barrier(2)
        outcomes: list[BusinessOutcome] = []

        def submit(permit: ExecutionPermit) -> None:
            barrier.wait()
            result = LeaveSubmissionExecutor(session_factory, isolated_settings).submit(permit)
            outcomes.append(result.outcome)

        workers = [
            threading.Thread(target=submit, args=(left,)),
            threading.Thread(target=submit, args=(right,)),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        return outcomes

    balance_outcomes = run_pair(first.permit, second.permit)
    assert balance_outcomes.count(BusinessOutcome.APPLIED) == 1
    assert balance_outcomes.count(BusinessOutcome.DEFINITELY_NOT_APPLIED) == 1
    overlap_outcomes = run_pair(overlap_first.permit, overlap_second.permit)
    assert overlap_outcomes.count(BusinessOutcome.APPLIED) <= 1
    assert _count(engine, "leave_requests") == 1 + overlap_outcomes.count(BusinessOutcome.APPLIED)


def test_holiday_and_coverage_cannot_execute(
    reservation: ExecutionReservationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    snapshot = _snapshot()
    cup = CanonicalDraft(
        action_type=ActionType.SUBMIT_ANNUAL_LEAVE.value,
        leave_type=LeaveType.ANNUAL.value,
        start_date=date(2026, 11, 3),
        end_date=date(2026, 11, 3),
        requested_hours=Decimal("7.60"),
        projected_balance_hours=Decimal("68.40"),
        readiness=READINESS_READY,
        reason="Cup day",
        calendar_version=V4_CALENDAR_VERSION,
        ruleset_version="v4-annual-leave-1",
        authority_snapshot_hash=snapshot.fingerprint(),
    )
    future = CanonicalDraft(
        action_type=ActionType.SUBMIT_ANNUAL_LEAVE.value,
        leave_type=LeaveType.ANNUAL.value,
        start_date=date(2027, 1, 4),
        end_date=date(2027, 1, 4),
        requested_hours=Decimal("7.60"),
        projected_balance_hours=Decimal("68.40"),
        readiness=READINESS_READY,
        reason="Next year",
        calendar_version=V4_CALENDAR_VERSION,
        ruleset_version="v4-annual-leave-1",
        authority_snapshot_hash=snapshot.fingerprint(),
    )
    with session_factory() as session:
        cup_id = _persist_action(session, start=date(2026, 11, 3), draft=cup)
        future_id = _persist_action(session, start=date(2027, 1, 4), draft=future)
        session.commit()
    assert reservation.reserve(action_id=cup_id, revision=1, worker_id=WORKER_A).outcome is (
        ReservationOutcome.STALE
    )
    assert reservation.reserve(action_id=future_id, revision=1, worker_id=WORKER_A).outcome is (
        ReservationOutcome.STALE
    )
    assert _count(engine, "action_execution_ledger") == 0
    assert _count(engine, "leave_requests") == 0


def test_reconcile_discovers_applied_and_cross_action_satisfaction(
    reservation: ExecutionReservationService,
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    with session_factory() as session:
        first_id = _persist_action(session, start=date(2026, 9, 9))
        session.commit()
    unknown_executor = LeaveSubmissionExecutor(
        session_factory,
        isolated_settings,
        failpoints=ExecutorFailpoints(report_unknown_after_commit=True),
    )
    reserved = reservation.reserve(action_id=first_id, revision=1, worker_id=WORKER_A)
    assert reserved.permit is not None
    unknown = unknown_executor.submit(reserved.permit)
    assert unknown.outcome is BusinessOutcome.OUTCOME_UNKNOWN
    assert _count(engine, "leave_requests") == 1
    found = LeaveSubmissionExecutor(session_factory, isolated_settings).reconcile(reserved.permit)
    assert found.outcome is BusinessOutcome.APPLIED
    with session_factory() as session:
        session.execute(
            text("UPDATE action_revisions SET state = :state WHERE action_id = :action_id"),
            {"action_id": first_id, "state": WorkflowState.SUCCEEDED.value},
        )
        second_id = _persist_action(session, start=date(2026, 9, 9))
        session.commit()
    second = reservation.reserve(action_id=second_id, revision=1, worker_id=WORKER_B)
    assert second.permit is not None
    adopted = LeaveSubmissionExecutor(session_factory, isolated_settings).reconcile(second.permit)
    assert adopted.outcome is BusinessOutcome.APPLIED
    assert adopted.leave_request_id == found.leave_request_id
    assert _count(engine, "leave_requests") == 1


def test_ambiguous_outcome_is_not_labeled_failure(
    reservation: ExecutionReservationService,
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        action_id = _persist_action(session, start=date(2026, 9, 10))
        session.commit()
    reserved = reservation.reserve(action_id=action_id, revision=1, worker_id=WORKER_A)
    assert reserved.permit is not None
    result = LeaveSubmissionExecutor(
        session_factory,
        isolated_settings,
        failpoints=ExecutorFailpoints(raise_after_commit=RuntimeError("lost connection")),
    ).submit(reserved.permit)
    assert result.outcome is BusinessOutcome.OUTCOME_UNKNOWN
    assert result.failure_kind is None


def test_same_generation_owner_can_mutate_and_key_is_immutable(
    reservation: ExecutionReservationService,
    executor: LeaveSubmissionExecutor,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        action_id = _persist_action(session, start=date(2026, 9, 11))
        session.commit()
    reserved = reservation.reserve(action_id=action_id, revision=1, worker_id=WORKER_A)
    again = reservation.reserve(action_id=action_id, revision=1, worker_id=WORKER_A)
    assert reserved.permit is not None
    assert again.permit is not None
    assert reserved.permit.execution_key == again.permit.execution_key
    assert reserved.permit.lease_generation == again.permit.lease_generation == 1
    assert executor.submit(reserved.permit).outcome is BusinessOutcome.APPLIED
    with session_factory() as session:
        row = LeaveQueryRepository().find_by_execution_key(session, reserved.permit.execution_key)
        assert row is not None
        assert row.status == "submitted"
        assert row.source_action_id == action_id
        assert row.requested_hours == Decimal("7.60")
        assert (
            session.execute(
                text("SELECT status FROM action_execution_ledger WHERE action_id = :action_id"),
                {"action_id": action_id},
            ).scalar_one()
            == ExecutionLedgerStatus.RESERVED.value
        )

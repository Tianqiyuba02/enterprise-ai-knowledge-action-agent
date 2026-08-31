import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import create_knowledge_engine
from app.db.workflow_models import ActionRevision, ActionWorkflow, LeaveRequest
from app.workflow.audit_repository import AuditRepository, NewAuditEvent
from app.workflow.authority import AuthoritySnapshot, CanonicalDraft
from app.workflow.calendar import V4_CALENDAR_VERSION
from app.workflow.calendar_service import CalendarCoverage, TrustedHolidayCalendarService
from app.workflow.canonical import business_request_key
from app.workflow.challenge_repository import ChallengeRepository, NewConfirmationChallenge
from app.workflow.domain import (
    ActionType,
    ActorType,
    ExecutionLedgerStatus,
    LeaveRequestStatus,
    LeaveType,
    OutboxEventType,
    WorkflowState,
)
from app.workflow.errors import DuplicateExecutionReservationError, DuplicateWorkflowEventError
from app.workflow.execution_repository import ExecutionLedgerRepository, NewExecutionReservation
from app.workflow.holiday_repository import HolidayCalendarRepository
from app.workflow.leave_query_repository import LeaveQueryRepository
from app.workflow.outbox_repository import NewOutboxEvent, OutboxRepository
from app.workflow.workflow_repository import NewWorkflowRevision, WorkflowRepository

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]


@pytest.fixture(scope="session")
def additive_engine() -> Iterator[Engine]:
    command.upgrade(AlembicConfig("alembic.ini"), "head")
    engine = create_knowledge_engine()
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session(additive_engine: Engine) -> Iterator[Session]:
    connection = additive_engine.connect()
    transaction = connection.begin()
    bound_session = sessionmaker(bind=connection, expire_on_commit=False)()
    try:
        yield bound_session
    finally:
        bound_session.close()
        transaction.rollback()
        connection.close()


def _create_action(
    session: Session,
    *,
    start: date = date(2026, 9, 1),
) -> tuple[ActionWorkflow, ActionRevision]:
    snapshot = AuthoritySnapshot(
        employee_id="EMP-1001",
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
    return WorkflowRepository().create_workflow_and_revision(
        session,
        NewWorkflowRevision(
            owner_subject_id="subj_9f2c4e81a6b047d3",
            owner_employee_id="EMP-1001",
            jurisdiction="AU-VIC",
            action_type=ActionType.SUBMIT_ANNUAL_LEAVE,
            state=WorkflowState.AWAITING_CONFIRMATION,
            draft_payload={"leave_type": "annual", "reason": "Family visit"},
            draft_hash=draft.fingerprint(),
            authority_snapshot_hash=snapshot.fingerprint(),
            business_request_key=business_request_key(
                employee_id="EMP-1001",
                leave_type="annual",
                start_date=start,
                end_date=start,
            ),
            ruleset_version="v4-annual-leave-1",
            calendar_version=V4_CALENDAR_VERSION,
            action_expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )


def test_workflow_create_load_owner_scope_and_lock(session: Session) -> None:
    repository = WorkflowRepository()
    workflow, revision = _create_action(session)

    loaded = repository.get_revision(session, workflow.action_id)
    owner = repository.get_workflow_for_owner(
        session,
        action_id=workflow.action_id,
        owner_subject_id=workflow.owner_subject_id,
    )
    stranger = repository.get_workflow_for_owner(
        session,
        action_id=workflow.action_id,
        owner_subject_id="subj_other",
    )
    locked = repository.lock_revision(session, action_id=workflow.action_id)

    assert loaded is not None
    assert loaded.revision == 1
    assert loaded.state == WorkflowState.AWAITING_CONFIRMATION.value
    assert owner is not None
    assert stranger is None
    assert locked.revision_id == revision.revision_id


def test_holiday_repository_and_calendar_service_use_seed(session: Session) -> None:
    repository = HolidayCalendarRepository()
    service = TrustedHolidayCalendarService(repository)

    holidays = repository.list_holidays(
        session,
        jurisdiction="AU-VIC",
        start_date=date(2026, 12, 25),
        end_date=date(2026, 12, 28),
    )
    covered = service.holidays_for_range(
        session,
        jurisdiction="AU-VIC",
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
    )
    unresolved = service.holidays_for_range(
        session,
        jurisdiction="AU-VIC",
        start_date=date(2027, 1, 1),
        end_date=date(2027, 1, 1),
    )

    assert [row.holiday_name for row in holidays] == [
        "Christmas Day",
        "Boxing Day",
        "Additional public holiday for Boxing Day",
    ]
    assert covered.coverage is CalendarCoverage.COVERED
    assert covered.holidays == ()
    assert unresolved.coverage is CalendarCoverage.UNRESOLVED
    assert repository.calendar_version() == V4_CALENDAR_VERSION


def test_outbox_duplicate_identity_and_claim_lock(session: Session) -> None:
    workflow, _revision = _create_action(session)
    repository = OutboxRepository()
    event = NewOutboxEvent(
        event_key=f"confirmation_committed:{workflow.action_id}:1",
        action_id=workflow.action_id,
        event_type=OutboxEventType.CONFIRMATION_COMMITTED,
        available_at=datetime.now(UTC),
    )
    first = repository.enqueue(session, event)
    with pytest.raises(DuplicateWorkflowEventError):
        repository.enqueue(session, event)

    claimed = repository.claim_ready(
        session,
        now=datetime.now(UTC),
        locked_by="worker-1",
        lock_for=timedelta(minutes=1),
    )
    assert [row.event_id for row in claimed] == [first.event_id]
    assert claimed[0].locked_by == "worker-1"
    repository.release(session, first.event_id, failure_kind="test")
    repository.mark_delivered(session, first.event_id, delivered_at=datetime.now(UTC))
    assert repository.get_by_event_key(session, event.event_key).delivered_at is not None


def test_execution_ledger_duplicate_and_stale_generation(session: Session) -> None:
    workflow, _revision = _create_action(session, start=date(2026, 9, 1))
    other_workflow, _other_revision = _create_action(session, start=date(2026, 9, 2))
    repository = ExecutionLedgerRepository()
    first = repository.create_reservation(
        session,
        NewExecutionReservation(
            action_id=workflow.action_id,
            execution_key=f"exec-{workflow.action_id}",
            lease_generation=2,
            lease_owner_id="worker-a",
            status=ExecutionLedgerStatus.RESERVED,
        ),
    )
    nested = session.begin_nested()
    with pytest.raises(DuplicateExecutionReservationError):
        repository.create_reservation(
            session,
            NewExecutionReservation(
                action_id=workflow.action_id,
                execution_key=f"exec-other-{uuid4()}",
            ),
        )
    nested.rollback()
    nested = session.begin_nested()
    with pytest.raises(DuplicateExecutionReservationError):
        repository.create_reservation(
            session,
            NewExecutionReservation(
                action_id=other_workflow.action_id,
                execution_key=first.execution_key,
            ),
        )
    nested.rollback()

    locked = repository.lock_reservation(session, action_id=workflow.action_id)
    assert locked.execution_id == first.execution_id
    assert repository.is_stale_generation(locked.lease_generation, 1) is True
    assert repository.is_stale_generation(locked.lease_generation, 2) is False


def test_audit_insert_and_leave_query_without_business_mutation(session: Session) -> None:
    workflow, _revision = _create_action(session)
    AuditRepository().insert(
        session,
        NewAuditEvent(
            action_id=workflow.action_id,
            event_type="created",
            actor_type=ActorType.SYSTEM,
            to_state=WorkflowState.AWAITING_CONFIRMATION.value,
            safe_metadata={"calendar_version": V4_CALENDAR_VERSION},
        ),
    )
    ChallengeRepository().persist(
        session,
        NewConfirmationChallenge(
            action_id=workflow.action_id,
            owner_subject_id=workflow.owner_subject_id,
            confirmation_session_id=workflow.owner_subject_id,
            draft_hash=workflow.action_id.hex + "a" * 32,
            token_hash="b" * 64,
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        ),
    )

    leave_count_before = session.execute(
        select(func.count()).select_from(LeaveRequest)
    ).scalar_one()
    session.add(
        LeaveRequest(
            leave_request_id=uuid4(),
            employee_id="EMP-1001",
            leave_type=LeaveType.ANNUAL.value,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 2),
            requested_hours=Decimal("15.20"),
            reason="Family visit",
            status=LeaveRequestStatus.SUBMITTED.value,
            submitted_at=datetime.now(UTC),
            execution_key=f"leave-exec-{workflow.action_id}",
            business_request_key=f"leave-business-{workflow.action_id}",
            source_action_id=workflow.action_id,
            source_action_revision=1,
            calendar_version=V4_CALENDAR_VERSION,
            ruleset_version="v4-annual-leave-1",
        )
    )
    session.flush()

    queries = LeaveQueryRepository()
    found = queries.find_by_execution_key(session, f"leave-exec-{workflow.action_id}")
    overlapping = queries.overlapping_active_annual_leave(
        session,
        employee_id="EMP-1001",
        start_date=date(2026, 9, 2),
        end_date=date(2026, 9, 3),
    )
    available = queries.effective_available_annual_leave(
        session,
        employee_id="EMP-1001",
        trusted_base_balance_hours=Decimal("76.00"),
    )
    leave_count_after_queries = session.execute(
        select(func.count()).select_from(LeaveRequest)
    ).scalar_one()

    assert found is not None
    assert (
        queries.find_by_business_request_key(
            session,
            f"leave-business-{workflow.action_id}",
        )
        is found
    )
    assert len(overlapping) == 1
    assert available == Decimal("60.80")
    assert leave_count_after_queries == leave_count_before + 1
    assert (
        ChallengeRepository().get_active_challenge(
            session,
            action_id=workflow.action_id,
        )
        is not None
    )

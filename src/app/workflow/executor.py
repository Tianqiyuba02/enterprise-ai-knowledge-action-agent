"""Same-PostgreSQL demo leave submission executor. No HR provider."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.workflow_models import ActionExecutionLedger, LeaveRequest
from app.identity import AuthenticatedEmployeeContext
from app.workflow.calendar_service import CalendarCoverage, TrustedHolidayCalendarService
from app.workflow.canonical import quantize_hours
from app.workflow.domain import ExecutionLedgerStatus, LeaveType, WorkflowState
from app.workflow.executable_preparation import (
    READINESS_NOT_EXECUTABLE,
    V4ExecutablePreparationService,
    verify_persisted_draft_integrity,
)
from app.workflow.execution import ExecutionPermit
from app.workflow.execution_repository import ExecutionLedgerRepository
from app.workflow.holiday_repository import HolidayCalendarRepository
from app.workflow.leave_command_repository import LeaveCommandRepository, NewLeaveRequest
from app.workflow.leave_query_repository import LeaveQueryRepository
from app.workflow.locks import acquire_business_request_lock, acquire_employee_lock
from app.workflow.time import database_now
from app.workflow.workflow_repository import WorkflowRepository

SUBMIT_MUTATION_STATES = frozenset({WorkflowState.EXECUTING.value})
RECONCILE_PROBE_STATES = frozenset(
    {
        WorkflowState.EXECUTING.value,
        WorkflowState.UNKNOWN_OUTCOME.value,
        WorkflowState.RECONCILING.value,
    }
)
SUBMIT_LEDGER_STATUSES = frozenset(
    {
        ExecutionLedgerStatus.RESERVED.value,
        ExecutionLedgerStatus.LEASED.value,
    }
)
RECONCILE_LEDGER_STATUSES = frozenset(
    {
        ExecutionLedgerStatus.RESERVED.value,
        ExecutionLedgerStatus.LEASED.value,
        ExecutionLedgerStatus.UNKNOWN.value,
        ExecutionLedgerStatus.RECONCILING.value,
    }
)


class BusinessOutcome(StrEnum):
    APPLIED = "APPLIED"
    DEFINITELY_NOT_APPLIED = "DEFINITELY_NOT_APPLIED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    EXECUTION_AUTHORITY_LOST = "EXECUTION_AUTHORITY_LOST"


class ResolutionKind(StrEnum):
    CREATED = "CREATED"
    ADOPTED_EXISTING = "ADOPTED_EXISTING"


@dataclass(frozen=True, slots=True)
class ExecutorResult:
    outcome: BusinessOutcome
    leave_request_id: UUID | None = None
    failure_kind: str | None = None
    execution_key: str | None = None
    business_request_key: str | None = None
    resolution: str | None = None

    @property
    def applied(self) -> bool:
        return self.outcome is BusinessOutcome.APPLIED

    @property
    def authority_lost(self) -> bool:
        return self.outcome is BusinessOutcome.EXECUTION_AUTHORITY_LOST


@dataclass
class ExecutorFailpoints:
    """Test-only hooks. Do not wire to HTTP, query params, or runtime env flags."""

    raise_before_insert: BaseException | None = None
    raise_after_insert_before_commit: BaseException | None = None
    raise_after_commit: BaseException | None = None
    report_unknown_after_commit: bool = False
    after_absence_observed: Callable[[], None] | None = None


class LeaveSubmissionExecutor:
    """Idempotent annual-leave submit against the same PostgreSQL database."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: KnowledgeSettings | None = None,
        *,
        failpoints: ExecutorFailpoints | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or load_knowledge_settings()
        self._failpoints = failpoints
        self._workflows = WorkflowRepository()
        self._ledger = ExecutionLedgerRepository()
        self._leave_queries = LeaveQueryRepository()
        self._leave_commands = LeaveCommandRepository()
        self._preparation = V4ExecutablePreparationService()
        self._calendar = TrustedHolidayCalendarService(HolidayCalendarRepository())

    def submit(self, permit: ExecutionPermit) -> ExecutorResult:
        return self._run(permit, conclude_absence=False)

    def reconcile(self, permit: ExecutionPermit) -> ExecutorResult:
        """Probe-only. Never inserts a leave request."""

        return self._run(permit, conclude_absence=True)

    def _run(self, permit: ExecutionPermit, *, conclude_absence: bool) -> ExecutorResult:
        try:
            with self._session_factory() as session:
                return self._run_in_session(session, permit, conclude_absence=conclude_absence)
        except _AmbiguousOutcome:
            return ExecutorResult(
                BusinessOutcome.OUTCOME_UNKNOWN,
                execution_key=permit.execution_key,
            )
        except SQLAlchemyError:
            return ExecutorResult(
                BusinessOutcome.OUTCOME_UNKNOWN,
                execution_key=permit.execution_key,
            )

    def _run_in_session(
        self,
        session: Session,
        permit: ExecutionPermit,
        *,
        conclude_absence: bool,
    ) -> ExecutorResult:
        workflow = self._workflows.lock_workflow(session, permit.action_id)
        revision = self._workflows.lock_revision(
            session, action_id=permit.action_id, revision=permit.revision
        )
        ledger = self._ledger.lock_reservation(
            session, action_id=permit.action_id, revision=permit.revision
        )
        now = database_now(session)
        allowed_states = RECONCILE_PROBE_STATES if conclude_absence else SUBMIT_MUTATION_STATES
        allowed_ledger = RECONCILE_LEDGER_STATUSES if conclude_absence else SUBMIT_LEDGER_STATUSES
        fence = self._evaluate_fence(
            ledger,
            revision,
            permit,
            now,
            allowed_states=allowed_states,
            allowed_ledger_statuses=allowed_ledger,
        )
        if fence is not None:
            session.commit()
            return fence
        acquire_business_request_lock(session, revision.business_request_key)
        if not conclude_absence:
            acquire_employee_lock(session, workflow.owner_employee_id)
        now = database_now(session)
        fence = self._evaluate_fence(
            ledger,
            revision,
            permit,
            now,
            allowed_states=allowed_states,
            allowed_ledger_statuses=allowed_ledger,
        )
        if fence is not None:
            session.commit()
            return fence
        existing = self._find_existing_leave(
            session, permit.execution_key, revision.business_request_key
        )
        if existing is not None:
            session.commit()
            return self._applied_existing(permit, existing)
        if conclude_absence:
            self._raise_after_absence_observed()
            session.commit()
            return ExecutorResult(
                BusinessOutcome.DEFINITELY_NOT_APPLIED,
                failure_kind="authoritatively_absent",
                execution_key=permit.execution_key,
                business_request_key=revision.business_request_key,
            )
        persisted = verify_persisted_draft_integrity(revision)
        calendar = self._calendar.holidays_for_range(
            session,
            jurisdiction=workflow.jurisdiction,
            start_date=persisted.start_date,
            end_date=persisted.end_date,
            calendar_version=revision.calendar_version,
        )
        if (
            calendar.coverage is not CalendarCoverage.COVERED
            or persisted.readiness == READINESS_NOT_EXECUTABLE
        ):
            session.commit()
            return ExecutorResult(
                BusinessOutcome.DEFINITELY_NOT_APPLIED,
                failure_kind="calendar_unresolved",
                execution_key=permit.execution_key,
                business_request_key=revision.business_request_key,
            )
        overlaps = self._leave_queries.overlapping_active_annual_leave(
            session,
            employee_id=workflow.owner_employee_id,
            start_date=persisted.start_date,
            end_date=persisted.end_date,
        )
        distinct_overlap = next(
            (row for row in overlaps if row.business_request_key != revision.business_request_key),
            None,
        )
        if distinct_overlap is not None:
            session.commit()
            return ExecutorResult(
                BusinessOutcome.DEFINITELY_NOT_APPLIED,
                failure_kind="overlap",
                execution_key=permit.execution_key,
                business_request_key=revision.business_request_key,
            )
        live = self._preparation.prepare(
            session,
            context=AuthenticatedEmployeeContext(
                employee_id=workflow.owner_employee_id,
                subject_id=workflow.owner_subject_id,
                jurisdiction=workflow.jurisdiction,
            ),
            start_date=persisted.start_date,
            end_date=persisted.end_date,
            reason=persisted.reason,
        )
        if live.draft.requested_hours > live.snapshot.effective_available_hours:
            session.commit()
            return ExecutorResult(
                BusinessOutcome.DEFINITELY_NOT_APPLIED,
                failure_kind="insufficient_balance",
                execution_key=permit.execution_key,
                business_request_key=revision.business_request_key,
            )
        self._raise_failpoint("raise_before_insert")
        try:
            created = self._leave_commands.persist(
                session,
                NewLeaveRequest(
                    employee_id=workflow.owner_employee_id,
                    leave_type=LeaveType.ANNUAL,
                    start_date=persisted.start_date,
                    end_date=persisted.end_date,
                    requested_hours=quantize_hours(persisted.requested_hours),
                    reason=persisted.reason,
                    submitted_at=now,
                    execution_key=permit.execution_key,
                    business_request_key=revision.business_request_key,
                    source_action_id=permit.action_id,
                    source_action_revision=permit.revision,
                    calendar_version=revision.calendar_version,
                    ruleset_version=revision.ruleset_version,
                ),
            )
        except IntegrityError:
            session.rollback()
            return self._applied_after_conflict(permit, revision.business_request_key)
        self._raise_failpoint("raise_after_insert_before_commit")
        try:
            session.commit()
        except SQLAlchemyError as exc:
            raise _AmbiguousOutcome from exc
        if self._failpoints is not None and self._failpoints.report_unknown_after_commit:
            return ExecutorResult(
                BusinessOutcome.OUTCOME_UNKNOWN,
                leave_request_id=created.leave_request_id,
                execution_key=permit.execution_key,
                business_request_key=revision.business_request_key,
                resolution=ResolutionKind.CREATED.value,
            )
        self._raise_failpoint("raise_after_commit", after_commit=True)
        return ExecutorResult(
            BusinessOutcome.APPLIED,
            leave_request_id=created.leave_request_id,
            execution_key=permit.execution_key,
            business_request_key=revision.business_request_key,
            resolution=ResolutionKind.CREATED.value,
        )

    def _evaluate_fence(
        self,
        ledger: ActionExecutionLedger,
        revision,
        permit: ExecutionPermit,
        now,
        *,
        allowed_states: frozenset[str],
        allowed_ledger_statuses: frozenset[str],
    ) -> ExecutorResult | None:
        if ledger.execution_key != permit.execution_key:
            return _authority_lost("execution_key_mismatch", permit)
        if self._ledger.is_stale_generation(ledger.lease_generation, permit.lease_generation):
            return _authority_lost("stale_generation", permit)
        if ledger.lease_generation != permit.lease_generation:
            return _authority_lost("stale_generation", permit)
        if ledger.lease_owner_id != permit.lease_owner_id:
            return _authority_lost("wrong_lease_owner", permit)
        if ledger.lease_expires_at is None or ledger.lease_expires_at <= now:
            return _authority_lost("lease_expired", permit)
        if revision.state not in allowed_states:
            return _authority_lost("revision_not_executable", permit)
        if ledger.status not in allowed_ledger_statuses:
            return _authority_lost("ledger_not_executable", permit)
        return None

    def _find_existing_leave(
        self,
        session: Session,
        execution_key: str,
        business_request_key: str,
    ) -> LeaveRequest | None:
        found = self._leave_queries.find_by_execution_key(session, execution_key)
        if found is not None:
            return found
        return self._leave_queries.find_by_business_request_key(session, business_request_key)

    def _applied_existing(
        self,
        permit: ExecutionPermit,
        existing: LeaveRequest,
    ) -> ExecutorResult:
        resolution = (
            ResolutionKind.CREATED
            if existing.execution_key == permit.execution_key
            and existing.source_action_id == permit.action_id
            else ResolutionKind.ADOPTED_EXISTING
        )
        return ExecutorResult(
            BusinessOutcome.APPLIED,
            leave_request_id=existing.leave_request_id,
            execution_key=existing.execution_key,
            business_request_key=existing.business_request_key,
            resolution=resolution.value,
        )

    def _applied_after_conflict(
        self,
        permit: ExecutionPermit,
        business_request_key: str,
    ) -> ExecutorResult:
        with self._session_factory() as session:
            existing = self._find_existing_leave(
                session, permit.execution_key, business_request_key
            )
            if existing is None:
                return ExecutorResult(
                    BusinessOutcome.OUTCOME_UNKNOWN,
                    execution_key=permit.execution_key,
                    business_request_key=business_request_key,
                )
            return self._applied_existing(permit, existing)

    def _raise_failpoint(self, name: str, *, after_commit: bool = False) -> None:
        if self._failpoints is None:
            return
        error = getattr(self._failpoints, name)
        if error is None:
            return
        if after_commit:
            raise _AmbiguousOutcome from error
        raise error

    def _raise_after_absence_observed(self) -> None:
        if self._failpoints is None or self._failpoints.after_absence_observed is None:
            return
        self._failpoints.after_absence_observed()


def _authority_lost(failure_kind: str, permit: ExecutionPermit) -> ExecutorResult:
    return ExecutorResult(
        BusinessOutcome.EXECUTION_AUTHORITY_LOST,
        failure_kind=failure_kind,
        execution_key=permit.execution_key,
    )


class _AmbiguousOutcome(RuntimeError):
    """Internal signal that commit success cannot be proven to the caller."""

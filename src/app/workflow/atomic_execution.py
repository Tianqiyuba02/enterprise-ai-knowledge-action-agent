"""Atomic CONFIRMED execution: one action, one PostgreSQL transaction, no EXECUTING."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.workflow_models import ActionRevision, ActionWorkflow, LeaveRequest
from app.identity import AuthenticatedEmployeeContext
from app.workflow.audit_repository import AuditRepository, NewAuditEvent
from app.workflow.authority import CanonicalDraft
from app.workflow.calendar import V4_CALENDAR_VERSION, V4_RULESET_VERSION
from app.workflow.calendar_service import CalendarCoverage
from app.workflow.canonical import quantize_hours
from app.workflow.confirmation import AUDIT_ACTION_EXPIRED
from app.workflow.domain import V4_REVISION, ActorType, LeaveType, WorkflowState
from app.workflow.errors import WorkflowIntegrityError, WorkflowRowNotFoundError
from app.workflow.executable_preparation import (
    ExecutablePreparation,
    V4ExecutablePreparationService,
    load_confirmed_stable_authority,
    verify_persisted_draft_integrity,
)
from app.workflow.leave_command_repository import LeaveCommandRepository, NewLeaveRequest
from app.workflow.leave_equivalence import leaves_trusted_equivalent
from app.workflow.leave_query_repository import LeaveQueryRepository
from app.workflow.locks import acquire_employee_lock
from app.workflow.occupancy import is_leave_unique_violation
from app.workflow.time import database_now
from app.workflow.workflow_repository import WorkflowRepository

AUDIT_EXECUTION_SUCCEEDED = "EXECUTION_SUCCEEDED"
AUDIT_EXECUTION_FAILED = "EXECUTION_FAILED"
AUDIT_ACTION_STALE = "ACTION_STALE"

LOCK_TIMEOUT_SQL = "5s"
STATEMENT_TIMEOUT_SQL = "15s"
IDLE_IN_TRANSACTION_TIMEOUT_SQL = "15s"
OUTAGE_BACKOFF_INITIAL_SECONDS = 0.05
OUTAGE_BACKOFF_CAP_SECONDS = 5.0
ACTION_COOLDOWN_INITIAL_SECONDS = 0.05
ACTION_COOLDOWN_CAP_SECONDS = 2.0


class AtomicOutcome(StrEnum):
    IDLE = "IDLE"
    SUCCEEDED = "SUCCEEDED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    TRANSIENT = "TRANSIENT"
    LOST_ACK = "LOST_ACK"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class AtomicExecutionResult:
    action_id: UUID | None
    observed_state: str | None
    outcome: AtomicOutcome
    leave_request_id: UUID | None = None
    failure_kind: str | None = None
    adopted: bool = False

    @property
    def delivered(self) -> bool:
        return self.outcome not in {
            AtomicOutcome.IDLE,
            AtomicOutcome.TRANSIENT,
            AtomicOutcome.SKIPPED,
        }


@dataclass
class AtomicExecutionFailpoints:
    """Test-only hooks. Do not wire to HTTP, query params, or runtime env flags."""

    raise_before_leave_insert: BaseException | None = None
    raise_after_leave_insert: BaseException | None = None
    raise_after_succeeded_update: BaseException | None = None
    raise_on_audit: BaseException | None = None
    raise_transient_before_commit: BaseException | None = None
    raise_after_claim: Callable[[UUID], BaseException | None] | None = None
    discard_after_commit: bool = False
    hold_after_action_lock: Callable[[], None] | None = None
    hold_before_employee_lock: Callable[[], None] | None = None
    hold_before_leave_insert: Callable[[], None] | None = None
    hold_after_lost_ack_lock: Callable[[], None] | None = None


@dataclass
class _Cooldown:
    until: float
    delay: float


class AtomicConfirmedExecutor:
    """Claim one CONFIRMED action and finish it in a single PostgreSQL transaction."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: KnowledgeSettings | None = None,
        *,
        failpoints: AtomicExecutionFailpoints | None = None,
        worker_id: str = "confirmed-poller",
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or load_knowledge_settings()
        self._failpoints = failpoints or AtomicExecutionFailpoints()
        self.worker_id = worker_id
        self._workflows = WorkflowRepository()
        self._audits = AuditRepository()
        self._leave_queries = LeaveQueryRepository()
        self._leave_commands = LeaveCommandRepository()
        self._preparation = V4ExecutablePreparationService()
        self._cooldowns: dict[UUID, _Cooldown] = {}
        self._outage_until = 0.0
        self._outage_delay = OUTAGE_BACKOFF_INITIAL_SECONDS

    def execute_one(self) -> AtomicExecutionResult:
        return self._execute(action_id=None)

    def execute_action(self, action_id: UUID) -> AtomicExecutionResult:
        return self._execute(action_id=action_id)

    def note_loop_failure(self) -> None:
        """Process-level outage backoff after an unexpected poller-loop leak."""

        self._note_outage()

    def _execute(self, action_id: UUID | None) -> AtomicExecutionResult:
        now_mono = time.monotonic()
        if now_mono < self._outage_until:
            return AtomicExecutionResult(None, None, AtomicOutcome.TRANSIENT)
        lost_ack_id: UUID | None = None
        claimed_id: UUID | None = None
        try:
            with self._session_factory() as session:
                _apply_transaction_timeouts(session)
                revision = self._claim_confirmed(session, action_id)
                if revision is None:
                    session.rollback()
                    self._clear_outage()
                    return AtomicExecutionResult(action_id, None, AtomicOutcome.IDLE)
                claimed_id = revision.action_id
                if self._is_cooling(claimed_id):
                    session.rollback()
                    return AtomicExecutionResult(
                        claimed_id, WorkflowState.CONFIRMED.value, AtomicOutcome.SKIPPED
                    )
                self._hold("hold_after_action_lock")
                self._raise_claim_failpoint(claimed_id)
                result = self._finish_claimed(session, revision)
                self._raise_failpoint("raise_transient_before_commit", action_id=claimed_id)
                try:
                    session.commit()
                except SQLAlchemyError:
                    self._invalidate(session)
                    return self._observe_after_lost_ack(claimed_id)
                self._clear_cooldown(claimed_id)
                self._clear_outage()
                if self._failpoints.discard_after_commit:
                    lost_ack_id = claimed_id
                else:
                    return result
        except _IntegrityConflict as exc:
            return self._recover_after_conflict(exc.action_id)
        except IntegrityError as exc:
            if claimed_id is not None and is_leave_unique_violation(exc):
                return self._recover_after_conflict(claimed_id)
            if claimed_id is not None:
                self._note_action_cooldown(claimed_id)
                return AtomicExecutionResult(
                    claimed_id, WorkflowState.CONFIRMED.value, AtomicOutcome.TRANSIENT
                )
            self._note_outage()
            return AtomicExecutionResult(action_id, None, AtomicOutcome.TRANSIENT)
        except _TransientExecution as exc:
            cooled = exc.action_id or claimed_id
            self._note_action_cooldown(cooled)
            return AtomicExecutionResult(
                cooled, WorkflowState.CONFIRMED.value, AtomicOutcome.TRANSIENT
            )
        except (OperationalError, SQLAlchemyError):
            if claimed_id is not None:
                self._note_action_cooldown(claimed_id)
                return AtomicExecutionResult(
                    claimed_id, WorkflowState.CONFIRMED.value, AtomicOutcome.TRANSIENT
                )
            self._note_outage()
            return AtomicExecutionResult(action_id, None, AtomicOutcome.TRANSIENT)
        except Exception:
            if claimed_id is not None:
                self._note_action_cooldown(claimed_id)
                return AtomicExecutionResult(
                    claimed_id, WorkflowState.CONFIRMED.value, AtomicOutcome.TRANSIENT
                )
            self._note_outage()
            return AtomicExecutionResult(action_id, None, AtomicOutcome.TRANSIENT)
        if lost_ack_id is not None:
            return self._observe_after_lost_ack(lost_ack_id)
        return AtomicExecutionResult(action_id, None, AtomicOutcome.IDLE)

    def _finish_claimed(
        self,
        session: Session,
        revision: ActionRevision,
    ) -> AtomicExecutionResult:
        workflow = session.get(ActionWorkflow, revision.action_id)
        if workflow is None:
            raise WorkflowIntegrityError("claimed action workflow was not found")
        now = database_now(session)
        if _ttl_expired(revision, now):
            return self._expire(session, workflow, revision, now)
        self._hold("hold_before_employee_lock")
        acquire_employee_lock(session, workflow.owner_employee_id)
        now = database_now(session)
        if _ttl_expired(revision, now):
            return self._expire(session, workflow, revision, now)
        classified = self._classify(session, workflow, revision)
        if classified.terminal is not None:
            return self._fail(
                session,
                workflow,
                revision,
                classified.terminal,
                classified.failure_kind or "revalidation",
            )
        assert classified.preparation is not None
        assert classified.persisted is not None
        try:
            existing = self._preprobe_leaves(session, workflow, revision, classified.persisted)
        except _AdoptionRejected as exc:
            return self._fail(session, workflow, revision, WorkflowState.EXECUTION_FAILED, exc.kind)
        now = database_now(session)
        if _ttl_expired(revision, now):
            return self._expire(session, workflow, revision, now)
        if existing is not None:
            return self._succeed(session, workflow, revision, existing, adopted=True)
        self._hold("hold_before_leave_insert")
        self._raise_failpoint("raise_before_leave_insert", action_id=workflow.action_id)
        try:
            created = self._leave_commands.persist(
                session,
                NewLeaveRequest(
                    employee_id=workflow.owner_employee_id,
                    leave_type=LeaveType.ANNUAL,
                    start_date=classified.persisted.start_date,
                    end_date=classified.persisted.end_date,
                    requested_hours=quantize_hours(classified.persisted.requested_hours),
                    reason=classified.persisted.reason,
                    submitted_at=now,
                    execution_key=None,
                    business_request_key=revision.business_request_key,
                    source_action_id=workflow.action_id,
                    source_action_revision=revision.revision,
                    calendar_version=revision.calendar_version,
                    ruleset_version=revision.ruleset_version,
                ),
            )
        except IntegrityError as exc:
            session.rollback()
            if not is_leave_unique_violation(exc):
                raise
            raise _IntegrityConflict(workflow.action_id) from exc
        self._raise_failpoint("raise_after_leave_insert")
        return self._succeed(session, workflow, revision, created, adopted=False)

    def _recover_after_conflict(self, action_id: UUID) -> AtomicExecutionResult:
        with self._session_factory() as session:
            _apply_transaction_timeouts(session)
            revision = self._claim_confirmed(session, action_id)
            if revision is None:
                session.rollback()
                return self._observe_after_lost_ack(action_id)
            workflow = session.get(ActionWorkflow, revision.action_id)
            if workflow is None:
                raise WorkflowIntegrityError("conflict-recovery workflow was not found")
            acquire_employee_lock(session, workflow.owner_employee_id)
            now = database_now(session)
            if _ttl_expired(revision, now):
                result = self._expire(session, workflow, revision, now)
                session.commit()
                return result
            persisted = verify_persisted_draft_integrity(revision)
            try:
                existing = self._preprobe_leaves(session, workflow, revision, persisted)
            except _AdoptionRejected as exc:
                result = self._fail(
                    session, workflow, revision, WorkflowState.EXECUTION_FAILED, exc.kind
                )
                session.commit()
                return result
            if existing is None:
                result = self._fail(
                    session,
                    workflow,
                    revision,
                    WorkflowState.EXECUTION_FAILED,
                    "UNIQUE_CONFLICT_UNRESOLVED",
                )
                session.commit()
                return result
            result = self._succeed(session, workflow, revision, existing, adopted=True)
            session.commit()
            return result

    def _claim_confirmed(self, session: Session, action_id: UUID | None) -> ActionRevision | None:
        cooling = [
            item for item, cooldown in self._cooldowns.items() if time.monotonic() < cooldown.until
        ]
        params: dict[str, object] = {"confirmed": WorkflowState.CONFIRMED.value}
        filters = ["state = :confirmed"]
        if action_id is not None:
            filters.append("action_id = :action_id")
            params["action_id"] = action_id
        elif cooling:
            filters.append("action_id NOT IN :cooling")
            params["cooling"] = cooling
        statement = text(
            f"""
            SELECT action_id
            FROM action_revisions
            WHERE {" AND ".join(filters)}
            ORDER BY confirmed_at, action_id
            FOR NO KEY UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        if cooling and action_id is None:
            statement = statement.bindparams(bindparam("cooling", expanding=True))
        locked_id = session.execute(statement, params).scalar_one_or_none()
        if locked_id is None:
            return None
        return session.execute(
            select(ActionRevision).where(
                ActionRevision.action_id == locked_id,
                ActionRevision.revision == V4_REVISION,
            )
        ).scalar_one()

    def _classify(
        self,
        session: Session,
        workflow: ActionWorkflow,
        revision: ActionRevision,
    ) -> _Classification:
        try:
            persisted = verify_persisted_draft_integrity(revision)
        except WorkflowIntegrityError:
            return _Classification(WorkflowState.EXECUTION_FAILED, "DRAFT_INTEGRITY_FAILURE")
        if (
            revision.calendar_version != V4_CALENDAR_VERSION
            or revision.ruleset_version != V4_RULESET_VERSION
            or persisted.calendar_version != revision.calendar_version
            or persisted.ruleset_version != revision.ruleset_version
        ):
            return _Classification(WorkflowState.STALE, "AUTHORITY_CHANGED")
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
        if _stable_authority_changed(workflow, revision, persisted, live):
            return _Classification(WorkflowState.STALE, "AUTHORITY_CHANGED")
        if live.coverage is not CalendarCoverage.COVERED:
            return _Classification(WorkflowState.EXECUTION_FAILED, "CALENDAR_UNCOVERED")
        if live.draft.requested_hours > live.snapshot.effective_available_hours:
            return _Classification(WorkflowState.EXECUTION_FAILED, "INSUFFICIENT_BALANCE")
        overlaps = self._leave_queries.overlapping_active_annual_leave(
            session,
            employee_id=workflow.owner_employee_id,
            start_date=persisted.start_date,
            end_date=persisted.end_date,
        )
        foreign = [
            row
            for row in overlaps
            if row.business_request_key != revision.business_request_key
            and row.source_action_id != workflow.action_id
        ]
        if foreign:
            return _Classification(WorkflowState.EXECUTION_FAILED, "OVERLAP")
        return _Classification(None, None, live, persisted)

    def _preprobe_leaves(
        self,
        session: Session,
        workflow: ActionWorkflow,
        revision: ActionRevision,
        persisted: CanonicalDraft,
    ) -> LeaveRequest | None:
        by_source = self._leave_queries.find_by_source_action_id(session, workflow.action_id)
        by_key = self._leave_queries.find_by_business_request_key(
            session, revision.business_request_key
        )
        if (
            by_source is not None
            and by_key is not None
            and by_source.leave_request_id != by_key.leave_request_id
        ):
            raise _AdoptionRejected("ADOPTION_IDENTITY_CONFLICT")
        existing = by_source or by_key
        if existing is None:
            return None
        if not action_leave_trusted_equivalent(workflow, revision, persisted, existing):
            raise _AdoptionRejected("ADOPTION_MISMATCH")
        return existing

    def _succeed(
        self,
        session: Session,
        workflow: ActionWorkflow,
        revision: ActionRevision,
        leave: LeaveRequest,
        *,
        adopted: bool,
    ) -> AtomicExecutionResult:
        from_state = revision.state
        revision.state = WorkflowState.SUCCEEDED.value
        session.flush()
        self._raise_failpoint("raise_after_succeeded_update")
        self._audit(
            session,
            workflow,
            from_state=from_state,
            to_state=WorkflowState.SUCCEEDED.value,
            event_type=AUDIT_EXECUTION_SUCCEEDED,
            metadata={
                "leave_request_id": str(leave.leave_request_id),
                "adopted": adopted,
                "worker_id": self.worker_id,
            },
        )
        return AtomicExecutionResult(
            workflow.action_id,
            WorkflowState.SUCCEEDED.value,
            AtomicOutcome.SUCCEEDED,
            leave_request_id=leave.leave_request_id,
            adopted=adopted,
        )

    def _fail(
        self,
        session: Session,
        workflow: ActionWorkflow,
        revision: ActionRevision,
        state: WorkflowState,
        failure_kind: str,
    ) -> AtomicExecutionResult:
        from_state = revision.state
        revision.state = state.value
        event_type = AUDIT_ACTION_STALE if state is WorkflowState.STALE else AUDIT_EXECUTION_FAILED
        self._audit(
            session,
            workflow,
            from_state=from_state,
            to_state=state.value,
            event_type=event_type,
            metadata={"failure_kind": failure_kind, "worker_id": self.worker_id},
        )
        outcome = (
            AtomicOutcome.STALE if state is WorkflowState.STALE else AtomicOutcome.EXECUTION_FAILED
        )
        return AtomicExecutionResult(
            workflow.action_id,
            state.value,
            outcome,
            failure_kind=failure_kind,
        )

    def _expire(
        self,
        session: Session,
        workflow: ActionWorkflow,
        revision: ActionRevision,
        now: datetime,
    ) -> AtomicExecutionResult:
        del now
        from_state = revision.state
        revision.state = WorkflowState.EXPIRED.value
        self._audit(
            session,
            workflow,
            from_state=from_state,
            to_state=WorkflowState.EXPIRED.value,
            event_type=AUDIT_ACTION_EXPIRED,
            metadata={"reason": "confirmed_ttl", "worker_id": self.worker_id},
        )
        return AtomicExecutionResult(
            workflow.action_id,
            WorkflowState.EXPIRED.value,
            AtomicOutcome.EXPIRED,
        )

    def _audit(
        self,
        session: Session,
        workflow: ActionWorkflow,
        *,
        from_state: str,
        to_state: str,
        event_type: str,
        metadata: dict[str, Any],
    ) -> None:
        self._raise_failpoint("raise_on_audit")
        self._audits.insert(
            session,
            NewAuditEvent(
                action_id=workflow.action_id,
                event_type=event_type,
                actor_type=ActorType.WORKER,
                actor_subject_id=self.worker_id,
                from_state=from_state,
                to_state=to_state,
                safe_metadata=metadata,
            ),
        )

    def _observe_after_lost_ack(self, action_id: UUID) -> AtomicExecutionResult:
        with self._session_factory() as session:
            try:
                revision = self._workflows.lock_revision(session, action_id=action_id)
            except WorkflowRowNotFoundError:
                session.rollback()
                return AtomicExecutionResult(action_id, None, AtomicOutcome.LOST_ACK)
            self._hold("hold_after_lost_ack_lock")
            leave = self._leave_queries.find_by_source_action_id(session, action_id)
            session.commit()
            return AtomicExecutionResult(
                action_id,
                revision.state,
                AtomicOutcome.LOST_ACK,
                leave_request_id=None if leave is None else leave.leave_request_id,
                adopted=False,
            )

    def _raise_failpoint(self, name: str, *, action_id: UUID | None = None) -> None:
        error = getattr(self._failpoints, name)
        if error is None:
            return
        if isinstance(error, OperationalError):
            raise _TransientExecution(action_id) from error
        raise error

    def _raise_claim_failpoint(self, action_id: UUID) -> None:
        factory = self._failpoints.raise_after_claim
        if factory is None:
            return
        error = factory(action_id)
        if error is None:
            return
        if isinstance(error, OperationalError):
            raise _TransientExecution(action_id) from error
        raise error

    def _hold(self, name: str) -> None:
        callback = getattr(self._failpoints, name)
        if callback is not None:
            callback()

    def _invalidate(self, session: Session) -> None:
        session.rollback()
        bind = session.get_bind()
        if bind is not None and hasattr(bind, "invalidate"):
            bind.invalidate()

    def _is_cooling(self, action_id: UUID) -> bool:
        cooldown = self._cooldowns.get(action_id)
        return cooldown is not None and time.monotonic() < cooldown.until

    def _note_action_cooldown(self, action_id: UUID | None) -> None:
        if action_id is None:
            return
        previous = self._cooldowns.get(action_id)
        delay = (
            min(ACTION_COOLDOWN_CAP_SECONDS, previous.delay * 2)
            if previous is not None
            else ACTION_COOLDOWN_INITIAL_SECONDS
        )
        jitter = random.uniform(0.0, delay / 2)
        self._cooldowns[action_id] = _Cooldown(time.monotonic() + delay + jitter, delay)

    def _clear_cooldown(self, action_id: UUID) -> None:
        self._cooldowns.pop(action_id, None)

    def _note_outage(self) -> None:
        jitter = random.uniform(0.0, self._outage_delay / 2)
        self._outage_until = time.monotonic() + self._outage_delay + jitter
        self._outage_delay = min(OUTAGE_BACKOFF_CAP_SECONDS, self._outage_delay * 2)

    def _clear_outage(self) -> None:
        self._outage_until = 0.0
        self._outage_delay = OUTAGE_BACKOFF_INITIAL_SECONDS


def action_leave_trusted_equivalent(
    workflow: ActionWorkflow,
    revision: ActionRevision,
    persisted: CanonicalDraft,
    leave: LeaveRequest,
) -> bool:
    return leaves_trusted_equivalent(
        employee_id=workflow.owner_employee_id,
        action_id=workflow.action_id,
        revision=revision.revision,
        leave_type=persisted.leave_type,
        start_date=persisted.start_date,
        end_date=persisted.end_date,
        requested_hours=persisted.requested_hours,
        business_request_key=revision.business_request_key,
        reason=persisted.reason,
        calendar_version=revision.calendar_version,
        ruleset_version=revision.ruleset_version,
        leave=leave,
    )


def _stable_authority_changed(
    workflow: ActionWorkflow,
    revision: ActionRevision,
    persisted: CanonicalDraft,
    live: ExecutablePreparation,
) -> bool:
    if not isinstance(revision.draft_payload, dict):
        return True
    confirmed = load_confirmed_stable_authority(revision.draft_payload)
    if confirmed is None:
        return True
    snapshot = live.snapshot
    return (
        snapshot.employee_id != confirmed["employee_id"]
        or snapshot.jurisdiction != confirmed["jurisdiction"]
        or snapshot.work_days != confirmed["work_days"]
        or snapshot.hours_per_day != confirmed["hours_per_day"]
        or snapshot.timezone != confirmed["timezone"]
        or snapshot.calendar_version != confirmed["calendar_version"]
        or snapshot.ruleset_version != confirmed["ruleset_version"]
        or snapshot.employee_id != workflow.owner_employee_id
        or snapshot.jurisdiction != workflow.jurisdiction
        or live.draft.leave_type != persisted.leave_type
        or live.draft.start_date != persisted.start_date
        or live.draft.end_date != persisted.end_date
        or live.draft.requested_hours != persisted.requested_hours
        or live.business_request_key != revision.business_request_key
        or live.draft.calendar_version != revision.calendar_version
        or live.draft.ruleset_version != revision.ruleset_version
        or live.draft.action_type != persisted.action_type
    )


def _ttl_expired(revision: ActionRevision, now: datetime) -> bool:
    if revision.confirmed_expires_at is None or revision.confirmed_at is None:
        raise WorkflowIntegrityError("CONFIRMED action is missing confirmation timestamps")
    return revision.confirmed_expires_at <= now


def _apply_transaction_timeouts(session: Session) -> None:
    session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_SQL}'"))
    session.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_SQL}'"))
    session.execute(
        text(f"SET LOCAL idle_in_transaction_session_timeout = '{IDLE_IN_TRANSACTION_TIMEOUT_SQL}'")
    )


@dataclass(frozen=True, slots=True)
class _Classification:
    terminal: WorkflowState | None
    failure_kind: str | None
    preparation: ExecutablePreparation | None = None
    persisted: CanonicalDraft | None = None


class _AdoptionRejected(RuntimeError):
    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)


class _IntegrityConflict(RuntimeError):
    def __init__(self, action_id: UUID) -> None:
        self.action_id = action_id
        super().__init__(str(action_id))


class _TransientExecution(RuntimeError):
    def __init__(self, action_id: UUID | None) -> None:
        self.action_id = action_id
        super().__init__("transient atomic execution failure")

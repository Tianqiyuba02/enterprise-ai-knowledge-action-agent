"""Deterministic T4 execution reservation, leases, and fencing permits."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.workflow_models import ActionExecutionLedger, ActionRevision, ActionWorkflow
from app.identity import AuthenticatedEmployeeContext
from app.workflow.audit_repository import AuditRepository, NewAuditEvent
from app.workflow.confirmation import AUDIT_ACTION_EXPIRED
from app.workflow.domain import (
    UNRESOLVED_EXECUTION_STATES,
    V4_REVISION,
    ActorType,
    ExecutionLedgerStatus,
    ExecutionOperation,
    WorkflowState,
)
from app.workflow.errors import (
    ExecutionFenceError,
    WorkflowIntegrityError,
    WorkflowRowNotFoundError,
)
from app.workflow.executable_preparation import V4ExecutablePreparationService
from app.workflow.execution_repository import ExecutionLedgerRepository, NewExecutionReservation
from app.workflow.locks import acquire_business_request_lock
from app.workflow.time import database_now
from app.workflow.workflow_repository import WorkflowRepository

AUDIT_ACTION_STALE = "ACTION_STALE"
AUDIT_EXECUTION_RESERVED = "EXECUTION_RESERVED"
AUDIT_EXECUTION_CAS_LOST = "EXECUTION_CAS_LOST"
AUDIT_EXECUTION_RESERVATION_REUSED = "EXECUTION_RESERVATION_REUSED"
AUDIT_LEASE_TAKEOVER = "LEASE_TAKEOVER"


class ReservationOutcome(StrEnum):
    RESERVED = "RESERVED"
    ALREADY_RESERVED = "ALREADY_RESERVED"
    BLOCKED_UNRESOLVED = "BLOCKED_UNRESOLVED"
    EXPIRED = "EXPIRED"
    STALE = "STALE"
    NOT_CONFIRMABLE = "NOT_CONFIRMABLE"


@dataclass(frozen=True, slots=True)
class ExecutionPermit:
    """Internal fencing permit. Not chat, checkpoint, or client authority."""

    execution_key: str
    lease_owner_id: str
    lease_generation: int
    action_id: UUID
    revision: int


@dataclass(frozen=True, slots=True)
class ReservationResult:
    outcome: ReservationOutcome
    state: str
    permit: ExecutionPermit | None = None
    retryable: bool = False

    @property
    def reserved(self) -> bool:
        return self.outcome in {
            ReservationOutcome.RESERVED,
            ReservationOutcome.ALREADY_RESERVED,
        }


def generate_execution_key() -> str:
    """Return 256 bits of server-owned entropy as a hex execution key."""

    return secrets.token_hex(32)


def permit_from_ledger(row: ActionExecutionLedger) -> ExecutionPermit:
    if row.lease_owner_id is None:
        raise WorkflowIntegrityError("execution reservation is missing lease_owner_id")
    return ExecutionPermit(
        execution_key=row.execution_key,
        lease_owner_id=row.lease_owner_id,
        lease_generation=row.lease_generation,
        action_id=row.action_id,
        revision=row.revision,
    )


def permit_for_caller(row: ActionExecutionLedger, worker_id: str) -> ExecutionPermit:
    """Return a permit only when the caller already owns the ledger lease."""

    if not worker_id or not worker_id.strip():
        raise WorkflowIntegrityError("worker_id is required to load an execution permit")
    if row.lease_owner_id != worker_id:
        raise ExecutionFenceError("caller does not own the execution lease")
    return permit_from_ledger(row)


class ExecutionReservationService:
    """Reserve exactly one executable attempt after deterministic revalidation."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: KnowledgeSettings | None = None,
        *,
        preparation: V4ExecutablePreparationService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or load_knowledge_settings()
        self._workflows = WorkflowRepository()
        self._ledger = ExecutionLedgerRepository()
        self._audits = AuditRepository()
        self._preparation = preparation or V4ExecutablePreparationService()

    def reserve(
        self,
        *,
        action_id: UUID,
        revision: int,
        worker_id: str,
    ) -> ReservationResult:
        if revision != V4_REVISION:
            raise WorkflowIntegrityError("only V4 revision=1 may be reserved")
        if not worker_id or not worker_id.strip():
            raise WorkflowIntegrityError("worker_id is required for execution reservation")
        with self._session_factory() as session:
            workflow = self._workflows.lock_workflow(session, action_id)
            row = self._workflows.lock_revision(session, action_id=action_id, revision=revision)
            now = database_now(session)
            self._expire_confirmed_if_needed(session, workflow, row, now)
            if row.state == WorkflowState.EXPIRED.value:
                session.commit()
                return ReservationResult(ReservationOutcome.EXPIRED, row.state)
            if row.state == WorkflowState.STALE.value:
                session.commit()
                return ReservationResult(ReservationOutcome.STALE, row.state)
            existing = self._ledger.get_by_action(session, action_id=action_id, revision=revision)
            if existing is not None:
                same_owner = existing.lease_owner_id == worker_id
                self._audits.insert(
                    session,
                    NewAuditEvent(
                        action_id=action_id,
                        event_type=AUDIT_EXECUTION_RESERVATION_REUSED
                        if same_owner
                        else AUDIT_EXECUTION_CAS_LOST,
                        actor_type=ActorType.WORKER,
                        actor_subject_id=worker_id,
                        from_state=row.state,
                        to_state=row.state,
                        safe_metadata={"lease_generation": existing.lease_generation},
                        revision=revision,
                    ),
                )
                session.commit()
                return ReservationResult(
                    ReservationOutcome.ALREADY_RESERVED,
                    row.state,
                    permit=permit_for_caller(existing, worker_id) if same_owner else None,
                )
            if row.state != WorkflowState.CONFIRMED.value:
                session.commit()
                return ReservationResult(ReservationOutcome.NOT_CONFIRMABLE, row.state)
            revalidation = self._preparation.revalidate_confirmed(session, workflow, row)
            if revalidation.stale:
                from_state = row.state
                row.state = WorkflowState.STALE.value
                self._audits.insert(
                    session,
                    NewAuditEvent(
                        action_id=action_id,
                        event_type=AUDIT_ACTION_STALE,
                        actor_type=ActorType.SYSTEM,
                        actor_subject_id=worker_id,
                        from_state=from_state,
                        to_state=WorkflowState.STALE.value,
                        safe_metadata={"reason": "revalidation_drift"},
                        revision=revision,
                    ),
                )
                session.commit()
                return ReservationResult(ReservationOutcome.STALE, row.state)
            acquire_business_request_lock(session, row.business_request_key)
            unresolved = self._workflows.list_unresolved_by_business_request_key(
                session, row.business_request_key
            )
            blocking = tuple(
                item
                for item in unresolved
                if item.action_id != action_id
                and item.state in {state.value for state in UNRESOLVED_EXECUTION_STATES}
            )
            if blocking:
                session.commit()
                return ReservationResult(
                    ReservationOutcome.BLOCKED_UNRESOLVED,
                    row.state,
                    retryable=True,
                )
            execution_key = generate_execution_key()
            lease_expires_at = now + timedelta(
                seconds=self._settings.v4_execution_lease_ttl_seconds
            )
            from_state = row.state
            row.state = WorkflowState.EXECUTING.value
            ledger = self._ledger.create_reservation(
                session,
                NewExecutionReservation(
                    action_id=action_id,
                    execution_key=execution_key,
                    operation=ExecutionOperation.SUBMIT_ANNUAL_LEAVE,
                    status=ExecutionLedgerStatus.RESERVED,
                    revision=revision,
                    lease_owner_id=worker_id,
                    lease_generation=1,
                    lease_expires_at=lease_expires_at,
                ),
            )
            ledger.attempt_count = 1
            ledger.last_heartbeat_at = now
            ledger.started_at = now
            self._audits.insert(
                session,
                NewAuditEvent(
                    action_id=action_id,
                    event_type=AUDIT_EXECUTION_RESERVED,
                    actor_type=ActorType.WORKER,
                    actor_subject_id=worker_id,
                    from_state=from_state,
                    to_state=WorkflowState.EXECUTING.value,
                    safe_metadata={
                        "lease_generation": 1,
                        "operation": ExecutionOperation.SUBMIT_ANNUAL_LEAVE.value,
                    },
                    revision=revision,
                ),
            )
            session.commit()
            return ReservationResult(
                ReservationOutcome.RESERVED,
                WorkflowState.EXECUTING.value,
                permit=permit_for_caller(ledger, worker_id),
            )

    def takeover_expired_lease(
        self,
        *,
        action_id: UUID,
        revision: int,
        worker_id: str,
    ) -> ExecutionPermit:
        if not worker_id or not worker_id.strip():
            raise WorkflowIntegrityError("worker_id is required for lease takeover")
        with self._session_factory() as session:
            self._workflows.lock_workflow(session, action_id)
            self._workflows.lock_revision(session, action_id=action_id, revision=revision)
            ledger = self._ledger.lock_reservation(session, action_id=action_id, revision=revision)
            now = database_now(session)
            if ledger.lease_expires_at is None or ledger.lease_expires_at > now:
                if ledger.lease_owner_id == worker_id:
                    session.commit()
                    return permit_for_caller(ledger, worker_id)
                raise ExecutionFenceError("execution lease is still owned")
            previous_owner = ledger.lease_owner_id
            previous_generation = ledger.lease_generation
            ledger.lease_owner_id = worker_id
            ledger.lease_generation = previous_generation + 1
            ledger.lease_expires_at = now + timedelta(
                seconds=self._settings.v4_execution_lease_ttl_seconds
            )
            ledger.last_heartbeat_at = now
            self._audits.insert(
                session,
                NewAuditEvent(
                    action_id=action_id,
                    event_type=AUDIT_LEASE_TAKEOVER,
                    actor_type=ActorType.WORKER,
                    actor_subject_id=worker_id,
                    from_state=WorkflowState.EXECUTING.value,
                    to_state=WorkflowState.EXECUTING.value,
                    safe_metadata={
                        "previous_lease_generation": previous_generation,
                        "lease_generation": ledger.lease_generation,
                        "previous_lease_owner_present": previous_owner is not None,
                    },
                    revision=revision,
                ),
            )
            session.commit()
            return permit_for_caller(ledger, worker_id)

    def reload_permit(
        self,
        *,
        action_id: UUID,
        revision: int = V4_REVISION,
        worker_id: str,
    ) -> ExecutionPermit:
        if not worker_id or not worker_id.strip():
            raise WorkflowIntegrityError("worker_id is required to reload an execution permit")
        with self._session_factory() as session:
            ledger = self._ledger.get_by_action(session, action_id=action_id, revision=revision)
            if ledger is None:
                raise WorkflowRowNotFoundError("execution reservation was not found")
            return permit_for_caller(ledger, worker_id)

    def _expire_confirmed_if_needed(
        self,
        session: Session,
        workflow: ActionWorkflow,
        revision: ActionRevision,
        now,
    ) -> None:
        if revision.state != WorkflowState.CONFIRMED.value:
            return
        if revision.confirmed_expires_at is None or revision.confirmed_expires_at > now:
            return
        from_state = revision.state
        revision.state = WorkflowState.EXPIRED.value
        self._audits.insert(
            session,
            NewAuditEvent(
                action_id=workflow.action_id,
                event_type=AUDIT_ACTION_EXPIRED,
                actor_type=ActorType.SYSTEM,
                from_state=from_state,
                to_state=WorkflowState.EXPIRED.value,
                revision=revision.revision,
            ),
        )
        session.flush()


def owner_context(workflow: ActionWorkflow) -> AuthenticatedEmployeeContext:
    return AuthenticatedEmployeeContext(
        employee_id=workflow.owner_employee_id,
        subject_id=workflow.owner_subject_id,
        jurisdiction=workflow.jurisdiction,
    )

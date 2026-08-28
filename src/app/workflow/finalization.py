"""T5 execution finalization and durable reconciliation scheduling."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings
from app.workflow.audit_repository import AuditRepository, NewAuditEvent
from app.workflow.domain import (
    ActorType,
    ExecutionLedgerStatus,
    OutboxEventType,
    WorkflowState,
)
from app.workflow.errors import (
    DuplicateWorkflowEventError,
    ExecutionFenceError,
    WorkflowIntegrityError,
)
from app.workflow.execution import ExecutionPermit, permit_for_caller
from app.workflow.execution_repository import ExecutionLedgerRepository
from app.workflow.executor import BusinessOutcome, ExecutorResult, ResolutionKind
from app.workflow.leave_query_repository import LeaveQueryRepository
from app.workflow.locks import acquire_business_request_lock
from app.workflow.outbox_repository import NewOutboxEvent, OutboxRepository
from app.workflow.time import database_now
from app.workflow.workflow_repository import WorkflowRepository

AUDIT_EXECUTION_SUCCEEDED = "EXECUTION_SUCCEEDED"
AUDIT_EXECUTION_FAILED = "EXECUTION_FAILED"
AUDIT_EXECUTION_OUTCOME_UNKNOWN = "EXECUTION_OUTCOME_UNKNOWN"
AUDIT_RECONCILIATION_STARTED = "RECONCILIATION_STARTED"
AUDIT_RECONCILIATION_SUCCEEDED = "RECONCILIATION_SUCCEEDED"
AUDIT_RECONCILIATION_ABSENT = "RECONCILIATION_ABSENT"
AUDIT_RECONCILIATION_UNKNOWN = "RECONCILIATION_UNKNOWN"
AUDIT_EXECUTION_AUTHORITY_LOST = "EXECUTION_AUTHORITY_LOST"

MAX_AUTOMATIC_RECONCILIATION_ATTEMPTS = 3
RECONCILE_BACKOFF_CAP_SECONDS = 60
TERMINAL_EXECUTION_STATES = frozenset(
    {
        WorkflowState.SUCCEEDED.value,
        WorkflowState.EXECUTION_FAILED.value,
    }
)
RECONCILE_SOURCE_STATES = frozenset(
    {
        WorkflowState.EXECUTING.value,
        WorkflowState.UNKNOWN_OUTCOME.value,
        WorkflowState.RECONCILING.value,
    }
)


def reconciliation_outbox_event_key(
    action_id: UUID,
    revision: int,
    execution_key: str,
    attempt: int,
) -> str:
    return f"reconcile_requested:{action_id}:{revision}:{execution_key}:{attempt}"


@dataclass
class FinalizationFailpoints:
    """Test-only hooks. Do not wire to HTTP, query params, or runtime env flags."""

    after_absence_observed: Callable[[], None] | None = None


class ExecutionFinalizationService:
    """Apply proven executor results to the authoritative revision and ledger."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: KnowledgeSettings | None = None,
        *,
        failpoints: FinalizationFailpoints | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or load_knowledge_settings()
        self._failpoints = failpoints
        self._workflows = WorkflowRepository()
        self._ledger = ExecutionLedgerRepository()
        self._outbox = OutboxRepository()
        self._audits = AuditRepository()
        self._leave_queries = LeaveQueryRepository()

    def finalize(self, permit: ExecutionPermit, result: ExecutorResult) -> str:
        """Persist a supplied execute-path outcome. Never invokes the executor."""

        with self._session_factory() as session:
            self._workflows.lock_workflow(session, permit.action_id)
            revision = self._workflows.lock_revision(
                session, action_id=permit.action_id, revision=permit.revision
            )
            ledger = self._ledger.lock_reservation(
                session, action_id=permit.action_id, revision=permit.revision
            )
            if ledger.execution_key != permit.execution_key:
                raise WorkflowIntegrityError("finalization execution_key mismatch")
            if result.outcome is BusinessOutcome.EXECUTION_AUTHORITY_LOST:
                self._audit_authority_lost(session, permit, revision, result)
                session.commit()
                return revision.state
            if ledger.lease_generation != permit.lease_generation:
                self._audit_authority_lost(
                    session,
                    permit,
                    revision,
                    ExecutorResult(
                        BusinessOutcome.EXECUTION_AUTHORITY_LOST,
                        failure_kind="stale_generation",
                        execution_key=permit.execution_key,
                    ),
                )
                session.commit()
                return revision.state
            now = database_now(session)
            if revision.state in TERMINAL_EXECUTION_STATES:
                session.commit()
                return revision.state
            if result.outcome is BusinessOutcome.APPLIED:
                self._apply_terminal(
                    session,
                    revision,
                    ledger,
                    permit,
                    now=now,
                    to_state=WorkflowState.SUCCEEDED,
                    ledger_status=ExecutionLedgerStatus.COMPLETED,
                    event_type=AUDIT_EXECUTION_SUCCEEDED,
                    result=result,
                    recon_event=AUDIT_RECONCILIATION_SUCCEEDED
                    if revision.state
                    in {
                        WorkflowState.RECONCILING.value,
                        WorkflowState.UNKNOWN_OUTCOME.value,
                    }
                    else None,
                )
            elif result.outcome is BusinessOutcome.DEFINITELY_NOT_APPLIED:
                acquire_business_request_lock(session, revision.business_request_key)
                existing = self._find_existing_leave(
                    session, permit.execution_key, revision.business_request_key
                )
                if existing is not None:
                    adopted = ExecutorResult(
                        BusinessOutcome.APPLIED,
                        leave_request_id=existing.leave_request_id,
                        execution_key=existing.execution_key,
                        business_request_key=existing.business_request_key,
                        resolution=ResolutionKind.ADOPTED_EXISTING.value
                        if existing.source_action_id != permit.action_id
                        else ResolutionKind.CREATED.value,
                    )
                    self._apply_terminal(
                        session,
                        revision,
                        ledger,
                        permit,
                        now=now,
                        to_state=WorkflowState.SUCCEEDED,
                        ledger_status=ExecutionLedgerStatus.COMPLETED,
                        event_type=AUDIT_EXECUTION_SUCCEEDED,
                        result=adopted,
                    )
                else:
                    self._apply_terminal(
                        session,
                        revision,
                        ledger,
                        permit,
                        now=now,
                        to_state=WorkflowState.EXECUTION_FAILED,
                        ledger_status=ExecutionLedgerStatus.FAILED,
                        event_type=AUDIT_EXECUTION_FAILED,
                        failure_kind=result.failure_kind,
                        result=result,
                        recon_event=AUDIT_RECONCILIATION_ABSENT
                        if revision.state
                        in {
                            WorkflowState.RECONCILING.value,
                            WorkflowState.UNKNOWN_OUTCOME.value,
                        }
                        else None,
                    )
            else:
                self._apply_unknown(session, revision, ledger, permit, now)
            session.commit()
            return revision.state

    def classify_and_finalize(self, permit: ExecutionPermit, worker_id: str) -> str:
        """Probe business state and persist the terminal classification in one transaction."""

        with self._session_factory() as session:
            self._workflows.lock_workflow(session, permit.action_id)
            revision = self._workflows.lock_revision(
                session, action_id=permit.action_id, revision=permit.revision
            )
            ledger = self._ledger.lock_reservation(
                session, action_id=permit.action_id, revision=permit.revision
            )
            now = database_now(session)
            self._require_reconciliation_authority(ledger, permit, worker_id, now)
            if revision.state in TERMINAL_EXECUTION_STATES:
                session.commit()
                return revision.state
            if revision.state not in RECONCILE_SOURCE_STATES:
                session.commit()
                return revision.state
            from_state = revision.state
            if from_state != WorkflowState.RECONCILING.value:
                revision.state = WorkflowState.RECONCILING.value
                ledger.status = ExecutionLedgerStatus.RECONCILING.value
                self._audits.insert(
                    session,
                    NewAuditEvent(
                        action_id=permit.action_id,
                        event_type=AUDIT_RECONCILIATION_STARTED,
                        actor_type=ActorType.WORKER,
                        actor_subject_id=worker_id,
                        from_state=from_state,
                        to_state=WorkflowState.RECONCILING.value,
                        safe_metadata={"lease_generation": ledger.lease_generation},
                        revision=permit.revision,
                    ),
                )
            acquire_business_request_lock(session, revision.business_request_key)
            existing = self._find_existing_leave(
                session, permit.execution_key, revision.business_request_key
            )
            if existing is None:
                self._raise_after_absence_observed()
                existing = self._find_existing_leave(
                    session, permit.execution_key, revision.business_request_key
                )
            if existing is not None:
                resolution = (
                    ResolutionKind.CREATED
                    if existing.execution_key == permit.execution_key
                    and existing.source_action_id == permit.action_id
                    else ResolutionKind.ADOPTED_EXISTING
                )
                result = ExecutorResult(
                    BusinessOutcome.APPLIED,
                    leave_request_id=existing.leave_request_id,
                    execution_key=existing.execution_key,
                    business_request_key=existing.business_request_key,
                    resolution=resolution.value,
                )
                self._apply_terminal(
                    session,
                    revision,
                    ledger,
                    permit,
                    now=now,
                    to_state=WorkflowState.SUCCEEDED,
                    ledger_status=ExecutionLedgerStatus.COMPLETED,
                    event_type=AUDIT_EXECUTION_SUCCEEDED,
                    result=result,
                    recon_event=AUDIT_RECONCILIATION_SUCCEEDED,
                )
            else:
                result = ExecutorResult(
                    BusinessOutcome.DEFINITELY_NOT_APPLIED,
                    failure_kind="authoritatively_absent",
                    execution_key=permit.execution_key,
                    business_request_key=revision.business_request_key,
                )
                self._apply_terminal(
                    session,
                    revision,
                    ledger,
                    permit,
                    now=now,
                    to_state=WorkflowState.EXECUTION_FAILED,
                    ledger_status=ExecutionLedgerStatus.FAILED,
                    event_type=AUDIT_EXECUTION_FAILED,
                    failure_kind="authoritatively_absent",
                    result=result,
                    recon_event=AUDIT_RECONCILIATION_ABSENT,
                )
            session.commit()
            return revision.state

    def begin_reconciliation(self, permit: ExecutionPermit, worker_id: str) -> ExecutionPermit:
        with self._session_factory() as session:
            self._workflows.lock_workflow(session, permit.action_id)
            revision = self._workflows.lock_revision(
                session, action_id=permit.action_id, revision=permit.revision
            )
            ledger = self._ledger.lock_reservation(
                session, action_id=permit.action_id, revision=permit.revision
            )
            now = database_now(session)
            self._require_reconciliation_authority(ledger, permit, worker_id, now)
            if revision.state in TERMINAL_EXECUTION_STATES:
                session.commit()
                return permit_for_caller(ledger, worker_id)
            from_state = revision.state
            revision.state = WorkflowState.RECONCILING.value
            ledger.status = ExecutionLedgerStatus.RECONCILING.value
            self._audits.insert(
                session,
                NewAuditEvent(
                    action_id=permit.action_id,
                    event_type=AUDIT_RECONCILIATION_STARTED,
                    actor_type=ActorType.WORKER,
                    actor_subject_id=worker_id,
                    from_state=from_state,
                    to_state=WorkflowState.RECONCILING.value,
                    safe_metadata={"lease_generation": ledger.lease_generation},
                    revision=permit.revision,
                ),
            )
            session.commit()
            return permit_for_caller(ledger, worker_id)

    def _require_reconciliation_authority(
        self,
        ledger,
        permit: ExecutionPermit,
        worker_id: str,
        now,
    ) -> None:
        if ledger.execution_key != permit.execution_key:
            raise WorkflowIntegrityError("reconciliation execution_key mismatch")
        if ledger.lease_owner_id != worker_id or permit.lease_owner_id != worker_id:
            raise ExecutionFenceError("caller does not own the execution lease")
        if ledger.lease_generation != permit.lease_generation:
            raise ExecutionFenceError("caller lease generation is stale")
        if ledger.lease_expires_at is None or ledger.lease_expires_at <= now:
            raise ExecutionFenceError("execution lease has expired")

    def _find_existing_leave(self, session: Session, execution_key: str, business_request_key: str):
        found = self._leave_queries.find_by_execution_key(session, execution_key)
        if found is not None:
            return found
        return self._leave_queries.find_by_business_request_key(session, business_request_key)

    def _apply_terminal(
        self,
        session: Session,
        revision,
        ledger,
        permit: ExecutionPermit,
        *,
        now,
        to_state: WorkflowState,
        ledger_status: ExecutionLedgerStatus,
        event_type: str,
        failure_kind: str | None = None,
        result: ExecutorResult | None = None,
        recon_event: str | None = None,
    ) -> None:
        from_state = revision.state
        revision.state = to_state.value
        ledger.status = ledger_status.value
        ledger.completed_at = now
        ledger.failure_kind = failure_kind
        metadata = {"lease_generation": permit.lease_generation}
        if result is not None:
            if result.resolution is not None:
                metadata["resolution"] = result.resolution
            if result.leave_request_id is not None:
                metadata["result_reference"] = str(result.leave_request_id)
            if result.execution_key is not None:
                metadata["result_execution_key"] = result.execution_key
            if result.business_request_key is not None:
                metadata["business_request_key"] = result.business_request_key
        self._audits.insert(
            session,
            NewAuditEvent(
                action_id=permit.action_id,
                event_type=event_type,
                actor_type=ActorType.WORKER,
                actor_subject_id=permit.lease_owner_id,
                from_state=from_state,
                to_state=to_state.value,
                safe_metadata=metadata,
                revision=permit.revision,
            ),
        )
        if recon_event is not None:
            self._audits.insert(
                session,
                NewAuditEvent(
                    action_id=permit.action_id,
                    event_type=recon_event,
                    actor_type=ActorType.WORKER,
                    actor_subject_id=permit.lease_owner_id,
                    from_state=from_state,
                    to_state=to_state.value,
                    safe_metadata=metadata,
                    revision=permit.revision,
                ),
            )

    def _apply_unknown(
        self, session: Session, revision, ledger, permit: ExecutionPermit, now
    ) -> None:
        from_state = revision.state
        revision.state = WorkflowState.UNKNOWN_OUTCOME.value
        ledger.status = ExecutionLedgerStatus.UNKNOWN.value
        self._audits.insert(
            session,
            NewAuditEvent(
                action_id=permit.action_id,
                event_type=AUDIT_EXECUTION_OUTCOME_UNKNOWN,
                actor_type=ActorType.WORKER,
                actor_subject_id=permit.lease_owner_id,
                from_state=from_state,
                to_state=WorkflowState.UNKNOWN_OUTCOME.value,
                safe_metadata={"lease_generation": permit.lease_generation},
                revision=permit.revision,
            ),
        )
        if from_state == WorkflowState.RECONCILING.value:
            self._audits.insert(
                session,
                NewAuditEvent(
                    action_id=permit.action_id,
                    event_type=AUDIT_RECONCILIATION_UNKNOWN,
                    actor_type=ActorType.WORKER,
                    actor_subject_id=permit.lease_owner_id,
                    from_state=from_state,
                    to_state=WorkflowState.UNKNOWN_OUTCOME.value,
                    safe_metadata={"lease_generation": permit.lease_generation},
                    revision=permit.revision,
                ),
            )
        self._schedule_reconciliation(session, revision, ledger, permit, now)

    def _audit_authority_lost(
        self,
        session: Session,
        permit: ExecutionPermit,
        revision,
        result: ExecutorResult,
    ) -> None:
        self._audits.insert(
            session,
            NewAuditEvent(
                action_id=permit.action_id,
                event_type=AUDIT_EXECUTION_AUTHORITY_LOST,
                actor_type=ActorType.WORKER,
                actor_subject_id=permit.lease_owner_id,
                from_state=revision.state,
                to_state=revision.state,
                safe_metadata={
                    "lease_generation": permit.lease_generation,
                    "failure_kind": result.failure_kind,
                },
                revision=permit.revision,
            ),
        )

    def _schedule_reconciliation(
        self,
        session: Session,
        revision,
        ledger,
        permit: ExecutionPermit,
        now,
    ) -> None:
        next_attempt = ledger.reconciliation_attempt_count + 1
        if next_attempt > MAX_AUTOMATIC_RECONCILIATION_ATTEMPTS:
            revision.manual_review_required = True
            ledger.manual_review_required = True
            return
        ledger.reconciliation_attempt_count = next_attempt
        delay = min(RECONCILE_BACKOFF_CAP_SECONDS, 2 ** min(next_attempt, 5))
        try:
            self._outbox.enqueue(
                session,
                NewOutboxEvent(
                    event_key=reconciliation_outbox_event_key(
                        permit.action_id,
                        permit.revision,
                        permit.execution_key,
                        next_attempt,
                    ),
                    action_id=permit.action_id,
                    event_type=OutboxEventType.RECONCILE_REQUESTED,
                    available_at=now + timedelta(seconds=delay),
                    revision=permit.revision,
                ),
            )
        except DuplicateWorkflowEventError:
            return

    def _raise_after_absence_observed(self) -> None:
        if self._failpoints is None or self._failpoints.after_absence_observed is None:
            return
        self._failpoints.after_absence_observed()

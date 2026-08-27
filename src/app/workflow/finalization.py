"""T5 execution finalization and durable reconciliation scheduling."""

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
from app.workflow.errors import DuplicateWorkflowEventError, WorkflowIntegrityError
from app.workflow.execution import ExecutionPermit, permit_from_ledger
from app.workflow.execution_repository import ExecutionLedgerRepository
from app.workflow.executor import BusinessOutcome, ExecutorResult
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

MAX_AUTOMATIC_RECONCILIATION_ATTEMPTS = 3
RECONCILE_BACKOFF_CAP_SECONDS = 60
TERMINAL_EXECUTION_STATES = frozenset(
    {
        WorkflowState.SUCCEEDED.value,
        WorkflowState.EXECUTION_FAILED.value,
    }
)


def reconciliation_outbox_event_key(
    action_id: UUID,
    revision: int,
    execution_key: str,
    attempt: int,
) -> str:
    return f"reconcile_requested:{action_id}:{revision}:{execution_key}:{attempt}"


class ExecutionFinalizationService:
    """Apply proven executor results to the authoritative revision and ledger."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: KnowledgeSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or load_knowledge_settings()
        self._workflows = WorkflowRepository()
        self._ledger = ExecutionLedgerRepository()
        self._outbox = OutboxRepository()
        self._audits = AuditRepository()

    def finalize(self, permit: ExecutionPermit, result: ExecutorResult) -> str:
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
            if ledger.lease_generation != permit.lease_generation:
                raise WorkflowIntegrityError("finalization lease_generation mismatch")
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
                    recon_event=AUDIT_RECONCILIATION_SUCCEEDED
                    if revision.state
                    in {
                        WorkflowState.RECONCILING.value,
                        WorkflowState.UNKNOWN_OUTCOME.value,
                    }
                    else None,
                )
            elif result.outcome is BusinessOutcome.DEFINITELY_NOT_APPLIED:
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

    def begin_reconciliation(self, permit: ExecutionPermit, worker_id: str) -> ExecutionPermit:
        with self._session_factory() as session:
            self._workflows.lock_workflow(session, permit.action_id)
            revision = self._workflows.lock_revision(
                session, action_id=permit.action_id, revision=permit.revision
            )
            ledger = self._ledger.lock_reservation(
                session, action_id=permit.action_id, revision=permit.revision
            )
            if ledger.execution_key != permit.execution_key:
                raise WorkflowIntegrityError("reconciliation execution_key mismatch")
            if revision.state in TERMINAL_EXECUTION_STATES:
                session.commit()
                return permit_from_ledger(ledger)
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
            return permit_from_ledger(ledger)

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
        recon_event: str | None = None,
    ) -> None:
        from_state = revision.state
        revision.state = to_state.value
        ledger.status = ledger_status.value
        ledger.completed_at = now
        ledger.failure_kind = failure_kind
        self._audits.insert(
            session,
            NewAuditEvent(
                action_id=permit.action_id,
                event_type=event_type,
                actor_type=ActorType.WORKER,
                actor_subject_id=permit.lease_owner_id,
                from_state=from_state,
                to_state=to_state.value,
                safe_metadata={"lease_generation": permit.lease_generation},
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
                    safe_metadata={"lease_generation": permit.lease_generation},
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

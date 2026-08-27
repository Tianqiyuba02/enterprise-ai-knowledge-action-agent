"""Durable outbox worker for confirmation wakes and execution recovery."""

import time
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.workflow_models import WorkflowOutbox
from app.workflow.audit_repository import AuditRepository, NewAuditEvent
from app.workflow.confirmation import ConfirmationService
from app.workflow.domain import ActorType, OutboxEventType, WorkflowState
from app.workflow.errors import (
    OrchestrationAuthorityError,
    ThreadBindingError,
    WorkflowIntegrityError,
    WorkflowInvariantError,
)
from app.workflow.orchestration import WorkflowOrchestrationService
from app.workflow.outbox_repository import OutboxRepository
from app.workflow.time import database_now

AUDIT_OUTBOX_DELIVERED = "OUTBOX_DELIVERED"
AUDIT_OUTBOX_WAKE_FAILED = "OUTBOX_WAKE_FAILED"
SETTLED_WAKE_STATES = frozenset(
    {
        WorkflowState.SUCCEEDED.value,
        WorkflowState.EXECUTION_FAILED.value,
        WorkflowState.UNKNOWN_OUTCOME.value,
        WorkflowState.CANCELLED.value,
        WorkflowState.EXPIRED.value,
        WorkflowState.STALE.value,
    }
)
WAKE_BACKOFF_CAP_SECONDS = 60
RECONCILE_BACKOFF_CAP_SECONDS = 60


@dataclass(frozen=True, slots=True)
class ClaimedWake:
    event_id: UUID
    event_key: str
    action_id: UUID
    revision: int
    event_type: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class WorkerResult:
    event_id: UUID
    action_id: UUID
    observed_state: str
    delivered: bool


class WorkflowWorker:
    """Claim outbox rows and advance the persisted LangGraph thread as this worker."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: KnowledgeSettings | None = None,
        *,
        worker_id: str | None = None,
        lock_for: timedelta | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or load_knowledge_settings()
        self.worker_id = worker_id or f"workflow-worker:{uuid4()}"
        self.lock_for = lock_for or timedelta(seconds=30)
        self._outbox = OutboxRepository()
        self._audits = AuditRepository()
        self._confirmation = ConfirmationService(session_factory, self._settings)
        self._orchestration = WorkflowOrchestrationService(session_factory)

    def run_once(self) -> WorkerResult | None:
        claimed = self.claim_one()
        if claimed is None:
            return None
        return self.deliver(claimed, mark_delivered=True)

    def run_loop(self, *, poll_seconds: float = 1.0, once: bool = False) -> None:
        while True:
            self.run_once()
            if once:
                return
            time.sleep(poll_seconds)

    def claim_one(self) -> ClaimedWake | None:
        with self._session_factory() as session:
            now = database_now(session)
            rows = self._outbox.claim_ready(
                session,
                now=now,
                locked_by=self.worker_id,
                lock_for=self.lock_for,
            )
            session.commit()
            if not rows:
                return None
            return _claimed(rows[0])

    def deliver(self, claimed: ClaimedWake, *, mark_delivered: bool) -> WorkerResult:
        try:
            observed = self._wake(claimed)
            should_deliver = mark_delivered and observed in SETTLED_WAKE_STATES
            if should_deliver:
                self._mark_delivered(claimed)
            elif mark_delivered and observed == WorkflowState.CONFIRMED.value:
                self._release(claimed, failure_kind="pending_unresolved")
            elif mark_delivered:
                self._release(claimed, failure_kind="pending_execution")
            return WorkerResult(
                event_id=claimed.event_id,
                action_id=claimed.action_id,
                observed_state=observed,
                delivered=should_deliver,
            )
        except (
            OrchestrationAuthorityError,
            ThreadBindingError,
            WorkflowInvariantError,
            WorkflowIntegrityError,
        ) as exc:
            self._release(claimed, failure_kind=_failure_kind(exc))
            raise

    def _wake(self, claimed: ClaimedWake) -> str:
        if claimed.event_type not in {
            OutboxEventType.CONFIRMATION_COMMITTED.value,
            OutboxEventType.RECONCILE_REQUESTED.value,
        }:
            raise WorkflowInvariantError("unsupported outbox event type")
        view = self._confirmation.normalize_expiry(action_id=claimed.action_id)
        if (
            claimed.event_type == OutboxEventType.CONFIRMATION_COMMITTED.value
            and view.state == WorkflowState.AWAITING_CONFIRMATION.value
        ):
            raise WorkflowInvariantError("ACTION_CONFIRMED observed AWAITING_CONFIRMATION")
        result = self._orchestration.resume_internal(
            action_id=claimed.action_id,
            settings=self._settings,
            worker_id=self.worker_id,
        )
        observed = str(result.get("observed_state") or "")
        if claimed.event_type == OutboxEventType.CONFIRMATION_COMMITTED.value and (
            observed == WorkflowState.AWAITING_CONFIRMATION.value or result.get("__interrupt__")
        ):
            raise WorkflowInvariantError("ACTION_CONFIRMED wake remained awaiting")
        return observed

    def _mark_delivered(self, claimed: ClaimedWake) -> None:
        with self._session_factory() as session:
            now = database_now(session)
            self._outbox.mark_delivered(session, claimed.event_id, delivered_at=now)
            self._audits.insert(
                session,
                NewAuditEvent(
                    action_id=claimed.action_id,
                    event_type=AUDIT_OUTBOX_DELIVERED,
                    actor_type=ActorType.WORKER,
                    actor_subject_id=self.worker_id,
                    safe_metadata={"event_key": claimed.event_key},
                    revision=claimed.revision,
                ),
            )
            session.commit()

    def _release(self, claimed: ClaimedWake, *, failure_kind: str) -> None:
        with self._session_factory() as session:
            now = database_now(session)
            cap = (
                RECONCILE_BACKOFF_CAP_SECONDS
                if claimed.event_type == OutboxEventType.RECONCILE_REQUESTED.value
                else WAKE_BACKOFF_CAP_SECONDS
            )
            delay = min(cap, 2 ** min(claimed.attempt_count, 5))
            self._outbox.release(
                session,
                claimed.event_id,
                failure_kind=failure_kind,
                available_at=now + timedelta(seconds=delay),
            )
            self._audits.insert(
                session,
                NewAuditEvent(
                    action_id=claimed.action_id,
                    event_type=AUDIT_OUTBOX_WAKE_FAILED,
                    actor_type=ActorType.WORKER,
                    actor_subject_id=self.worker_id,
                    safe_metadata={"reason": failure_kind},
                    revision=claimed.revision,
                ),
            )
            session.commit()


def _claimed(row: WorkflowOutbox) -> ClaimedWake:
    return ClaimedWake(
        event_id=row.event_id,
        event_key=row.event_key,
        action_id=row.action_id,
        revision=row.revision,
        event_type=row.event_type,
        attempt_count=row.attempt_count,
    )


def _failure_kind(exc: Exception) -> str:
    if isinstance(exc, WorkflowInvariantError):
        return "invariant_failure"
    if isinstance(exc, OrchestrationAuthorityError):
        return "checkpoint_failure"
    if isinstance(exc, WorkflowIntegrityError):
        return "integrity_failure"
    return "orchestration_failure"

"""Worker-owned execution port used by the durable graph. No provider calls."""

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings
from app.workflow.domain import WorkflowState
from app.workflow.errors import ExecutionFenceError, WorkflowRowNotFoundError
from app.workflow.execution import ExecutionReservationService, ReservationOutcome
from app.workflow.executor import LeaveSubmissionExecutor
from app.workflow.finalization import ExecutionFinalizationService
from app.workflow.workflow_repository import WorkflowRepository


class WorkflowExecutionRuntime:
    """Reload-and-execute helpers for graph nodes. PostgreSQL remains authority."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: KnowledgeSettings | None = None,
        *,
        worker_id: str,
        executor: LeaveSubmissionExecutor | None = None,
        finalization: ExecutionFinalizationService | None = None,
    ) -> None:
        if not worker_id or not worker_id.strip():
            raise ValueError("worker_id is required for execution runtime")
        self._session_factory = session_factory
        self._settings = settings or load_knowledge_settings()
        self.worker_id = worker_id
        self._workflows = WorkflowRepository()
        self._reservation = ExecutionReservationService(session_factory, self._settings)
        self._executor = executor or LeaveSubmissionExecutor(session_factory, self._settings)
        self._finalization = finalization or ExecutionFinalizationService(
            session_factory, self._settings
        )

    def reserve(self, action_id: str, revision: int) -> ReservationOutcome:
        result = self._reservation.reserve(
            action_id=UUID(action_id),
            revision=revision,
            worker_id=self.worker_id,
        )
        return result.outcome

    def execute(self, action_id: str, revision: int) -> str:
        state = self._current_state(action_id, revision)
        if state != WorkflowState.EXECUTING.value:
            return state
        try:
            permit = self._reservation.reload_permit(
                action_id=UUID(action_id),
                revision=revision,
                worker_id=self.worker_id,
            )
        except (ExecutionFenceError, WorkflowRowNotFoundError):
            return self._current_state(action_id, revision)
        result = self._executor.submit(permit)
        if result.authority_lost:
            return self._current_state(action_id, revision)
        return self._finalization.finalize(permit, result)

    def reconcile(self, action_id: str, revision: int) -> str:
        state = self._current_state(action_id, revision)
        if state in {
            WorkflowState.SUCCEEDED.value,
            WorkflowState.EXECUTION_FAILED.value,
            WorkflowState.CANCELLED.value,
            WorkflowState.EXPIRED.value,
            WorkflowState.STALE.value,
        }:
            return state
        action_uuid = UUID(action_id)
        permit = self._recover_permit(action_uuid, revision)
        if permit is None:
            return self._current_state(action_id, revision)
        try:
            return self._finalization.classify_and_finalize(permit, self.worker_id)
        except ExecutionFenceError:
            return self._current_state(action_id, revision)

    def finalize(self, action_id: str, revision: int) -> str:
        """Persist nothing. Execute and reconcile already wrote their outcomes."""

        return self._current_state(action_id, revision)

    def _recover_permit(self, action_id: UUID, revision: int):
        try:
            return self._reservation.takeover_expired_lease(
                action_id=action_id,
                revision=revision,
                worker_id=self.worker_id,
            )
        except (WorkflowRowNotFoundError, ExecutionFenceError):
            return None

    def _current_state(self, action_id: str, revision: int) -> str:
        with self._session_factory() as session:
            row = self._workflows.get_revision(session, UUID(action_id), revision)
            if row is None:
                return ""
            return row.state

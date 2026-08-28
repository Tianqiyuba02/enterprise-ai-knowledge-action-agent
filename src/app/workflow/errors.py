"""Controlled V4 workflow persistence and primitive failures."""


class WorkflowError(RuntimeError):
    """Base class for deterministic V4 workflow foundation failures."""


class DuplicateWorkflowEventError(WorkflowError):
    """Raised when an outbox event identity already exists."""


class DuplicateExecutionReservationError(WorkflowError):
    """Raised when an execution reservation or execution_key already exists."""


class WorkflowRowNotFoundError(WorkflowError):
    """Raised when a locked or owner-scoped workflow row is absent."""


class WorkflowOwnershipError(WorkflowError):
    """Raised when a trusted owner cannot access the requested action."""


class ThreadBindingError(WorkflowError):
    """Raised when a caller-supplied thread_id does not match the stored binding."""


class OrchestrationAuthorityError(WorkflowError):
    """Raised when checkpoint loss cannot be used to guess workflow authority."""


class WorkflowInvariantError(WorkflowError):
    """Raised when durable scheduling contradicts authoritative PostgreSQL state."""


class WorkflowIntegrityError(WorkflowError):
    """Raised when stored workflow authority is internally inconsistent."""


class ExecutionFenceError(WorkflowError):
    """Raised when a stale or unauthorized worker may not mutate business state."""


class ActionCreationError(WorkflowError):
    """Base class for deterministic V4 action-creation failures."""

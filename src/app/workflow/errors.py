"""Controlled V4 workflow persistence and primitive failures."""


class WorkflowError(RuntimeError):
    """Base class for deterministic V4 workflow foundation failures."""


class DuplicateWorkflowEventError(WorkflowError):
    """Raised when an outbox event identity already exists."""


class DuplicateExecutionReservationError(WorkflowError):
    """Raised when an execution reservation or execution_key already exists."""


class WorkflowRowNotFoundError(WorkflowError):
    """Raised when a locked or owner-scoped workflow row is absent."""

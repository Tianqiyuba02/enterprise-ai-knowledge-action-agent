"""Controlled V4 workflow persistence and primitive failures."""


class WorkflowError(RuntimeError):
    """Base class for deterministic V4 workflow foundation failures."""


class WorkflowRowNotFoundError(WorkflowError):
    """Raised when a locked or owner-scoped workflow row is absent."""


class WorkflowOwnershipError(WorkflowError):
    """Raised when a trusted owner cannot access the requested action."""


class WorkflowInvariantError(WorkflowError):
    """Raised when durable scheduling contradicts authoritative PostgreSQL state."""


class WorkflowIntegrityError(WorkflowError):
    """Raised when stored workflow authority is internally inconsistent."""


class ActionCreationError(WorkflowError):
    """Base class for deterministic V4 action-creation failures."""

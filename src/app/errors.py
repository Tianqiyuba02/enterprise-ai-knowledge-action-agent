"""Application-layer failures with stable public meanings."""

from typing import ClassVar


class ApplicationError(RuntimeError):
    """Base class for deterministic application failures."""

    error_code: ClassVar[str] = "application_error"
    public_message: ClassVar[str] = "The request could not be completed."

    def __init__(self) -> None:
        super().__init__(self.public_message)


class InvalidDemoSessionError(ApplicationError):
    """Raised when the trusted demo-session dependency cannot resolve identity."""

    error_code = "invalid_demo_session"
    public_message = "A valid demo session is required."


class EmployeeNotFoundError(ApplicationError):
    """Raised when the authenticated demo employee has no seeded profile."""

    error_code = "employee_not_found"
    public_message = "The employee profile was not found."


class TicketNotFoundError(ApplicationError):
    """Raised for nonexistent and non-owned tickets without distinguishing them."""

    error_code = "ticket_not_found"
    public_message = "The requested ticket was not found."


class ActionNotFoundError(ApplicationError):
    """Raised for nonexistent and non-owned actions without distinguishing them."""

    error_code = "action_not_found"
    public_message = "The requested action was not found."


class ConfirmationInvalidError(ApplicationError):
    """Raised for generic confirmation failures that must not leak token detail."""

    error_code = "confirmation_invalid"
    public_message = "The confirmation request is invalid."


class ActionConflictError(ApplicationError):
    """Raised when the current action or challenge state rejects the request."""

    error_code = "action_conflict"
    public_message = "The action cannot be changed in its current state."

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

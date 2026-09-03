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


class ActionCreationIdentityError(ApplicationError):
    """Raised when trusted V4 identity bindings are incomplete at the action boundary."""

    error_code = "action_identity_incomplete"
    public_message = "A complete trusted identity is required to create an action."


class PortalReadUnavailableError(ApplicationError):
    """Raised when an owner-scoped portal projection cannot query PostgreSQL."""

    error_code = "portal_read_unavailable"
    public_message = "The employee portal data is temporarily unavailable."


class PolicyDocumentNotFoundError(ApplicationError):
    """Raised when a policy revision is absent or inapplicable to the current employee."""

    error_code = "policy_document_not_found"
    public_message = "The requested policy document was not found."


class DemoCapacityReachedError(ApplicationError):
    """Raised when an atomic public-demo allowance cannot be reserved."""

    error_code = "demo_capacity_reached"
    public_message = "The public demo has reached its current usage limit. Please try again later."


class DemoMaintenanceError(ApplicationError):
    """Raised while the private deterministic reset owns the mutation boundary."""

    error_code = "demo_maintenance"
    public_message = "The demo is refreshing its sample data. Please try again shortly."

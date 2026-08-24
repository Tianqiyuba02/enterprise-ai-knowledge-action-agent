"""Trusted identity values passed from the HTTP dependency to application services."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthenticatedEmployeeContext:
    """Server-resolved employee identity; never constructed from request bodies."""

    employee_id: str

"""Canonical exact business-result equivalence for cutover and runtime adoption.

Compares every mutation-relevant persisted leave field. Conversational fields
that never enter the leave_requests mutation are not compared.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.workflow.canonical import quantize_hours
from app.workflow.domain import LeaveRequestStatus


def _get(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row[name]
    return getattr(row, name)


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        raise TypeError("datetime is not allowed as a leave date")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError("unsupported leave date value")


def _as_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _reason(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def leaves_trusted_equivalent(
    *,
    employee_id: str,
    action_id: UUID,
    revision: int,
    leave_type: str,
    start_date: date,
    end_date: date,
    requested_hours: Decimal,
    business_request_key: str,
    reason: str | None,
    calendar_version: str,
    ruleset_version: str,
    leave: Any,
) -> bool:
    """Return True only when the persisted leave is an exact business-result match."""

    try:
        return (
            str(_get(leave, "employee_id")) == employee_id
            and _as_uuid(_get(leave, "source_action_id")) == action_id
            and int(_get(leave, "source_action_revision")) == revision
            and str(_get(leave, "leave_type")) == leave_type
            and _as_date(_get(leave, "start_date")) == start_date
            and _as_date(_get(leave, "end_date")) == end_date
            and quantize_hours(_get(leave, "requested_hours")) == quantize_hours(requested_hours)
            and str(_get(leave, "business_request_key")) == business_request_key
            and _reason(_get(leave, "reason")) == _reason(reason)
            and str(_get(leave, "calendar_version")) == calendar_version
            and str(_get(leave, "ruleset_version")) == ruleset_version
            and str(_get(leave, "status")) == LeaveRequestStatus.SUBMITTED.value
        )
    except (KeyError, AttributeError, TypeError, ValueError):
        return False

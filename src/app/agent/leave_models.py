"""Strict annual-leave preparation arguments and non-executing draft models."""

import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

ReasonText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500, strict=True),
]


class PrepareLeaveRequestArguments(BaseModel):
    """Provider-facing annual-only ISO date contract."""

    model_config = ConfigDict(extra="forbid", strict=True)

    leave_type: Literal["annual"]
    start_date: date
    end_date: date
    reason: ReasonText | None = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def parse_strict_iso_date(cls, value: object) -> date:
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
            raise ValueError("leave dates must be strict ISO YYYY-MM-DD strings")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("leave date is not a valid calendar date") from exc

    @model_validator(mode="after")
    def validate_date_range(self) -> Self:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if (self.end_date - self.start_date).days + 1 > 31:
            raise ValueError("leave range must not exceed 31 calendar days")
        return self


class LeavePreparationStatus(StrEnum):
    READY = "ready"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    NO_SCHEDULED_WORKDAYS = "no_scheduled_workdays"


class LeaveRequestDraft(BaseModel):
    """Trusted calculation result with structurally explicit non-execution."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    leave_type: Literal["annual"]
    start_date: date
    end_date: date
    scheduled_work_days: Annotated[int, Field(ge=0)]
    requested_hours: Annotated[Decimal, Field(ge=Decimal("0"))]
    current_balance_hours: Annotated[Decimal, Field(ge=Decimal("0"))]
    projected_balance_hours: Decimal
    preparation_status: LeavePreparationStatus
    reason: ReasonText | None = None
    public_holiday_check_required: bool
    non_executing: Literal[True] = True

    @field_serializer(
        "requested_hours",
        "current_balance_hours",
        "projected_balance_hours",
        when_used="json",
    )
    def serialize_decimal_hours(self, value: Decimal) -> float:
        return float(value)

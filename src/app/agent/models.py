"""Strict provider-call and untrusted tool-result models for V3 read dispatch."""

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.agent.leave_models import LeaveRequestDraft
from app.api.knowledge_models import KnowledgeCitation
from app.grounding.models import KnowledgeAnswerStatus
from app.it.domain import PreparedITSupportTicket

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]
ToolNameString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64, strict=True),
]
QuestionString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000, strict=True),
]
KnowledgeAnswerString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000, strict=True),
]
TicketId = Annotated[
    str,
    StringConstraints(pattern=r"^TKT-[0-9]+$", strict=True),
]


class StrictToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NoToolArguments(StrictToolModel):
    pass


class KnowledgeQueryArguments(StrictToolModel):
    question: QuestionString


class GetMyTicketArguments(StrictToolModel):
    ticket_id: TicketId

    @field_validator("ticket_id")
    @classmethod
    def require_full_ticket_id_match(cls, value: str) -> str:
        if re.fullmatch(r"TKT-[0-9]+", value) is None:
            raise ValueError("ticket_id must exactly match TKT-<digits>")
        return value


class ProviderToolRequest(StrictToolModel):
    """Provider-neutral call data; provider IDs and SDK objects are deliberately absent."""

    name: ToolNameString
    arguments: dict[str, Any]


class ToolResultStatus(StrEnum):
    SUCCESS = "success"
    INVALID_ARGUMENTS = "invalid_arguments"
    NOT_FOUND_OR_INACCESSIBLE = "not_found_or_inaccessible"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INTERNAL_ERROR = "internal_error"


class ProfileToolData(StrictToolModel):
    kind: Literal["profile"] = "profile"
    full_name: NonEmptyString
    work_email: NonEmptyString
    location: NonEmptyString
    employment_type: NonEmptyString
    hours_per_day: Annotated[float, Field(gt=0.0, le=24.0)]
    work_days: tuple[NonEmptyString, ...]
    timezone: NonEmptyString
    is_active: bool


class LeaveBalanceToolItem(StrictToolModel):
    leave_type: NonEmptyString
    balance_hours: Annotated[float, Field(ge=0.0)]
    as_of_date: date


class LeaveBalancesToolData(StrictToolModel):
    kind: Literal["leave_balances"] = "leave_balances"
    balances: tuple[LeaveBalanceToolItem, ...]


class TicketToolData(StrictToolModel):
    kind: Literal["ticket"] = "ticket"
    ticket_id: TicketId
    category: NonEmptyString
    summary: NonEmptyString
    description: NonEmptyString
    urgency: NonEmptyString
    status: NonEmptyString
    created_at: datetime
    updated_at: datetime


class KnowledgeToolData(StrictToolModel):
    kind: Literal["knowledge"] = "knowledge"
    status: KnowledgeAnswerStatus
    answer: KnowledgeAnswerString
    citations: tuple[KnowledgeCitation, ...]


class PreparedLeaveRequestToolData(StrictToolModel):
    kind: Literal["prepared_leave_request"] = "prepared_leave_request"
    draft: LeaveRequestDraft


class PreparedITSupportTicketToolData(StrictToolModel):
    kind: Literal["prepared_it_support_ticket"] = "prepared_it_support_ticket"
    draft: PreparedITSupportTicket


ToolData = Annotated[
    ProfileToolData
    | LeaveBalancesToolData
    | TicketToolData
    | KnowledgeToolData
    | PreparedLeaveRequestToolData
    | PreparedITSupportTicketToolData,
    Field(discriminator="kind"),
]


class ToolResult(StrictToolModel):
    """Bounded data envelope safe to serialize into a later agent transcript."""

    tool_name: ToolNameString
    status: ToolResultStatus
    data: ToolData | None = None
    safe_message: str | None = None
    untrusted_data: Literal[True] = True

    @model_validator(mode="after")
    def validate_result_shape(self) -> Self:
        if self.status is ToolResultStatus.SUCCESS and self.data is None:
            raise ValueError("successful tool result requires typed data")
        if self.status is not ToolResultStatus.SUCCESS and self.data is not None:
            raise ValueError("failed tool result must not include data")
        return self

    @classmethod
    def success(cls, tool_name: str, data: ToolData) -> Self:
        return cls(tool_name=tool_name, status=ToolResultStatus.SUCCESS, data=data)

    @classmethod
    def failure(
        cls,
        tool_name: str,
        status: ToolResultStatus,
        message: str,
    ) -> Self:
        return cls(tool_name=tool_name, status=status, safe_message=message)

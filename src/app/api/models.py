"""Typed V1 HTTP request, response, and error contracts."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

from app.api.portal_models import AuthoritativeActionDraft
from app.it.domain import ITTicketCategory, ITTicketStatus, ITTicketUrgency
from app.llm.models import QuestionAnalysis, QuestionCategory


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttributeAPIModel(APIModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class HealthResponse(APIModel):
    status: Literal["ok"] = "ok"
    service: Literal["enterprise-ai-knowledge-action-agent"] = (
        "enterprise-ai-knowledge-action-agent"
    )
    milestone: Literal["V1"] = "V1"


class ChatRequest(APIModel):
    question: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000, strict=True),
    ]


class ChatResponse(APIModel):
    category: QuestionCategory
    summary: str
    requires_action: StrictBool
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]

    @classmethod
    def from_analysis(cls, analysis: QuestionAnalysis) -> Self:
        return cls.model_validate(analysis.model_dump())


class EmployeeProfileResponse(AttributeAPIModel):
    employee_id: str
    full_name: str
    work_email: str
    location: str
    employment_type: str
    hours_per_day: Annotated[float, Field(gt=0.0, le=24.0)]
    work_days: tuple[str, ...]
    timezone: str
    is_active: StrictBool


class LeaveType(StrEnum):
    ANNUAL = "annual"
    PERSONAL = "personal"


class LeaveBalanceResponse(AttributeAPIModel):
    leave_type: LeaveType
    balance_hours: Annotated[float, Field(ge=0.0)]
    as_of_date: date


class LeaveBalancesResponse(APIModel):
    balances: tuple[LeaveBalanceResponse, ...]


class TicketResponse(AttributeAPIModel):
    ticket_id: str
    category: ITTicketCategory
    summary: str
    description: str
    urgency: ITTicketUrgency
    status: ITTicketStatus
    created_at: datetime
    updated_at: datetime


class TicketListResponse(APIModel):
    items: tuple[TicketResponse, ...]
    total: Annotated[int, Field(ge=0)]


class ErrorResponse(APIModel):
    error_code: str
    message: str
    request_id: str


class ActionResponse(APIModel):
    action_id: str
    revision: int
    action_type: str
    state: str
    draft: AuthoritativeActionDraft
    action_expires_at: datetime
    confirmed_expires_at: datetime | None
    confirmation_required: StrictBool
    manual_review_required: StrictBool


class ConfirmActionRequest(APIModel):
    challenge_id: UUID
    confirmation_token: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=256, strict=True),
    ]


class ConfirmationChallengeResponse(APIModel):
    challenge_id: str
    confirmation_token: str
    expires_at: datetime
    action_id: str
    revision: int
    action: ActionResponse

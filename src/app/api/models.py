"""Typed V1 HTTP request, response, and error contracts."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

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


class TicketCategory(StrEnum):
    ACCESS = "access"
    HARDWARE = "hardware"
    SOFTWARE = "software"
    NETWORK = "network"


class TicketUrgency(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class TicketResponse(AttributeAPIModel):
    ticket_id: str
    category: TicketCategory
    summary: str
    description: str
    urgency: TicketUrgency
    status: TicketStatus
    created_at: datetime
    updated_at: datetime


class ErrorResponse(APIModel):
    error_code: str
    message: str
    request_id: str

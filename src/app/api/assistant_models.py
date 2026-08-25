"""Strict public contracts and explicit mapping for the V3 assistant endpoint."""

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
    model_validator,
)

from app.agent.errors import (
    AssistantModelRateLimitedError,
    AssistantModelUnavailableError,
)
from app.agent.leave_models import LeavePreparationStatus, LeaveRequestDraft
from app.agent.loop_models import MAX_AGENT_CITATIONS, AgentRunResult, AgentRunStatus
from app.api.knowledge_models import KnowledgeCitation

StrictMessage = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000, strict=True),
]
PublicText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000, strict=True),
]


class AssistantAPIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssistantQueryRequest(AssistantAPIModel):
    message: StrictMessage


class AssistantPublicStatus(StrEnum):
    COMPLETED = "completed"
    UNABLE_TO_COMPLETE = "unable_to_complete"


class PreparedLeaveRequestAction(AssistantAPIModel):
    type: Literal["leave_request"] = "leave_request"
    leave_type: Literal["annual"]
    start_date: date
    end_date: date
    scheduled_work_days: Annotated[int, Field(ge=0)]
    requested_hours: Decimal
    current_balance_hours: Decimal
    projected_balance_hours: Decimal
    preparation_status: LeavePreparationStatus
    reason: str | None = None
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


class AssistantQueryResponse(AssistantAPIModel):
    status: AssistantPublicStatus
    answer: PublicText | None = None
    citations: tuple[KnowledgeCitation, ...] = Field(max_length=MAX_AGENT_CITATIONS)
    message: PublicText | None = None
    prepared_action: PreparedLeaveRequestAction | None = None

    @model_validator(mode="after")
    def validate_public_shape(self) -> Self:
        if self.status is AssistantPublicStatus.COMPLETED:
            if self.answer is None:
                raise ValueError("completed response requires an answer")
            if self.message is not None:
                raise ValueError("completed response must not include inability message")
        elif self.answer is not None or self.message is None:
            raise ValueError("unable response requires message and no answer")
        return self


def map_agent_result(result: AgentRunResult) -> AssistantQueryResponse:
    """Map internal orchestration state without exposing counters, calls, or protocol data."""

    if result.status is AgentRunStatus.COMPLETED:
        return AssistantQueryResponse(
            status=AssistantPublicStatus.COMPLETED,
            answer=result.answer,
            citations=result.citations,
            prepared_action=_map_prepared_action(result.prepared_leave_request),
        )
    if result.status in {
        AgentRunStatus.UNABLE_TO_COMPLETE,
        AgentRunStatus.TOOL_BUDGET_EXHAUSTED,
    }:
        return AssistantQueryResponse(
            status=AssistantPublicStatus.UNABLE_TO_COMPLETE,
            answer=None,
            citations=result.citations,
            message="The assistant could not complete the request.",
            prepared_action=_map_prepared_action(result.prepared_leave_request),
        )
    if result.status is AgentRunStatus.PROVIDER_RATE_LIMITED:
        raise AssistantModelRateLimitedError
    raise AssistantModelUnavailableError


def _map_prepared_action(
    draft: LeaveRequestDraft | None,
) -> PreparedLeaveRequestAction | None:
    if draft is None:
        return None
    return PreparedLeaveRequestAction(
        type="leave_request",
        leave_type=draft.leave_type,
        start_date=draft.start_date,
        end_date=draft.end_date,
        scheduled_work_days=draft.scheduled_work_days,
        requested_hours=draft.requested_hours,
        current_balance_hours=draft.current_balance_hours,
        projected_balance_hours=draft.projected_balance_hours,
        preparation_status=draft.preparation_status,
        reason=draft.reason,
        public_holiday_check_required=draft.public_holiday_check_required,
        non_executing=True,
    )

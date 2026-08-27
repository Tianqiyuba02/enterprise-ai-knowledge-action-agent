"""Provider-neutral internal models for one bounded V3 agent run."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.agent.leave_models import LeaveRequestDraft
from app.agent.models import ToolResult
from app.agent.provider_failures import AgentProviderFailureDetail
from app.api.knowledge_models import KnowledgeCitation

MAX_AGENT_CITATIONS = 24

AgentText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000, strict=True),
]


@dataclass(frozen=True, slots=True)
class AgentRequestedToolCall:
    """Provider-neutral request; call ID stays internal to provider continuation."""

    name: object
    arguments: object
    provider_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentToolResponse:
    """One trusted ToolResult paired with internal provider correlation data."""

    name: str
    result: ToolResult
    provider_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentModelTurn:
    """One parsed model response containing final text, tool calls, or neither."""

    final_text: str | None = None
    requested_calls: tuple[AgentRequestedToolCall, ...] = ()


class AgentRunStatus(StrEnum):
    COMPLETED = "completed"
    UNABLE_TO_COMPLETE = "unable_to_complete"
    TOOL_BUDGET_EXHAUSTED = "tool_budget_exhausted"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"


class AgentRunResult(BaseModel):
    """Strict internal result; transcript, calls, IDs, and reasoning are excluded."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: AgentRunStatus
    answer: AgentText | None = None
    citations: tuple[KnowledgeCitation, ...] = Field(max_length=MAX_AGENT_CITATIONS)
    prepared_leave_request: LeaveRequestDraft | None = None
    safe_message: str | None = None
    tool_calls_attempted: Annotated[int, Field(ge=0, le=5)]
    model_rounds: Annotated[int, Field(ge=0, le=7)]
    provider_failure: AgentProviderFailureDetail | None = None

    @model_validator(mode="after")
    def validate_status_shape(self) -> Self:
        if self.status is AgentRunStatus.COMPLETED and self.answer is None:
            raise ValueError("completed result requires final answer text")
        if self.status is not AgentRunStatus.COMPLETED and self.safe_message is None:
            raise ValueError("non-completed result requires a safe message")
        if self.provider_failure is not None and self.status not in {
            AgentRunStatus.PROVIDER_UNAVAILABLE,
            AgentRunStatus.PROVIDER_RATE_LIMITED,
        }:
            raise ValueError("provider failure detail is only valid on provider outcomes")
        return self

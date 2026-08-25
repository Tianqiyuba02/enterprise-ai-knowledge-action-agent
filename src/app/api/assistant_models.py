"""Strict public contracts and explicit mapping for the V3 assistant endpoint."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.agent.errors import (
    AssistantModelRateLimitedError,
    AssistantModelUnavailableError,
)
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


class AssistantQueryResponse(AssistantAPIModel):
    status: AssistantPublicStatus
    answer: PublicText | None = None
    citations: tuple[KnowledgeCitation, ...] = Field(max_length=MAX_AGENT_CITATIONS)
    message: PublicText | None = None

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
        )
    if result.status is AgentRunStatus.PROVIDER_RATE_LIMITED:
        raise AssistantModelRateLimitedError
    raise AssistantModelUnavailableError

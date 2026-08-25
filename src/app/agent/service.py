"""Bounded single-turn V3 read-agent orchestration over deterministic dispatch."""

from typing import Protocol

from app.agent.client import (
    AgentProviderError,
    AgentProviderRateLimitError,
    InvalidAgentProviderResponseError,
)
from app.agent.contracts import MAX_TOOL_CALLS_PER_TURN
from app.agent.dispatcher import ToolDispatcher
from app.agent.loop_models import (
    AgentModelTurn,
    AgentRunResult,
    AgentRunStatus,
    AgentToolResponse,
)
from app.agent.models import KnowledgeToolData, ToolResult, ToolResultStatus
from app.api.knowledge_models import KnowledgeCitation
from app.identity import AuthenticatedEmployeeContext

MAX_MODEL_ROUNDS_PER_TURN = 7


class AgentProviderSession(Protocol):
    def next(
        self,
        tool_responses: tuple[AgentToolResponse, ...] = (),
    ) -> AgentModelTurn: ...


class AgentProviderClient(Protocol):
    def start(self, user_message: str) -> AgentProviderSession: ...


class AgentService:
    """Run one bounded in-memory model/tool conversation with no persistence."""

    def __init__(
        self,
        *,
        provider: AgentProviderClient,
        dispatcher: ToolDispatcher,
    ) -> None:
        self._provider = provider
        self._dispatcher = dispatcher

    def run(
        self,
        user_message: str,
        context: AuthenticatedEmployeeContext,
    ) -> AgentRunResult:
        if not isinstance(user_message, str) or not user_message.strip():
            return _unable("The assistant request was invalid.", tool_calls=0, rounds=0)
        cleaned_message = user_message.strip()
        if len(cleaned_message) > 4_000:
            return _unable("The assistant request was too long.", tool_calls=0, rounds=0)

        try:
            session = self._provider.start(cleaned_message)
        except AgentProviderRateLimitError:
            return _provider_rate_limited(tool_calls=0, rounds=0)
        except AgentProviderError:
            return _provider_unavailable(tool_calls=0, rounds=0)

        pending_responses: tuple[AgentToolResponse, ...] = ()
        citations: list[KnowledgeCitation] = []
        citation_identities: set[tuple[str, str, str, str, int | None]] = set()
        tool_calls_attempted = 0

        for model_round in range(1, MAX_MODEL_ROUNDS_PER_TURN + 1):
            try:
                turn = session.next(pending_responses)
            except AgentProviderRateLimitError:
                return _provider_rate_limited(
                    tool_calls=tool_calls_attempted,
                    rounds=model_round,
                    citations=tuple(citations),
                )
            except InvalidAgentProviderResponseError:
                return _unable(
                    "The assistant provider returned an invalid response.",
                    tool_calls=tool_calls_attempted,
                    rounds=model_round,
                    citations=tuple(citations),
                )
            except AgentProviderError:
                return _provider_unavailable(
                    tool_calls=tool_calls_attempted,
                    rounds=model_round,
                    citations=tuple(citations),
                )
            pending_responses = ()

            if turn.final_text is not None:
                return AgentRunResult(
                    status=AgentRunStatus.COMPLETED,
                    answer=turn.final_text,
                    citations=tuple(citations),
                    tool_calls_attempted=tool_calls_attempted,
                    model_rounds=model_round,
                )

            if not turn.requested_calls:
                continue

            responses: list[AgentToolResponse] = []
            for requested_call in turn.requested_calls:
                if tool_calls_attempted >= MAX_TOOL_CALLS_PER_TURN:
                    return AgentRunResult(
                        status=AgentRunStatus.TOOL_BUDGET_EXHAUSTED,
                        citations=tuple(citations),
                        safe_message="The assistant reached its tool-call limit.",
                        tool_calls_attempted=tool_calls_attempted,
                        model_rounds=model_round,
                    )
                tool_calls_attempted += 1
                try:
                    result = self._dispatcher.dispatch(
                        name=requested_call.name,
                        arguments=requested_call.arguments,
                        context=context,
                    )
                except Exception:
                    result = ToolResult.failure(
                        "unknown_tool",
                        ToolResultStatus.INTERNAL_ERROR,
                        "The tool could not complete the request.",
                    )
                _collect_citations(result, citations, citation_identities)
                responses.append(
                    AgentToolResponse(
                        name=result.tool_name,
                        result=result,
                        provider_call_id=requested_call.provider_call_id,
                    )
                )
            pending_responses = tuple(responses)

        return _unable(
            "The assistant reached its model-round limit.",
            tool_calls=tool_calls_attempted,
            rounds=MAX_MODEL_ROUNDS_PER_TURN,
            citations=tuple(citations),
        )


def _collect_citations(
    result: ToolResult,
    citations: list[KnowledgeCitation],
    identities: set[tuple[str, str, str, str, int | None]],
) -> None:
    if result.status is not ToolResultStatus.SUCCESS or not isinstance(
        result.data, KnowledgeToolData
    ):
        return
    for citation in result.data.citations:
        identity = (
            citation.doc_code,
            citation.title,
            citation.version,
            citation.section_anchor,
            citation.page,
        )
        if identity in identities:
            continue
        identities.add(identity)
        citations.append(citation)


def _unable(
    message: str,
    *,
    tool_calls: int,
    rounds: int,
    citations: tuple[KnowledgeCitation, ...] = (),
) -> AgentRunResult:
    return AgentRunResult(
        status=AgentRunStatus.UNABLE_TO_COMPLETE,
        citations=citations,
        safe_message=message,
        tool_calls_attempted=tool_calls,
        model_rounds=rounds,
    )


def _provider_unavailable(
    *,
    tool_calls: int,
    rounds: int,
    citations: tuple[KnowledgeCitation, ...] = (),
) -> AgentRunResult:
    return AgentRunResult(
        status=AgentRunStatus.PROVIDER_UNAVAILABLE,
        citations=citations,
        safe_message="The assistant provider is temporarily unavailable.",
        tool_calls_attempted=tool_calls,
        model_rounds=rounds,
    )


def _provider_rate_limited(
    *,
    tool_calls: int,
    rounds: int,
    citations: tuple[KnowledgeCitation, ...] = (),
) -> AgentRunResult:
    return AgentRunResult(
        status=AgentRunStatus.PROVIDER_RATE_LIMITED,
        citations=citations,
        safe_message="The assistant provider is busy. Please try again later.",
        tool_calls_attempted=tool_calls,
        model_rounds=rounds,
    )

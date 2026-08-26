"""Bounded single-turn V3 read-agent orchestration over deterministic dispatch."""

from datetime import date
from typing import Protocol

from app.agent.client import (
    AgentProviderError,
    AgentProviderRateLimitError,
    InvalidAgentProviderResponseError,
)
from app.agent.contracts import MAX_TOOL_CALLS_PER_TURN
from app.agent.dispatcher import ToolDispatcher
from app.agent.leave_models import LeaveRequestDraft
from app.agent.loop_models import (
    MAX_AGENT_CITATIONS,
    AgentModelTurn,
    AgentRunResult,
    AgentRunStatus,
    AgentToolResponse,
)
from app.agent.models import (
    KnowledgeToolData,
    PreparedLeaveRequestToolData,
    ToolResult,
    ToolResultStatus,
)
from app.agent.provider_failures import AgentProviderFailureDetail
from app.api.knowledge_models import KnowledgeCitation
from app.identity import AuthenticatedEmployeeContext
from app.knowledge.clock import MelbourneClock, TrustedClock

MAX_MODEL_ROUNDS_PER_TURN = 7


class AgentProviderSession(Protocol):
    def next(
        self,
        tool_responses: tuple[AgentToolResponse, ...] = (),
    ) -> AgentModelTurn: ...


class AgentProviderClient(Protocol):
    def start(
        self,
        user_message: str,
        trusted_today: date,
    ) -> AgentProviderSession: ...


class AgentService:
    """Run one bounded in-memory model/tool conversation with no persistence."""

    def __init__(
        self,
        *,
        provider: AgentProviderClient,
        dispatcher: ToolDispatcher,
        clock: TrustedClock | None = None,
    ) -> None:
        self._provider = provider
        self._dispatcher = dispatcher
        self._clock = clock or MelbourneClock()

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
            session = self._provider.start(cleaned_message, self._clock.today())
        except AgentProviderRateLimitError as exc:
            return _provider_rate_limited(
                tool_calls=0,
                rounds=0,
                provider_failure=exc.failure,
            )
        except AgentProviderError as exc:
            return _provider_unavailable(
                tool_calls=0,
                rounds=0,
                provider_failure=exc.failure,
            )
        except Exception:
            return _unable(
                "The assistant provider could not start safely.",
                tool_calls=0,
                rounds=0,
            )

        pending_responses: tuple[AgentToolResponse, ...] = ()
        citations: list[KnowledgeCitation] = []
        citation_identities: set[tuple[str, str, str, str, int | None]] = set()
        tool_calls_attempted = 0
        prepared_leave_request: LeaveRequestDraft | None = None

        for model_round in range(1, MAX_MODEL_ROUNDS_PER_TURN + 1):
            try:
                turn = session.next(pending_responses)
            except AgentProviderRateLimitError as exc:
                return _provider_rate_limited(
                    tool_calls=tool_calls_attempted,
                    rounds=model_round,
                    citations=tuple(citations),
                    prepared_leave_request=prepared_leave_request,
                    provider_failure=exc.failure,
                )
            except InvalidAgentProviderResponseError:
                return _unable(
                    "The assistant provider returned an invalid response.",
                    tool_calls=tool_calls_attempted,
                    rounds=model_round,
                    citations=tuple(citations),
                    prepared_leave_request=prepared_leave_request,
                )
            except AgentProviderError as exc:
                return _provider_unavailable(
                    tool_calls=tool_calls_attempted,
                    rounds=model_round,
                    citations=tuple(citations),
                    prepared_leave_request=prepared_leave_request,
                    provider_failure=exc.failure,
                )
            except Exception:
                return _unable(
                    "The assistant provider returned an unexpected response.",
                    tool_calls=tool_calls_attempted,
                    rounds=model_round,
                    citations=tuple(citations),
                    prepared_leave_request=prepared_leave_request,
                )
            pending_responses = ()

            if turn.final_text is not None:
                return AgentRunResult(
                    status=AgentRunStatus.COMPLETED,
                    answer=turn.final_text,
                    citations=tuple(citations),
                    prepared_leave_request=prepared_leave_request,
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
                        prepared_leave_request=prepared_leave_request,
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
                if result.status is ToolResultStatus.SUCCESS and isinstance(
                    result.data, PreparedLeaveRequestToolData
                ):
                    prepared_leave_request = result.data.draft
                responses.append(
                    AgentToolResponse(
                        name=_function_response_name(
                            requested_call.name,
                            result.tool_name,
                        ),
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
            prepared_leave_request=prepared_leave_request,
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
        if len(citations) >= MAX_AGENT_CITATIONS:
            continue
        identities.add(identity)
        citations.append(citation)


def _function_response_name(requested_name: object, result_tool_name: str) -> str:
    """Preserve only a provider name that passed the exact dispatcher sanitizer."""

    return requested_name if requested_name == result_tool_name else result_tool_name


def _unable(
    message: str,
    *,
    tool_calls: int,
    rounds: int,
    citations: tuple[KnowledgeCitation, ...] = (),
    prepared_leave_request: LeaveRequestDraft | None = None,
) -> AgentRunResult:
    return AgentRunResult(
        status=AgentRunStatus.UNABLE_TO_COMPLETE,
        citations=citations,
        prepared_leave_request=prepared_leave_request,
        safe_message=message,
        tool_calls_attempted=tool_calls,
        model_rounds=rounds,
    )


def _provider_unavailable(
    *,
    tool_calls: int,
    rounds: int,
    citations: tuple[KnowledgeCitation, ...] = (),
    prepared_leave_request: LeaveRequestDraft | None = None,
    provider_failure: AgentProviderFailureDetail | None = None,
) -> AgentRunResult:
    return AgentRunResult(
        status=AgentRunStatus.PROVIDER_UNAVAILABLE,
        citations=citations,
        prepared_leave_request=prepared_leave_request,
        safe_message="The assistant provider is temporarily unavailable.",
        tool_calls_attempted=tool_calls,
        model_rounds=rounds,
        provider_failure=provider_failure,
    )


def _provider_rate_limited(
    *,
    tool_calls: int,
    rounds: int,
    citations: tuple[KnowledgeCitation, ...] = (),
    prepared_leave_request: LeaveRequestDraft | None = None,
    provider_failure: AgentProviderFailureDetail | None = None,
) -> AgentRunResult:
    return AgentRunResult(
        status=AgentRunStatus.PROVIDER_RATE_LIMITED,
        citations=citations,
        prepared_leave_request=prepared_leave_request,
        safe_message="The assistant provider is busy. Please try again later.",
        tool_calls_attempted=tool_calls,
        model_rounds=rounds,
        provider_failure=provider_failure,
    )

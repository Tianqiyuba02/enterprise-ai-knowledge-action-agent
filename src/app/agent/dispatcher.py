"""Deterministic authenticated dispatch for the fixed V3 read-tool allowlist."""

import re

from pydantic import ValidationError

from app.agent.contracts import V3_TOOL_ALLOWLIST, ToolHandlerName, V3ToolName
from app.agent.leave_models import PrepareLeaveRequestArguments
from app.agent.models import (
    GetMyTicketArguments,
    KnowledgeQueryArguments,
    KnowledgeToolData,
    LeaveBalancesToolData,
    LeaveBalanceToolItem,
    PreparedLeaveRequestToolData,
    ProfileToolData,
    ProviderToolRequest,
    TicketToolData,
    ToolResult,
    ToolResultStatus,
)
from app.agent.provider import normalize_provider_arguments
from app.embeddings.client import EmbeddingClientError
from app.errors import ApplicationError, EmployeeNotFoundError, TicketNotFoundError
from app.grounding.client import GroundedGenerationError
from app.identity import AuthenticatedEmployeeContext
from app.knowledge.applicability import ApplicabilityContextError, resolve_knowledge_applicability
from app.knowledge.errors import KnowledgeDatabaseError, KnowledgeRetrievalError
from app.knowledge.query_service import KnowledgeQueryService
from app.repositories.demo import DemoRepository
from app.services.employee import EmployeeService
from app.services.it import ITService
from app.services.leave_preparation import (
    LeavePreparationService,
    LeavePreparationUnavailableError,
)


class ToolDispatcher:
    """Validate one requested call and dispatch it through trusted application services."""

    def __init__(
        self,
        *,
        employee_service: EmployeeService,
        it_service: ITService,
        knowledge_service: KnowledgeQueryService,
        demo_repository: DemoRepository,
        leave_preparation_service: LeavePreparationService | None = None,
    ) -> None:
        self._employee_service = employee_service
        self._it_service = it_service
        self._knowledge_service = knowledge_service
        self._demo_repository = demo_repository
        self._leave_preparation_service = leave_preparation_service or LeavePreparationService(
            employee_service
        )

    def dispatch(
        self,
        *,
        name: object,
        arguments: object,
        context: AuthenticatedEmployeeContext,
    ) -> ToolResult:
        tool_name = _safe_tool_name(name)
        try:
            request = ProviderToolRequest.model_validate(
                {
                    "name": name,
                    "arguments": normalize_provider_arguments(arguments),
                }
            )
            canonical_name = V3ToolName(request.name)
            contract = V3_TOOL_ALLOWLIST[canonical_name]
            validated_arguments = contract.argument_model.model_validate(request.arguments)
        except (ValidationError, ValueError, KeyError):
            return ToolResult.failure(
                tool_name,
                ToolResultStatus.INVALID_ARGUMENTS,
                "The requested tool or its arguments were invalid.",
            )

        try:
            if contract.handler is ToolHandlerName.GET_MY_PROFILE:
                return self._get_my_profile(canonical_name, context)
            if contract.handler is ToolHandlerName.GET_MY_LEAVE_BALANCES:
                return self._get_my_leave_balances(canonical_name, context)
            if contract.handler is ToolHandlerName.GET_MY_TICKET:
                return self._get_my_ticket(
                    canonical_name,
                    GetMyTicketArguments.model_validate(validated_arguments),
                    context,
                )
            if contract.handler is ToolHandlerName.KNOWLEDGE_QUERY:
                return self._knowledge_query(
                    canonical_name,
                    KnowledgeQueryArguments.model_validate(validated_arguments),
                    context,
                )
            if contract.handler is ToolHandlerName.PREPARE_LEAVE_REQUEST:
                return self._prepare_leave_request(
                    canonical_name,
                    PrepareLeaveRequestArguments.model_validate(validated_arguments),
                    context,
                )
            return ToolResult.failure(
                canonical_name.value,
                ToolResultStatus.INTERNAL_ERROR,
                "The tool could not be dispatched.",
            )
        except (TicketNotFoundError, EmployeeNotFoundError):
            return ToolResult.failure(
                canonical_name.value,
                ToolResultStatus.NOT_FOUND_OR_INACCESSIBLE,
                "The requested resource was not found or is inaccessible.",
            )
        except KnowledgeDatabaseError:
            return ToolResult.failure(
                canonical_name.value,
                ToolResultStatus.TEMPORARILY_UNAVAILABLE,
                "The requested data is temporarily unavailable.",
            )
        except LeavePreparationUnavailableError:
            return ToolResult.failure(
                canonical_name.value,
                ToolResultStatus.TEMPORARILY_UNAVAILABLE,
                "Leave preparation data is temporarily unavailable.",
            )
        except (EmbeddingClientError, GroundedGenerationError):
            return ToolResult.failure(
                canonical_name.value,
                ToolResultStatus.PROVIDER_UNAVAILABLE,
                "The provider-backed tool is temporarily unavailable.",
            )
        except (KnowledgeRetrievalError, ApplicabilityContextError, ApplicationError):
            return ToolResult.failure(
                canonical_name.value,
                ToolResultStatus.TEMPORARILY_UNAVAILABLE,
                "The requested data is temporarily unavailable.",
            )
        except Exception:
            return ToolResult.failure(
                canonical_name.value,
                ToolResultStatus.INTERNAL_ERROR,
                "The tool could not complete the request.",
            )

    def _get_my_profile(
        self,
        tool_name: V3ToolName,
        context: AuthenticatedEmployeeContext,
    ) -> ToolResult:
        profile = self._employee_service.get_my_profile(context)
        return ToolResult.success(
            tool_name.value,
            ProfileToolData(
                full_name=profile.full_name,
                work_email=profile.work_email,
                location=profile.location,
                employment_type=profile.employment_type,
                hours_per_day=profile.hours_per_day,
                work_days=profile.work_days,
                timezone=profile.timezone,
                is_active=profile.is_active,
            ),
        )

    def _get_my_leave_balances(
        self,
        tool_name: V3ToolName,
        context: AuthenticatedEmployeeContext,
    ) -> ToolResult:
        balances = self._employee_service.get_my_leave_balances(context)
        return ToolResult.success(
            tool_name.value,
            LeaveBalancesToolData(
                balances=tuple(
                    LeaveBalanceToolItem(
                        leave_type=balance.leave_type,
                        balance_hours=balance.balance_hours,
                        as_of_date=balance.as_of_date,
                    )
                    for balance in balances
                )
            ),
        )

    def _get_my_ticket(
        self,
        tool_name: V3ToolName,
        arguments: GetMyTicketArguments,
        context: AuthenticatedEmployeeContext,
    ) -> ToolResult:
        ticket = self._it_service.get_my_ticket(arguments.ticket_id, context)
        return ToolResult.success(
            tool_name.value,
            TicketToolData(
                ticket_id=ticket.ticket_id,
                category=ticket.category,
                summary=ticket.summary,
                description=ticket.description,
                urgency=ticket.urgency,
                status=ticket.status,
                created_at=ticket.created_at,
                updated_at=ticket.updated_at,
            ),
        )

    def _knowledge_query(
        self,
        tool_name: V3ToolName,
        arguments: KnowledgeQueryArguments,
        context: AuthenticatedEmployeeContext,
    ) -> ToolResult:
        applicability = resolve_knowledge_applicability(context, self._demo_repository)
        response = self._knowledge_service.query(arguments.question, applicability)
        return ToolResult.success(
            tool_name.value,
            KnowledgeToolData(
                status=response.status,
                answer=response.answer,
                citations=response.citations,
            ),
        )

    def _prepare_leave_request(
        self,
        tool_name: V3ToolName,
        arguments: PrepareLeaveRequestArguments,
        context: AuthenticatedEmployeeContext,
    ) -> ToolResult:
        draft = self._leave_preparation_service.prepare(arguments, context)
        return ToolResult.success(
            tool_name.value,
            PreparedLeaveRequestToolData(draft=draft),
        )


def _safe_tool_name(name: object) -> str:
    if isinstance(name, str) and name in {tool_name.value for tool_name in V3_TOOL_ALLOWLIST}:
        return name
    if not isinstance(name, str) or re.fullmatch(r"[a-z0-9_]{1,64}", name) is None:
        return "unknown_tool"
    suspicious_tokens = {
        "call",
        "developer",
        "execute",
        "ignore",
        "instruction",
        "instructions",
        "prompt",
        "system",
    }
    if suspicious_tokens & set(name.split("_")):
        return "unknown_tool"
    return name

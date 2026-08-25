"""Frozen Stage 0 contracts for the future bounded V3 agent loop."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

MAX_TOOL_CALLS_PER_TURN: Final = 5


class V3ToolName(StrEnum):
    KNOWLEDGE_QUERY = "knowledge_query"
    GET_MY_PROFILE = "get_my_profile"
    GET_MY_LEAVE_BALANCES = "get_my_leave_balances"
    GET_MY_TICKET = "get_my_ticket"


class ToolCapability(StrEnum):
    READ = "read"
    PREPARE = "prepare"


class ToolErrorCode(StrEnum):
    INVALID_ARGUMENTS = "invalid_arguments"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class ToolContract:
    name: V3ToolName
    capability: ToolCapability
    llm_arguments: tuple[str, ...]
    description: str


V3_TOOL_ALLOWLIST: Final = MappingProxyType(
    {
        V3ToolName.KNOWLEDGE_QUERY: ToolContract(
            name=V3ToolName.KNOWLEDGE_QUERY,
            capability=ToolCapability.READ,
            llm_arguments=("question",),
            description=(
                "Return one existing V2 grounded knowledge response. "
                "Applicability is injected from trusted server context."
            ),
        ),
        V3ToolName.GET_MY_PROFILE: ToolContract(
            name=V3ToolName.GET_MY_PROFILE,
            capability=ToolCapability.READ,
            llm_arguments=(),
            description="Read the authenticated employee's own profile.",
        ),
        V3ToolName.GET_MY_LEAVE_BALANCES: ToolContract(
            name=V3ToolName.GET_MY_LEAVE_BALANCES,
            capability=ToolCapability.READ,
            llm_arguments=(),
            description="Read the authenticated employee's own leave balances.",
        ),
        V3ToolName.GET_MY_TICKET: ToolContract(
            name=V3ToolName.GET_MY_TICKET,
            capability=ToolCapability.READ,
            llm_arguments=("ticket_id",),
            description=(
                "Read one support ticket only when it belongs to the authenticated employee."
            ),
        ),
    }
)

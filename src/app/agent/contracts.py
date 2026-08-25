"""Frozen Stage 0 contracts for the future bounded V3 agent loop."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel

from app.agent.models import GetMyTicketArguments, KnowledgeQueryArguments, NoToolArguments

MAX_TOOL_CALLS_PER_TURN: Final = 5


class V3ToolName(StrEnum):
    KNOWLEDGE_QUERY = "knowledge_query"
    GET_MY_PROFILE = "get_my_profile"
    GET_MY_LEAVE_BALANCES = "get_my_leave_balances"
    GET_MY_TICKET = "get_my_ticket"


class ToolCapability(StrEnum):
    READ = "read"
    PREPARE = "prepare"


class ToolHandlerName(StrEnum):
    KNOWLEDGE_QUERY = "knowledge_query"
    GET_MY_PROFILE = "get_my_profile"
    GET_MY_LEAVE_BALANCES = "get_my_leave_balances"
    GET_MY_TICKET = "get_my_ticket"


@dataclass(frozen=True, slots=True)
class ToolContract:
    name: V3ToolName
    capability: ToolCapability
    description: str
    argument_model: type[BaseModel]
    handler: ToolHandlerName

    @property
    def llm_arguments(self) -> tuple[str, ...]:
        return tuple(self.argument_model.model_fields)


V3_TOOL_ALLOWLIST: Final = MappingProxyType(
    {
        V3ToolName.KNOWLEDGE_QUERY: ToolContract(
            name=V3ToolName.KNOWLEDGE_QUERY,
            capability=ToolCapability.READ,
            description=(
                "Return one existing V2 grounded knowledge response. "
                "Applicability is injected from trusted server context."
            ),
            argument_model=KnowledgeQueryArguments,
            handler=ToolHandlerName.KNOWLEDGE_QUERY,
        ),
        V3ToolName.GET_MY_PROFILE: ToolContract(
            name=V3ToolName.GET_MY_PROFILE,
            capability=ToolCapability.READ,
            description="Read the authenticated employee's own profile.",
            argument_model=NoToolArguments,
            handler=ToolHandlerName.GET_MY_PROFILE,
        ),
        V3ToolName.GET_MY_LEAVE_BALANCES: ToolContract(
            name=V3ToolName.GET_MY_LEAVE_BALANCES,
            capability=ToolCapability.READ,
            description="Read the authenticated employee's own leave balances.",
            argument_model=NoToolArguments,
            handler=ToolHandlerName.GET_MY_LEAVE_BALANCES,
        ),
        V3ToolName.GET_MY_TICKET: ToolContract(
            name=V3ToolName.GET_MY_TICKET,
            capability=ToolCapability.READ,
            description=(
                "Read one support ticket only when it belongs to the authenticated employee."
            ),
            argument_model=GetMyTicketArguments,
            handler=ToolHandlerName.GET_MY_TICKET,
        ),
    }
)

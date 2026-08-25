import pytest
from pydantic import ValidationError

from app.agent.contracts import (
    MAX_TOOL_CALLS_PER_TURN,
    V3_TOOL_ALLOWLIST,
    ToolCapability,
    ToolErrorCode,
    V3ToolName,
)
from app.config import AgentSettings, KnowledgeSettings, Settings


def test_agent_model_is_isolated_from_released_v1_v2_models() -> None:
    agent = AgentSettings(_env_file=None)
    v1 = Settings(gemini_api_key="test-only-key", _env_file=None)
    v2 = KnowledgeSettings(_env_file=None)

    assert agent.agent_model == "gemini-3.6-flash"
    assert v1.gemini_model == "gemini-3.5-flash"
    assert v2.knowledge_grounded_model == "gemini-3.6-flash"
    assert v2.knowledge_embedding_model == "gemini-embedding-2"
    assert v2.knowledge_embedding_dimension == 768

    with pytest.raises(ValidationError):
        AgentSettings(agent_model="gemini-3.5-flash", _env_file=None)


def test_stage0_allowlist_contains_only_expected_read_tools() -> None:
    assert set(V3_TOOL_ALLOWLIST) == {
        V3ToolName.KNOWLEDGE_QUERY,
        V3ToolName.GET_MY_PROFILE,
        V3ToolName.GET_MY_LEAVE_BALANCES,
        V3ToolName.GET_MY_TICKET,
    }
    assert all(
        contract.capability is ToolCapability.READ for contract in V3_TOOL_ALLOWLIST.values()
    )


def test_model_arguments_cannot_control_identity_applicability_or_generic_capabilities() -> None:
    forbidden_arguments = {"employee_id", "jurisdiction", "audience", "audience_groups"}
    generic_fragments = {"sql", "http", "shell", "filesystem", "url", "command"}

    for contract in V3_TOOL_ALLOWLIST.values():
        assert forbidden_arguments.isdisjoint(contract.llm_arguments)
        assert not any(fragment in contract.name.value for fragment in generic_fragments)

    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_PROFILE].llm_arguments == ()
    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_LEAVE_BALANCES].llm_arguments == ()
    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_TICKET].llm_arguments == ("ticket_id",)
    assert V3_TOOL_ALLOWLIST[V3ToolName.KNOWLEDGE_QUERY].llm_arguments == ("question",)


def test_tool_budget_and_safe_error_taxonomy_are_explicit() -> None:
    assert MAX_TOOL_CALLS_PER_TURN == 5
    assert set(ToolErrorCode) == {
        ToolErrorCode.INVALID_ARGUMENTS,
        ToolErrorCode.NOT_FOUND,
        ToolErrorCode.UNAVAILABLE,
        ToolErrorCode.TIMEOUT,
        ToolErrorCode.BUDGET_EXHAUSTED,
        ToolErrorCode.INTERNAL_ERROR,
    }

    with pytest.raises(TypeError):
        V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_PROFILE] = None

import pytest
from pydantic import ValidationError

from app.agent.contracts import (
    MAX_TOOL_CALLS_PER_TURN,
    V3_TOOL_ALLOWLIST,
    ToolCapability,
    V3ToolName,
)
from app.agent.models import ToolResultStatus
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


def test_agent_timeout_is_isolated_validated_and_environment_controlled(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "45")

    assert AgentSettings(_env_file=None).agent_timeout_seconds == 60
    assert Settings(gemini_api_key="test-only-key", _env_file=None).gemini_timeout_seconds == 45

    monkeypatch.setenv("AGENT_TIMEOUT_SECONDS", "75")
    assert AgentSettings(_env_file=None).agent_timeout_seconds == 75

    for invalid in (0, 121):
        with pytest.raises(ValidationError):
            AgentSettings(agent_timeout_seconds=invalid, _env_file=None)


def test_agent_retry_policy_is_isolated_validated_and_environment_controlled(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AGENT_MAX_ATTEMPTS", raising=False)
    monkeypatch.setenv("GEMINI_MAX_ATTEMPTS", "3")

    assert AgentSettings(_env_file=None).agent_max_attempts == 1
    assert Settings(gemini_api_key="test-only-key", _env_file=None).gemini_max_attempts == 3

    monkeypatch.setenv("AGENT_MAX_ATTEMPTS", "2")
    assert AgentSettings(_env_file=None).agent_max_attempts == 2

    for invalid in (0, 4):
        with pytest.raises(ValidationError):
            AgentSettings(agent_max_attempts=invalid, _env_file=None)


def test_allowlist_contains_four_read_tools_and_one_prepare_tool() -> None:
    assert set(V3_TOOL_ALLOWLIST) == {
        V3ToolName.KNOWLEDGE_QUERY,
        V3ToolName.GET_MY_PROFILE,
        V3ToolName.GET_MY_LEAVE_BALANCES,
        V3ToolName.GET_MY_TICKET,
        V3ToolName.PREPARE_LEAVE_REQUEST,
    }
    assert V3_TOOL_ALLOWLIST[V3ToolName.PREPARE_LEAVE_REQUEST].capability is ToolCapability.PREPARE
    assert all(
        V3_TOOL_ALLOWLIST[name].capability is ToolCapability.READ
        for name in {
            V3ToolName.KNOWLEDGE_QUERY,
            V3ToolName.GET_MY_PROFILE,
            V3ToolName.GET_MY_LEAVE_BALANCES,
            V3ToolName.GET_MY_TICKET,
        }
    )


def test_model_arguments_cannot_control_identity_applicability_or_generic_capabilities() -> None:
    forbidden_arguments = {
        "employee_id",
        "jurisdiction",
        "audience",
        "audience_groups",
        "trusted_today",
        "timezone",
        "hours_per_day",
        "current_balance_hours",
    }
    generic_fragments = {"sql", "http", "shell", "filesystem", "url", "command"}

    for contract in V3_TOOL_ALLOWLIST.values():
        assert forbidden_arguments.isdisjoint(contract.llm_arguments)
        assert not any(fragment in contract.name.value for fragment in generic_fragments)

    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_PROFILE].llm_arguments == ()
    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_LEAVE_BALANCES].llm_arguments == ()
    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_TICKET].llm_arguments == ("ticket_id",)
    assert V3_TOOL_ALLOWLIST[V3ToolName.KNOWLEDGE_QUERY].llm_arguments == ("question",)
    assert V3_TOOL_ALLOWLIST[V3ToolName.PREPARE_LEAVE_REQUEST].llm_arguments == (
        "leave_type",
        "start_date",
        "end_date",
        "reason",
    )


def test_tool_budget_and_safe_error_taxonomy_are_explicit() -> None:
    assert MAX_TOOL_CALLS_PER_TURN == 5
    assert set(ToolResultStatus) == {
        ToolResultStatus.SUCCESS,
        ToolResultStatus.INVALID_ARGUMENTS,
        ToolResultStatus.NOT_FOUND_OR_INACCESSIBLE,
        ToolResultStatus.TEMPORARILY_UNAVAILABLE,
        ToolResultStatus.PROVIDER_UNAVAILABLE,
        ToolResultStatus.BUDGET_EXHAUSTED,
        ToolResultStatus.INTERNAL_ERROR,
    }

    with pytest.raises(TypeError):
        V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_PROFILE] = None

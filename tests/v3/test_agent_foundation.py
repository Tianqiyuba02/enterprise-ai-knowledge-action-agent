import pytest
from pydantic import ValidationError

from app.agent.client import AGENT_SYSTEM_INSTRUCTION
from app.agent.contracts import (
    MAX_TOOL_CALLS_PER_TURN,
    V3_TOOL_ALLOWLIST,
    ToolCapability,
    V3ToolName,
)
from app.agent.models import ToolResultStatus
from app.config import APPROVED_AGENT_MODEL, AgentSettings, KnowledgeSettings, Settings
from app.evaluation.agent_loader import (
    agent_dataset_fingerprint,
    load_agent_evaluation_cases,
)
from app.evaluation.models import EvaluationSplit


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


def test_system_instruction_states_general_no_exploratory_tool_discipline() -> None:
    assert (
        "Only call tools when necessary to fulfill an allowed operation for the "
        "authenticated employee."
    ) in AGENT_SYSTEM_INSTRUCTION
    assert (
        "If a request is disallowed, inapplicable, or cannot be fulfilled in the current turn"
        in AGENT_SYSTEM_INSTRUCTION
    )
    assert "without exploratory READ calls" in AGENT_SYSTEM_INSTRUCTION
    assert (
        "A request for another employee's private data must not trigger the current "
        "employee's self-profile" in AGENT_SYSTEM_INSTRUCTION
    )
    assert "or self-balance tools merely to gather context" in AGENT_SYSTEM_INSTRUCTION
    assert "A standalone confirmation or submission utterance" in AGENT_SYSTEM_INSTRUCTION
    assert (
        "must not trigger READ or PREPARE tools to reconstruct missing" in AGENT_SYSTEM_INSTRUCTION
    )
    assert "no actionable draft exists in the current turn" in AGENT_SYSTEM_INSTRUCTION
    assert "Sam Lee" not in AGENT_SYSTEM_INSTRUCTION
    assert "Yes, submit it." not in AGENT_SYSTEM_INSTRUCTION
    assert "dev_agent_" not in AGENT_SYSTEM_INSTRUCTION


def test_knowledge_tool_guidance_allows_helpful_fallback_without_execution() -> None:
    """Contract/prompt representation only; this does not prove live model behavior."""

    description = V3_TOOL_ALLOWLIST[V3ToolName.KNOWLEDGE_QUERY].description
    assert "informational" in description
    assert "policy" in description
    assert "procedure" in description
    assert "how-to" in description
    assert "cannot perform" in description or "cannot perform" in AGENT_SYSTEM_INSTRUCTION
    assert "trusted manual procedure" in description
    assert "does not perform the requested action" in description
    assert "automatic fallback" not in description
    assert "automatic fallback" not in AGENT_SYSTEM_INSTRUCTION
    assert (
        "Use the knowledge tool for informational, policy, procedure, or how-to questions."
        in AGENT_SYSTEM_INSTRUCTION
    )
    assert "clearly state that it cannot perform and did not" in AGENT_SYSTEM_INSTRUCTION
    assert "perform the requested action" in AGENT_SYSTEM_INSTRUCTION
    assert "trusted manual procedure or" in AGENT_SYSTEM_INSTRUCTION
    assert "next steps when that information is relevant" in AGENT_SYSTEM_INSTRUCTION
    assert "Keep that guidance distinct from performing the" in AGENT_SYSTEM_INSTRUCTION
    assert "Never claim that a business action was executed" in AGENT_SYSTEM_INSTRUCTION
    for surface in (description, AGENT_SYSTEM_INSTRUCTION):
        assert "TKT-1001" not in surface
        assert "close my ticket" not in surface.lower()
        assert "dev_agent_close_ticket" not in surface
        assert "close-ticket" not in surface


def test_knowledge_guidance_preserves_other_tool_contracts_and_boundaries() -> None:
    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_PROFILE].description == (
        "Read the authenticated employee's own profile."
    )
    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_LEAVE_BALANCES].description == (
        "Read the authenticated employee's own leave balances."
    )
    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_TICKET].description == (
        "Read one support ticket only when it belongs to the authenticated employee."
    )
    assert V3_TOOL_ALLOWLIST[V3ToolName.PREPARE_LEAVE_REQUEST].description == (
        "Build one annual leave draft from trusted schedule and balance data. "
        "The draft changes no business state."
    )
    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_PROFILE].llm_arguments == ()
    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_LEAVE_BALANCES].llm_arguments == ()
    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_TICKET].llm_arguments == ("ticket_id",)
    assert V3_TOOL_ALLOWLIST[V3ToolName.KNOWLEDGE_QUERY].llm_arguments == ("question",)
    assert "execute" not in V3_TOOL_ALLOWLIST[V3ToolName.KNOWLEDGE_QUERY].description.lower()
    assert "write" not in V3_TOOL_ALLOWLIST[V3ToolName.KNOWLEDGE_QUERY].description.lower()
    assert APPROVED_AGENT_MODEL == "gemini-3.6-flash"
    assert AgentSettings.model_fields["agent_timeout_seconds"].default == 60
    assert AgentSettings.model_fields["agent_max_attempts"].default == 1
    assert (
        agent_dataset_fingerprint(load_agent_evaluation_cases(EvaluationSplit.DEVELOPMENT))
        == "1b6fb7d7e7a813bae4d71e1459bf2d5e20ab611c6e9091f9bf4a556bf9ec3ee7"
    )
    assert (
        agent_dataset_fingerprint(load_agent_evaluation_cases(EvaluationSplit.HOLDOUT))
        == "b68a78f687b81040e265aef6d934d4879b3180405159cb4d5ed10ad923ba4d58"
    )


def test_system_instruction_states_general_relative_weekday_convention() -> None:
    assert (
        'Interpret "next <weekday>" as the first occurrence of that weekday strictly after the '
        "trusted" in AGENT_SYSTEM_INSTRUCTION
    )
    assert "current Australia/Melbourne date" in AGENT_SYSTEM_INSTRUCTION
    assert (
        "Convert that interpreted date to ISO YYYY-MM-DD before proposing"
        in AGENT_SYSTEM_INSTRUCTION
    )
    assert "A prepared leave draft remains non-executing" in AGENT_SYSTEM_INSTRUCTION
    assert "must expose the resulting" in AGENT_SYSTEM_INSTRUCTION
    assert "explicit date" in AGENT_SYSTEM_INSTRUCTION
    assert "2026-08-28" not in AGENT_SYSTEM_INSTRUCTION
    assert "Friday" not in AGENT_SYSTEM_INSTRUCTION
    assert "dev_agent_prepare_next_friday" not in AGENT_SYSTEM_INSTRUCTION


def test_system_instruction_change_does_not_weaken_security_or_runtime_boundaries() -> None:
    assert "UNTRUSTED DATA" in AGENT_SYSTEM_INSTRUCTION
    assert "Never infer, select, or change employee identity" in AGENT_SYSTEM_INSTRUCTION
    assert (
        "Authorization and the absence of write tools are enforced by application code"
        in AGENT_SYSTEM_INSTRUCTION
    )
    assert APPROVED_AGENT_MODEL == "gemini-3.6-flash"
    assert AgentSettings.model_fields["agent_timeout_seconds"].default == 60
    assert AgentSettings.model_fields["agent_max_attempts"].default == 1
    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_PROFILE].llm_arguments == ()
    assert V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_LEAVE_BALANCES].llm_arguments == ()
    assert "employee_id" not in V3_TOOL_ALLOWLIST[V3ToolName.GET_MY_TICKET].llm_arguments
    assert (
        agent_dataset_fingerprint(load_agent_evaluation_cases(EvaluationSplit.DEVELOPMENT))
        == "1b6fb7d7e7a813bae4d71e1459bf2d5e20ab611c6e9091f9bf4a556bf9ec3ee7"
    )
    assert (
        agent_dataset_fingerprint(load_agent_evaluation_cases(EvaluationSplit.HOLDOUT))
        == "b68a78f687b81040e265aef6d934d4879b3180405159cb4d5ed10ad923ba4d58"
    )

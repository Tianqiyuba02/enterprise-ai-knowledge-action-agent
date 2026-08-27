from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from google.genai import types

from app.agent.client import (
    AGENT_SYSTEM_INSTRUCTION,
    AgentProviderTimeoutError,
    GeminiAgentClient,
    build_function_response_content,
    parse_model_content,
)
from app.agent.loop_models import AgentToolResponse
from app.agent.models import ProfileToolData, ToolResult, ToolResultStatus
from app.config import AgentSettings, KnowledgeSettings, Settings
from app.embeddings.client import GeminiDocumentEmbeddingClient
from app.grounding.client import GeminiGroundedGenerationClient
from app.llm.client import GeminiStructuredClient


def _response(content: types.Content):
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def _settings() -> Settings:
    return Settings(gemini_api_key="test-only-key", _env_file=None)


def _agent_settings() -> AgentSettings:
    return AgentSettings(_env_file=None)


def test_agent_client_uses_isolated_timeout_and_single_attempt_policy() -> None:
    sdk_client = Mock()
    with patch("app.agent.client.genai.Client", return_value=sdk_client) as client_factory:
        client = GeminiAgentClient(
            _settings(),
            AgentSettings(
                agent_timeout_seconds=60,
                agent_max_attempts=1,
                _env_file=None,
            ),
        )

    http_options = client_factory.call_args.kwargs["http_options"]
    assert http_options.timeout == 60_000
    assert http_options.retry_options.attempts == 1
    assert http_options.retry_options.initial_delay == 0.5
    assert http_options.retry_options.max_delay == 2.0
    assert http_options.retry_options.jitter == 0.25
    assert http_options.retry_options.http_status_codes == [
        408,
        429,
        500,
        502,
        503,
        504,
    ]

    client.start("Hello", date(2026, 8, 26))
    sdk_client.models.generate_content.assert_not_called()


def test_v1_grounding_and_embeddings_keep_shared_30_second_timeout() -> None:
    settings = Settings(
        gemini_api_key="test-only-key",
        gemini_timeout_seconds=30,
        gemini_max_attempts=2,
        _env_file=None,
    )
    knowledge = KnowledgeSettings(_env_file=None)
    with patch("app.llm.client.genai.Client") as v1_factory:
        GeminiStructuredClient(settings)
    with patch("app.grounding.client.genai.Client") as grounded_factory:
        GeminiGroundedGenerationClient(settings, knowledge)
    with patch("app.embeddings.client.genai.Client") as embedding_factory:
        GeminiDocumentEmbeddingClient(settings, knowledge)

    assert v1_factory.call_args.kwargs["http_options"].timeout == 30_000
    assert grounded_factory.call_args.kwargs["http_options"].timeout == 30_000
    assert embedding_factory.call_args.kwargs["http_options"].timeout == 30_000
    assert v1_factory.call_args.kwargs["http_options"].retry_options.attempts == 2
    assert grounded_factory.call_args.kwargs["http_options"].retry_options.attempts == 2
    assert embedding_factory.call_args.kwargs["http_options"].retry_options.attempts == 2


def _profile_result() -> ToolResult:
    return ToolResult.success(
        "get_my_profile",
        ProfileToolData(
            full_name="Alex Morgan",
            work_email="alex.morgan@example.test",
            location="Melbourne",
            employment_type="permanent",
            hours_per_day=7.6,
            work_days=("monday", "tuesday", "wednesday", "thursday", "friday"),
            timezone="Australia/Melbourne",
            is_active=True,
        ),
    )


def test_parse_final_text_without_exposing_provider_objects() -> None:
    turn = parse_model_content(
        types.Content(role="model", parts=[types.Part(text="  Hello employee.  ")])
    )

    assert turn.final_text == "Hello employee."
    assert turn.requested_calls == ()


def test_parse_single_and_multiple_function_calls_in_provider_order() -> None:
    content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(
                    id="call-1",
                    name="get_my_profile",
                    args=None,
                )
            ),
            types.Part(
                function_call=types.FunctionCall(
                    id="call-2",
                    name="get_my_ticket",
                    args={"ticket_id": "TKT-1001"},
                )
            ),
        ],
    )

    turn = parse_model_content(content)

    assert [call.name for call in turn.requested_calls] == [
        "get_my_profile",
        "get_my_ticket",
    ]
    assert [call.arguments for call in turn.requested_calls] == [
        {},
        {"ticket_id": "TKT-1001"},
    ]
    assert [call.provider_call_id for call in turn.requested_calls] == [
        "call-1",
        "call-2",
    ]


def test_parse_malformed_call_values_for_later_safe_dispatch() -> None:
    content = SimpleNamespace(
        parts=[
            SimpleNamespace(
                function_call=SimpleNamespace(
                    id="internal-id",
                    name=None,
                    args=["not", "an", "object"],
                ),
                text=None,
            )
        ]
    )

    turn = parse_model_content(content)

    assert turn.requested_calls[0].name is None
    assert turn.requested_calls[0].arguments == ["not", "an", "object"]
    assert turn.requested_calls[0].provider_call_id == "internal-id"


def test_function_response_serialization_uses_locked_tool_result_json_only() -> None:
    result = _profile_result()

    content = build_function_response_content(
        (
            AgentToolResponse(
                name="get_my_profile",
                result=result,
                provider_call_id="call-1",
            ),
        )
    )

    assert content.role == "user"
    function_response = content.parts[0].function_response
    assert function_response.id == "call-1"
    assert function_response.name == "get_my_profile"
    assert function_response.response == result.model_dump(mode="json")
    assert "employee_id" not in function_response.response["data"]
    assert AGENT_SYSTEM_INSTRUCTION not in str(function_response.response)


def test_gemini_session_keeps_automatic_execution_disabled_and_appends_tool_data() -> None:
    sdk_client = Mock()
    original_model_content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(
                    id="call-1",
                    name="get_my_profile",
                    args=None,
                ),
                thought_signature=b"synthetic-provider-context",
            )
        ],
    )
    sdk_client.models.generate_content.side_effect = [
        _response(original_model_content),
        _response(
            types.Content(
                role="model",
                parts=[types.Part(text="Your work email is alex.morgan@example.test.")],
            )
        ),
    ]
    session = GeminiAgentClient(
        _settings(),
        _agent_settings(),
        sdk_client=sdk_client,
    ).start("What is my work email?", date(2026, 8, 26))

    first_turn = session.next()
    second_turn = session.next(
        (
            AgentToolResponse(
                name="get_my_profile",
                result=_profile_result(),
                provider_call_id=first_turn.requested_calls[0].provider_call_id,
            ),
        )
    )

    assert second_turn.final_text == "Your work email is alex.morgan@example.test."
    first_call = sdk_client.models.generate_content.call_args_list[0]
    assert first_call.kwargs["model"] == "gemini-3.6-flash"
    assert first_call.kwargs["config"].automatic_function_calling.disable is True
    assert "UNTRUSTED DATA" in first_call.kwargs["config"].system_instruction
    assert "2026-08-26" in first_call.kwargs["config"].system_instruction
    assert "Preparation does not" in first_call.kwargs["config"].system_instruction
    assert "without exploratory READ calls" in first_call.kwargs["config"].system_instruction
    assert (
        "no actionable draft exists in the current turn"
        in first_call.kwargs["config"].system_instruction
    )
    assert 'Interpret "next <weekday>"' in first_call.kwargs["config"].system_instruction
    assert "ISO YYYY-MM-DD" in first_call.kwargs["config"].system_instruction
    assert (
        "Trusted relative-weekday resolution" not in first_call.kwargs["config"].system_instruction
    )
    second_call = sdk_client.models.generate_content.call_args_list[1]
    second_contents = second_call.kwargs["contents"]
    assert [content.role for content in second_contents] == ["user", "model", "user"]
    assert second_contents[1] is original_model_content
    assert second_contents[1].parts[0].thought_signature == b"synthetic-provider-context"
    assert len(second_contents[2].parts) == 1
    assert second_contents[2].parts[0].text is None
    function_response = second_contents[2].parts[0].function_response
    assert function_response.id == "call-1"
    assert function_response.name == "get_my_profile"
    assert function_response.response == _profile_result().model_dump(mode="json")
    assert second_call.kwargs["config"] is first_call.kwargs["config"]


def test_gemini_session_preserves_parallel_call_content_ids_names_and_order() -> None:
    sdk_client = Mock()
    original_model_content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(
                    id="call-profile",
                    name="get_my_profile",
                    args=None,
                ),
                thought_signature=b"synthetic-parallel-context",
            ),
            types.Part(
                function_call=types.FunctionCall(
                    id="call-ticket",
                    name="get_my_ticket",
                    args={"ticket_id": "TKT-2001"},
                )
            ),
        ],
    )
    sdk_client.models.generate_content.side_effect = [
        _response(original_model_content),
        _response(
            types.Content(
                role="model",
                parts=[types.Part(text="The tool results were handled safely.")],
            )
        ),
    ]
    session = GeminiAgentClient(
        _settings(),
        _agent_settings(),
        sdk_client=sdk_client,
    ).start("Read my profile and ticket.", date(2026, 8, 26))

    first_turn = session.next()
    second_turn = session.next(
        (
            AgentToolResponse(
                name="get_my_profile",
                result=_profile_result(),
                provider_call_id=first_turn.requested_calls[0].provider_call_id,
            ),
            AgentToolResponse(
                name="get_my_ticket",
                result=ToolResult.failure(
                    "get_my_ticket",
                    ToolResultStatus.NOT_FOUND_OR_INACCESSIBLE,
                    "The requested resource was not found or is inaccessible.",
                ),
                provider_call_id=first_turn.requested_calls[1].provider_call_id,
            ),
        )
    )

    assert second_turn.final_text == "The tool results were handled safely."
    second_contents = sdk_client.models.generate_content.call_args_list[1].kwargs["contents"]
    assert [content.role for content in second_contents] == ["user", "model", "user"]
    assert second_contents.count(original_model_content) == 1
    assert second_contents[1] is original_model_content
    assert [part.function_call.id for part in second_contents[1].parts] == [
        "call-profile",
        "call-ticket",
    ]
    assert second_contents[1].parts[0].thought_signature == b"synthetic-parallel-context"
    response_parts = second_contents[2].parts
    assert all(part.text is None for part in response_parts)
    assert [part.function_response.id for part in response_parts] == [
        "call-profile",
        "call-ticket",
    ]
    assert [part.function_response.name for part in response_parts] == [
        "get_my_profile",
        "get_my_ticket",
    ]


def test_provider_timeout_is_safe() -> None:
    sdk_client = Mock()
    sdk_client.models.generate_content.side_effect = httpx.ReadTimeout(
        "sensitive provider request detail"
    )
    session = GeminiAgentClient(
        _settings(),
        _agent_settings(),
        sdk_client=sdk_client,
    ).start("Hello", date(2026, 8, 26))

    with pytest.raises(AgentProviderTimeoutError) as captured:
        session.next()

    assert "sensitive" not in str(captured.value)


def test_start_exposes_request_scoped_relative_weekday_as_trusted_context() -> None:
    sdk_client = Mock()
    sdk_client.models.generate_content.return_value = _response(
        types.Content(role="model", parts=[types.Part(text="Ready.")])
    )
    session = GeminiAgentClient(
        _settings(),
        _agent_settings(),
        sdk_client=sdk_client,
    ).start("Please prepare annual leave for next Friday.", date(2024, 1, 3))

    session.next()

    instruction = sdk_client.models.generate_content.call_args.kwargs["config"].system_instruction
    assert "Trusted current date in Australia/Melbourne: 2024-01-03." in instruction
    assert "Trusted relative-weekday resolution for this request:" in instruction
    assert "- next friday = 2024-01-05" in instruction
    assert "UNTRUSTED DATA" in instruction
    assert instruction.index("Trusted relative-weekday resolution") > instruction.index(
        "UNTRUSTED DATA"
    )
    assert "2024-01-12" not in instruction
    assert "relative_weekday" not in instruction
    assert "resolve_next_weekday" not in instruction


def test_start_does_not_claim_resolution_for_unsupported_date_phrases() -> None:
    sdk_client = Mock()
    sdk_client.models.generate_content.return_value = _response(
        types.Content(role="model", parts=[types.Part(text="Ready.")])
    )
    GeminiAgentClient(
        _settings(),
        _agent_settings(),
        sdk_client=sdk_client,
    ).start("Please prepare annual leave sometime next week.", date(2024, 1, 3)).next()

    instruction = sdk_client.models.generate_content.call_args.kwargs["config"].system_instruction
    assert "Trusted relative-weekday resolution" not in instruction
    assert "next friday" not in instruction

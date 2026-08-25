from types import SimpleNamespace
from unittest.mock import Mock

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
from app.agent.models import ProfileToolData, ToolResult
from app.config import AgentSettings, Settings


def _response(content: types.Content):
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def _settings() -> Settings:
    return Settings(gemini_api_key="test-only-key", _env_file=None)


def _agent_settings() -> AgentSettings:
    return AgentSettings(_env_file=None)


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

    assert content.role == "tool"
    function_response = content.parts[0].function_response
    assert function_response.id == "call-1"
    assert function_response.name == "get_my_profile"
    assert function_response.response == result.model_dump(mode="json")
    assert "employee_id" not in function_response.response["data"]
    assert AGENT_SYSTEM_INSTRUCTION not in str(function_response.response)


def test_gemini_session_keeps_automatic_execution_disabled_and_appends_tool_data() -> None:
    sdk_client = Mock()
    sdk_client.models.generate_content.side_effect = [
        _response(
            types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            id="call-1",
                            name="get_my_profile",
                            args=None,
                        )
                    )
                ],
            )
        ),
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
    ).start("What is my work email?")

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
    second_contents = sdk_client.models.generate_content.call_args_list[1].kwargs["contents"]
    assert [content.role for content in second_contents] == ["user", "model", "tool"]


def test_provider_timeout_is_safe() -> None:
    sdk_client = Mock()
    sdk_client.models.generate_content.side_effect = httpx.ReadTimeout(
        "sensitive provider request detail"
    )
    session = GeminiAgentClient(
        _settings(),
        _agent_settings(),
        sdk_client=sdk_client,
    ).start("Hello")

    with pytest.raises(AgentProviderTimeoutError) as captured:
        session.next()

    assert "sensitive" not in str(captured.value)

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from google.genai import errors

from app.config import ConfigurationError, Settings, load_settings
from app.llm.client import (
    AuthenticationError,
    GeminiStructuredClient,
    InvalidModelResponseError,
    InvalidQuestionError,
    ProviderServiceError,
    ProviderTimeoutError,
    RateLimitError,
)
from app.llm.models import QuestionCategory


@pytest.fixture
def settings() -> Settings:
    return Settings(gemini_api_key="test-only-key", _env_file=None)


def test_mocked_provider_success_returns_validated_model(settings: Settings) -> None:
    sdk_client = Mock()
    sdk_client.models.generate_content.return_value = SimpleNamespace(
        text=json.dumps(
            {
                "category": "expense",
                "summary": "Submit a reimbursement request.",
                "requires_action": True,
                "confidence": 0.91,
            }
        )
    )
    client = GeminiStructuredClient(settings, sdk_client=sdk_client)

    result = client.analyze("Please reimburse my taxi fare")

    assert result.category is QuestionCategory.EXPENSE
    assert result.requires_action is True
    call = sdk_client.models.generate_content.call_args
    assert call.kwargs["model"] == settings.gemini_model
    assert call.kwargs["config"].response_mime_type == "application/json"
    assert call.kwargs["config"].response_json_schema is not None
    assert call.kwargs["config"].thinking_config.thinking_level == "MINIMAL"
    assert call.kwargs["config"].automatic_function_calling.disable is True


def test_whitespace_only_question_does_not_call_provider(settings: Settings) -> None:
    sdk_client = Mock()
    client = GeminiStructuredClient(settings, sdk_client=sdk_client)

    with pytest.raises(InvalidQuestionError, match="non-empty question"):
        client.analyze("  \n\t  ")

    sdk_client.models.generate_content.assert_not_called()


def test_question_over_4000_characters_does_not_call_provider(settings: Settings) -> None:
    sdk_client = Mock()
    client = GeminiStructuredClient(settings, sdk_client=sdk_client)

    with pytest.raises(InvalidQuestionError, match="4,000 characters or fewer"):
        client.analyze("x" * 4_001)

    sdk_client.models.generate_content.assert_not_called()


@pytest.mark.parametrize(
    "response_text",
    [
        "not json",
        json.dumps(
            {
                "category": "unsupported",
                "summary": "Invalid category.",
                "requires_action": False,
                "confidence": 0.5,
            }
        ),
        json.dumps(
            {
                "category": "it",
                "summary": "Invalid confidence.",
                "requires_action": False,
                "confidence": 2.0,
            }
        ),
        "",
    ],
)
def test_malformed_or_schema_invalid_provider_output_is_rejected(
    settings: Settings, response_text: str
) -> None:
    sdk_client = Mock()
    sdk_client.models.generate_content.return_value = SimpleNamespace(text=response_text)

    with pytest.raises(InvalidModelResponseError, match="invalid structured response"):
        GeminiStructuredClient(settings, sdk_client=sdk_client).analyze("A question")


@pytest.mark.parametrize(
    ("provider_error", "expected_error"),
    [
        (
            errors.ClientError(
                401,
                {"error": {"status": "UNAUTHENTICATED", "message": "secret detail"}},
            ),
            AuthenticationError,
        ),
        (
            errors.ClientError(
                400,
                {
                    "error": {
                        "status": "INVALID_ARGUMENT",
                        "details": [{"reason": "API_KEY_INVALID"}],
                    }
                },
            ),
            AuthenticationError,
        ),
        (
            errors.ClientError(
                429,
                {"error": {"status": "RESOURCE_EXHAUSTED", "message": "secret detail"}},
            ),
            RateLimitError,
        ),
        (
            errors.ServerError(
                504,
                {"error": {"status": "DEADLINE_EXCEEDED", "message": "secret detail"}},
            ),
            ProviderTimeoutError,
        ),
        (
            errors.ServerError(
                503,
                {"error": {"status": "UNAVAILABLE", "message": "secret detail"}},
            ),
            ProviderServiceError,
        ),
        (httpx.ReadTimeout("request timed out"), ProviderTimeoutError),
        (httpx.ConnectError("connection failed"), ProviderServiceError),
    ],
)
def test_mocked_provider_errors_are_mapped_to_safe_failures(
    settings: Settings,
    provider_error: Exception,
    expected_error: type[Exception],
) -> None:
    sdk_client = Mock()
    sdk_client.models.generate_content.side_effect = provider_error

    with pytest.raises(expected_error) as captured:
        GeminiStructuredClient(settings, sdk_client=sdk_client).analyze("A question")

    assert "secret detail" not in str(captured.value)


def test_missing_configuration_has_safe_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        load_settings()

"""Single-provider Gemini client for V0 structured output."""

from typing import Any

import httpx
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from app.config import Settings
from app.llm.models import QuestionAnalysis

SYSTEM_INSTRUCTION = """You classify one internal employee question for a V0 demo.
Choose exactly one category from hr, it, expense, travel, or general.
Write a concise summary of the user's request.
Set requires_action to true only when the user is asking for an operational follow-up,
not when they only ask for information. Return only data matching the supplied schema.
"""


class LLMClientError(RuntimeError):
    """Base class for safe, user-facing LLM client failures."""


class InvalidQuestionError(LLMClientError):
    """Raised when a question cannot be sent to the provider."""


class AuthenticationError(LLMClientError):
    """Raised when Gemini rejects the configured credential."""


class RateLimitError(LLMClientError):
    """Raised when Gemini rate limits the request."""


class ProviderTimeoutError(LLMClientError):
    """Raised when the Gemini request exceeds its configured timeout."""


class ProviderServiceError(LLMClientError):
    """Raised when Gemini or its network transport is unavailable."""


class InvalidModelResponseError(LLMClientError):
    """Raised when provider output does not pass local schema validation."""


class GeminiStructuredClient:
    """Submit one prompt to Gemini and return only locally validated output."""

    def __init__(self, settings: Settings, sdk_client: Any | None = None) -> None:
        self._settings = settings
        self._client = sdk_client or genai.Client(
            api_key=settings.gemini_api_key.get_secret_value(),
            http_options=types.HttpOptions(
                timeout=settings.gemini_timeout_seconds * 1_000,
                retry_options=types.HttpRetryOptions(
                    attempts=settings.gemini_max_attempts,
                    initial_delay=0.5,
                    max_delay=2.0,
                    jitter=0.25,
                    http_status_codes=[408, 429, 500, 502, 503, 504],
                ),
            ),
        )

    def analyze(self, question: str) -> QuestionAnalysis:
        """Classify a question through Gemini and validate the returned JSON locally."""

        cleaned_question = question.strip()
        if not cleaned_question:
            raise InvalidQuestionError("Please provide a non-empty question.")
        if len(cleaned_question) > 4_000:
            raise InvalidQuestionError("The question is too long; use 4,000 characters or fewer.")

        try:
            response = self._client.models.generate_content(
                model=self._settings.gemini_model,
                contents=cleaned_question,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_json_schema=QuestionAnalysis.model_json_schema(),
                    max_output_tokens=512,
                    thinking_config=types.ThinkingConfig(
                        thinking_level=types.ThinkingLevel.MINIMAL
                    ),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except errors.APIError as exc:
            raise _safe_provider_error(exc) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("The model request timed out. Please try again.") from exc
        except httpx.TransportError as exc:
            raise ProviderServiceError(
                "The model service is temporarily unavailable. Please try again later."
            ) from exc

        try:
            response_text = response.text
            if not isinstance(response_text, str) or not response_text.strip():
                raise ValueError("empty model response")
            return QuestionAnalysis.model_validate_json(response_text)
        except (AttributeError, TypeError, ValueError, ValidationError) as exc:
            raise InvalidModelResponseError(
                "The model returned an invalid structured response. Please try again."
            ) from exc


def _safe_provider_error(exc: errors.APIError) -> LLMClientError:
    """Map provider details to stable messages without exposing those details."""

    code = int(exc.code or 0)
    status = str(exc.status or "").upper()
    provider_reason = str(exc.details or "").upper()

    if (
        code in {401, 403}
        or status in {"UNAUTHENTICATED", "PERMISSION_DENIED"}
        or "API_KEY_INVALID" in provider_reason
    ):
        return AuthenticationError(
            "The model service rejected the API key. Check GEMINI_API_KEY and try again."
        )
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return RateLimitError(
            "The model service is busy or the request limit was reached. Please try again later."
        )
    if code in {408, 504} or status == "DEADLINE_EXCEEDED":
        return ProviderTimeoutError("The model request timed out. Please try again.")
    return ProviderServiceError(
        "The model service could not complete the request. Please try again later."
    )

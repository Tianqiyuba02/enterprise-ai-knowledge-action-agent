"""Separate Gemini boundary for structured, evidence-grounded V2 answers."""

from typing import Any

import httpx
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from app.config import Settings
from app.grounding.models import GroundedAnswerDraft
from app.grounding.prompt import build_grounded_prompt
from app.grounding.references import ReferencedEvidence


class GroundedGenerationError(RuntimeError):
    """Base class for safe grounded-generation failures."""


class GroundedAuthenticationError(GroundedGenerationError):
    """Raised when the configured generation credential is rejected."""


class GroundedRateLimitError(GroundedGenerationError):
    """Raised when grounded generation is rate limited."""


class GroundedTimeoutError(GroundedGenerationError):
    """Raised when grounded generation exceeds its bounded timeout."""


class GroundedServiceError(GroundedGenerationError):
    """Raised when the generation provider or transport is unavailable."""


class InvalidGroundedResponseError(GroundedGenerationError):
    """Raised when model output fails strict local grounding validation."""


class GeminiGroundedGenerationClient:
    """Generate one strict draft from a bounded referenced evidence set."""

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

    def generate(
        self,
        question: str,
        referenced_evidence: tuple[ReferencedEvidence, ...],
    ) -> GroundedAnswerDraft:
        prompt = build_grounded_prompt(question, referenced_evidence)
        try:
            response = self._client.models.generate_content(
                model=self._settings.gemini_model,
                contents=prompt.user_content,
                config=types.GenerateContentConfig(
                    system_instruction=prompt.system_instruction,
                    response_mime_type="application/json",
                    response_json_schema=GroundedAnswerDraft.model_json_schema(),
                    max_output_tokens=1_024,
                    thinking_config=types.ThinkingConfig(
                        thinking_level=types.ThinkingLevel.MINIMAL
                    ),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except errors.APIError as exc:
            raise _safe_grounded_provider_error(exc) from exc
        except httpx.TimeoutException as exc:
            raise GroundedTimeoutError(
                "The grounded generation request timed out. Please try again."
            ) from exc
        except httpx.TransportError as exc:
            raise GroundedServiceError(
                "The grounded generation service is temporarily unavailable."
            ) from exc

        try:
            response_text = response.text
            if not isinstance(response_text, str) or not response_text.strip():
                raise ValueError("empty grounded response")
            return GroundedAnswerDraft.model_validate_json(response_text)
        except (AttributeError, TypeError, ValueError, ValidationError) as exc:
            raise InvalidGroundedResponseError(
                "The grounded generation service returned an invalid response."
            ) from exc


def _safe_grounded_provider_error(exc: errors.APIError) -> GroundedGenerationError:
    code = int(exc.code or 0)
    status = str(exc.status or "").upper()
    provider_reason = str(exc.details or "").upper()

    if (
        code in {401, 403}
        or status in {"UNAUTHENTICATED", "PERMISSION_DENIED"}
        or "API_KEY_INVALID" in provider_reason
    ):
        return GroundedAuthenticationError(
            "The grounded generation service rejected its configured credential."
        )
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return GroundedRateLimitError(
            "The grounded generation service is busy. Please try again later."
        )
    if code in {408, 504} or status == "DEADLINE_EXCEEDED":
        return GroundedTimeoutError("The grounded generation request timed out. Please try again.")
    return GroundedServiceError("The grounded generation service could not complete the request.")

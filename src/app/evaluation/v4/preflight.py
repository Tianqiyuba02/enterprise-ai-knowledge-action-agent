"""Non-scored provider preflight. Cannot create V4 actions or score development cases."""

from datetime import UTC, datetime
from typing import Any, Literal

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from app.agent.client import extract_provider_usage
from app.agent.loop_models import AgentProviderUsage
from app.agent.provider_failures import AgentProviderFailureDetail, classify_provider_failure
from app.config import AgentSettings, Settings
from app.evaluation.v4.clock import V4_DEVELOPMENT_BUSINESS_DATE
from app.evaluation.v4.fingerprints import (
    PROVIDER_AUTOMATIC_FUNCTION_CALLING,
    PROVIDER_MAX_OUTPUT_TOKENS,
    PROVIDER_TEMPERATURE,
    PROVIDER_THINKING_LEVEL,
)
from app.evaluation.v4.transport import (
    PREFLIGHT_CREATES_V4_ACTION,
    PREFLIGHT_IS_DEVELOPMENT_CASE,
    PREFLIGHT_IS_HOLDOUT_CASE,
    PREFLIGHT_SCORED,
    V4_EVALUATOR_VERSION,
)

PREFLIGHT_KIND: Literal["provider_preflight"] = "provider_preflight"
PREFLIGHT_INSTRUCTION = (
    "You are a provider connectivity probe. Reply with the single word READY. Do not call tools."
)
PREFLIGHT_USER_MESSAGE = "Reply with the single word READY."


class ProviderPreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["provider_preflight"] = PREFLIGHT_KIND
    evaluator_version: Literal["v4-product-eval-2"] = V4_EVALUATOR_VERSION
    scored: Literal[False] = PREFLIGHT_SCORED
    development_case: Literal[False] = PREFLIGHT_IS_DEVELOPMENT_CASE
    holdout_case: Literal[False] = PREFLIGHT_IS_HOLDOUT_CASE
    v4_action_created: Literal[False] = PREFLIGHT_CREATES_V4_ACTION
    business_mutation: Literal[False] = False
    completed: bool
    provider_failure: AgentProviderFailureDetail | None = None
    usage: AgentProviderUsage | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FailedPreflightBlocksDevelopmentRun(RuntimeError):
    """Raised when a failed preflight must prevent a development evaluation start."""


class ProviderPreflight:
    """Exercise the configured Gemini client without tools, AgentService, or V4 actions."""

    def __init__(
        self,
        settings: Settings,
        agent_settings: AgentSettings,
        *,
        sdk_client: Any | None = None,
    ) -> None:
        from google import genai

        self._model = agent_settings.agent_model
        self._client = sdk_client or genai.Client(
            api_key=settings.gemini_api_key.get_secret_value(),
            http_options=types.HttpOptions(
                timeout=agent_settings.agent_timeout_seconds * 1_000,
                retry_options=types.HttpRetryOptions(
                    attempts=agent_settings.agent_max_attempts,
                    initial_delay=0.5,
                    max_delay=2.0,
                    jitter=0.25,
                    http_status_codes=[408, 429, 500, 502, 503, 504],
                ),
            ),
        )
        thinking = types.ThinkingLevel.MINIMAL
        if PROVIDER_THINKING_LEVEL != "MINIMAL":
            raise ValueError("preflight must use the frozen MINIMAL thinking level")
        if PROVIDER_TEMPERATURE != 0:
            raise ValueError("preflight must use frozen temperature 0")
        if PROVIDER_AUTOMATIC_FUNCTION_CALLING is not False:
            raise ValueError("preflight must keep automatic function calling disabled")
        self._config = types.GenerateContentConfig(
            system_instruction=(
                f"{PREFLIGHT_INSTRUCTION}\nTrusted current date in Australia/Melbourne: "
                f"{V4_DEVELOPMENT_BUSINESS_DATE.isoformat()}."
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=min(16, PROVIDER_MAX_OUTPUT_TOKENS),
            thinking_config=types.ThinkingConfig(thinking_level=thinking),
            temperature=PROVIDER_TEMPERATURE,
        )

    def run(self) -> ProviderPreflightResult:
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=PREFLIGHT_USER_MESSAGE,
                config=self._config,
            )
        except Exception as exc:
            return ProviderPreflightResult(
                completed=False,
                provider_failure=classify_provider_failure(exc),
            )
        candidates = getattr(response, "candidates", None)
        if not isinstance(candidates, list) or not candidates:
            return ProviderPreflightResult(completed=False)
        return ProviderPreflightResult(
            completed=True,
            usage=extract_provider_usage(response),
        )


def require_successful_preflight(result: ProviderPreflightResult) -> None:
    if result.scored or result.development_case or result.holdout_case:
        raise FailedPreflightBlocksDevelopmentRun("preflight artifact is not a scored case")
    if result.v4_action_created or result.business_mutation:
        raise FailedPreflightBlocksDevelopmentRun("preflight must not mutate V4 business state")
    if not result.completed:
        raise FailedPreflightBlocksDevelopmentRun(
            "failed provider preflight prevents automatic development run start"
        )

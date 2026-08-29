"""Non-scored mirrored provider diagnostic. Not a development or holdout evaluation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from app.agent.client import AgentProviderError, GeminiAgentClient
from app.agent.contracts import V3_TOOL_ALLOWLIST
from app.agent.loop_models import AgentProviderUsage
from app.agent.provider import build_provider_function_declarations
from app.agent.provider_failures import AgentProviderFailureDetail, classify_provider_failure
from app.config import AgentSettings, Settings
from app.evaluation.v4.clock import (
    V4_DEVELOPMENT_BUSINESS_DATE,
    V4_DEVELOPMENT_BUSINESS_TIMEZONE,
    business_clock_identity,
)
from app.evaluation.v4.fingerprints import (
    PROVIDER_AUTOMATIC_FUNCTION_CALLING,
    PROVIDER_MAX_OUTPUT_TOKENS,
    PROVIDER_TEMPERATURE,
    PROVIDER_THINKING_LEVEL,
    business_clock_fingerprint,
    evaluation_subject_fingerprint,
    evaluation_transport_fingerprint,
    provider_config_fingerprint,
    sha256_json,
    sha256_text,
)
from app.evaluation.v4.preflight import PREFLIGHT_USER_MESSAGE, ProviderPreflight
from app.evaluation.v4.transport import (
    DEFAULT_PREFLIGHT_RESULTS_DIR,
    DIAGNOSTIC_PAIR_FILENAME_PREFIX,
    DIAGNOSTIC_PAIR_ORDER,
    DIAGNOSTIC_SCORED,
    PREFLIGHT_CREATES_V4_ACTION,
    PREFLIGHT_IS_DEVELOPMENT_CASE,
    PREFLIGHT_IS_HOLDOUT_CASE,
    RESERVED_EVIDENCE_NAME_PREFIXES,
    RESERVED_PREFLIGHT_EVIDENCE_PATHS,
    V4_EVALUATOR_VERSION,
)

DIAGNOSTIC_VERSION: Literal["v4-provider-diagnostic-1"] = "v4-provider-diagnostic-1"
DIAGNOSTIC_KIND: Literal["provider_diagnostic_pair"] = "provider_diagnostic_pair"
DIAGNOSTIC_USER_MESSAGE = PREFLIGHT_USER_MESSAGE
AGENT_SHAPED_CONTENTS_REPRESENTATION: Literal["content_user_role"] = "content_user_role"
MINIMAL_CONTENTS_REPRESENTATION: Literal["string"] = "string"
ALLOWED_FUNCTION_CALL_NAMES = frozenset(name.value for name in V3_TOOL_ALLOWLIST)


class DiagnosticRequestSizeInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    user_chars: int
    user_bytes: int
    instruction_chars: int
    instruction_bytes: int
    tool_declaration_count: int
    tool_schema_bytes: int
    contents_count: int
    max_output_tokens: int
    envelope_bytes: int


class DiagnosticProbeObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    probe_type: Literal["agent_shaped", "minimal_control"]
    scored: Literal[False] = DIAGNOSTIC_SCORED
    development_case: Literal[False] = PREFLIGHT_IS_DEVELOPMENT_CASE
    holdout_case: Literal[False] = PREFLIGHT_IS_HOLDOUT_CASE
    v4_action_created: Literal[False] = PREFLIGHT_CREATES_V4_ACTION
    business_mutation: Literal[False] = False
    tool_executed: Literal[False] = False
    started_at: datetime
    finished_at: datetime
    completed: bool
    provider_failure: AgentProviderFailureDetail | None = None
    usage: AgentProviderUsage | None = None
    response_kind: Literal["text", "function_call"] | None = None
    function_call_tool_name: str | None = None
    request_shape_fingerprint: str
    request_size: DiagnosticRequestSizeInventory


class ProviderDiagnosticPairResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["provider_diagnostic_pair"] = DIAGNOSTIC_KIND
    diagnostic_version: Literal["v4-provider-diagnostic-1"] = DIAGNOSTIC_VERSION
    evaluator_version: Literal["v4-product-eval-2"] = V4_EVALUATOR_VERSION
    scored: Literal[False] = DIAGNOSTIC_SCORED
    development_case: Literal[False] = PREFLIGHT_IS_DEVELOPMENT_CASE
    holdout_case: Literal[False] = PREFLIGHT_IS_HOLDOUT_CASE
    v4_action_created: Literal[False] = PREFLIGHT_CREATES_V4_ACTION
    business_mutation: Literal[False] = False
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    order: tuple[Literal["agent_shaped"], Literal["minimal_control"]] = DIAGNOSTIC_PAIR_ORDER
    provider_config_fingerprint: str
    evaluation_subject_fingerprint: str
    evaluation_transport_fingerprint: str
    business_clock: dict[str, str]
    business_clock_fingerprint: str
    agent_shaped: DiagnosticProbeObservation
    minimal_control: DiagnosticProbeObservation


def _utf8_size(value: str) -> tuple[int, int]:
    encoded = value.encode("utf-8")
    return len(value), len(encoded)


def _json_bytes(value: object) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def _tool_schema_inventory() -> tuple[int, int, object]:
    declarations = []
    schema_bytes = 0
    for declaration in build_provider_function_declarations():
        schema = declaration.parameters_json_schema
        declarations.append(
            {
                "name": declaration.name,
                "description": declaration.description,
                "parameters": schema,
            }
        )
        schema_bytes += _json_bytes(schema)
    return len(declarations), schema_bytes, declarations


def request_shape_fingerprint(
    *,
    probe_type: Literal["agent_shaped", "minimal_control"],
    model: str,
    max_output_tokens: int,
    system_instruction: str,
    user_content: str,
    contents_representation: str,
    tool_declarations: object | None,
) -> str:
    return sha256_json(
        {
            "probe_type": probe_type,
            "model": model,
            "thinking_level": PROVIDER_THINKING_LEVEL,
            "temperature": PROVIDER_TEMPERATURE,
            "max_output_tokens": max_output_tokens,
            "automatic_function_calling": PROVIDER_AUTOMATIC_FUNCTION_CALLING,
            "system_instruction_sha256": sha256_text(system_instruction),
            "user_content_sha256": sha256_text(user_content),
            "tool_declaration_sha256": (
                None if tool_declarations is None else sha256_json(tool_declarations)
            ),
            "contents_representation": contents_representation,
            "trusted_business_date": V4_DEVELOPMENT_BUSINESS_DATE.isoformat(),
            "timezone": V4_DEVELOPMENT_BUSINESS_TIMEZONE,
        }
    )


def _size_inventory(
    *,
    user_content: str,
    system_instruction: str,
    tool_declaration_count: int,
    tool_schema_bytes: int,
    contents: object,
    max_output_tokens: int,
    model: str,
    config: types.GenerateContentConfig,
) -> DiagnosticRequestSizeInventory:
    user_chars, user_bytes = _utf8_size(user_content)
    instruction_chars, instruction_bytes = _utf8_size(system_instruction)
    envelope = {
        "model": model,
        "contents": contents
        if isinstance(contents, str)
        else [item.model_dump(mode="json", exclude_none=True) for item in contents],
        "config": config.model_dump(mode="json", exclude_none=True),
    }
    return DiagnosticRequestSizeInventory(
        user_chars=user_chars,
        user_bytes=user_bytes,
        instruction_chars=instruction_chars,
        instruction_bytes=instruction_bytes,
        tool_declaration_count=tool_declaration_count,
        tool_schema_bytes=tool_schema_bytes,
        contents_count=1 if isinstance(contents, str) else len(contents),
        max_output_tokens=max_output_tokens,
        envelope_bytes=_json_bytes(envelope),
    )


def _safe_function_call_name(name: str | None) -> str | None:
    if name is None:
        return None
    return name if name in ALLOWED_FUNCTION_CALL_NAMES else None


def run_agent_shaped_probe(
    settings: Settings,
    agent_settings: AgentSettings,
    *,
    sdk_client: Any | None = None,
) -> DiagnosticProbeObservation:
    """One first-round Agent-shaped generate_content. Never executes tools."""

    if PROVIDER_THINKING_LEVEL != "MINIMAL":
        raise ValueError("diagnostic must use the frozen MINIMAL thinking level")
    if PROVIDER_TEMPERATURE != 0:
        raise ValueError("diagnostic must use frozen temperature 0")
    if PROVIDER_AUTOMATIC_FUNCTION_CALLING is not False:
        raise ValueError("diagnostic must keep automatic function calling disabled")
    if PROVIDER_MAX_OUTPUT_TOKENS != 1_024:
        raise ValueError("agent-shaped diagnostic must use Agent max_output_tokens")

    provider = GeminiAgentClient(settings, agent_settings, sdk_client=sdk_client)
    session = provider.start(DIAGNOSTIC_USER_MESSAGE, V4_DEVELOPMENT_BUSINESS_DATE)
    instruction = str(session._config.system_instruction)
    tool_count, tool_schema_bytes, tool_declarations = _tool_schema_inventory()
    shape = request_shape_fingerprint(
        probe_type="agent_shaped",
        model=session._model,
        max_output_tokens=PROVIDER_MAX_OUTPUT_TOKENS,
        system_instruction=instruction,
        user_content=DIAGNOSTIC_USER_MESSAGE,
        contents_representation=AGENT_SHAPED_CONTENTS_REPRESENTATION,
        tool_declarations=tool_declarations,
    )
    size = _size_inventory(
        user_content=DIAGNOSTIC_USER_MESSAGE,
        system_instruction=instruction,
        tool_declaration_count=tool_count,
        tool_schema_bytes=tool_schema_bytes,
        contents=list(session._contents),
        max_output_tokens=PROVIDER_MAX_OUTPUT_TOKENS,
        model=session._model,
        config=session._config,
    )
    started = datetime.now(UTC)
    try:
        turn = session.next()
    except AgentProviderError as exc:
        return DiagnosticProbeObservation(
            probe_type="agent_shaped",
            started_at=started,
            finished_at=datetime.now(UTC),
            completed=False,
            provider_failure=exc.failure,
            request_shape_fingerprint=shape,
            request_size=size,
        )
    except Exception as exc:
        return DiagnosticProbeObservation(
            probe_type="agent_shaped",
            started_at=started,
            finished_at=datetime.now(UTC),
            completed=False,
            provider_failure=classify_provider_failure(exc),
            request_shape_fingerprint=shape,
            request_size=size,
        )
    finished = datetime.now(UTC)
    if turn.requested_calls:
        return DiagnosticProbeObservation(
            probe_type="agent_shaped",
            started_at=started,
            finished_at=finished,
            completed=True,
            usage=turn.usage,
            response_kind="function_call",
            function_call_tool_name=_safe_function_call_name(turn.requested_calls[0].name),
            request_shape_fingerprint=shape,
            request_size=size,
        )
    return DiagnosticProbeObservation(
        probe_type="agent_shaped",
        started_at=started,
        finished_at=finished,
        completed=True,
        usage=turn.usage,
        response_kind="text",
        request_shape_fingerprint=shape,
        request_size=size,
    )


def run_minimal_control_probe(
    settings: Settings,
    agent_settings: AgentSettings,
    *,
    sdk_client: Any | None = None,
) -> DiagnosticProbeObservation:
    """Reuse committed ProviderPreflight request behavior exactly once."""

    preflight = ProviderPreflight(settings, agent_settings, sdk_client=sdk_client)
    instruction = str(preflight._config.system_instruction)
    shape = request_shape_fingerprint(
        probe_type="minimal_control",
        model=preflight._model,
        max_output_tokens=min(16, PROVIDER_MAX_OUTPUT_TOKENS),
        system_instruction=instruction,
        user_content=PREFLIGHT_USER_MESSAGE,
        contents_representation=MINIMAL_CONTENTS_REPRESENTATION,
        tool_declarations=None,
    )
    size = _size_inventory(
        user_content=PREFLIGHT_USER_MESSAGE,
        system_instruction=instruction,
        tool_declaration_count=0,
        tool_schema_bytes=0,
        contents=PREFLIGHT_USER_MESSAGE,
        max_output_tokens=min(16, PROVIDER_MAX_OUTPUT_TOKENS),
        model=preflight._model,
        config=preflight._config,
    )
    started = datetime.now(UTC)
    result = preflight.run()
    finished = datetime.now(UTC)
    return DiagnosticProbeObservation(
        probe_type="minimal_control",
        started_at=started,
        finished_at=finished,
        completed=result.completed,
        provider_failure=result.provider_failure,
        usage=result.usage,
        response_kind="text" if result.completed else None,
        request_shape_fingerprint=shape,
        request_size=size,
    )


def run_mirrored_diagnostic_pair(
    settings: Settings,
    agent_settings: AgentSettings,
    *,
    sdk_client: Any | None = None,
) -> ProviderDiagnosticPairResult:
    """Run Agent-shaped then minimal control. Two provider calls. No retries."""

    agent_shaped = run_agent_shaped_probe(settings, agent_settings, sdk_client=sdk_client)
    minimal_control = run_minimal_control_probe(settings, agent_settings, sdk_client=sdk_client)
    return ProviderDiagnosticPairResult(
        provider_config_fingerprint=provider_config_fingerprint(agent_settings),
        evaluation_subject_fingerprint=evaluation_subject_fingerprint(),
        evaluation_transport_fingerprint=evaluation_transport_fingerprint(),
        business_clock=business_clock_identity(),
        business_clock_fingerprint=business_clock_fingerprint(),
        agent_shaped=agent_shaped,
        minimal_control=minimal_control,
    )


def diagnostic_pair_artifact_path(
    generated_at: datetime,
    *,
    directory: Path | None = None,
) -> Path:
    moment = generated_at if generated_at.tzinfo is not None else generated_at.replace(tzinfo=UTC)
    stamp = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory if directory is not None else DEFAULT_PREFLIGHT_RESULTS_DIR
    return target / f"{DIAGNOSTIC_PAIR_FILENAME_PREFIX}{stamp}.json"


def persist_diagnostic_pair_result(
    result: ProviderDiagnosticPairResult,
    *,
    path: Path | None = None,
    directory: Path | None = None,
) -> Path:
    """Write the safe pair artifact. Does not call the provider."""

    target = (
        path
        if path is not None
        else diagnostic_pair_artifact_path(result.generated_at, directory=directory)
    )
    reserved = {item.resolve() for item in RESERVED_PREFLIGHT_EVIDENCE_PATHS}
    reserved_names = {item.name for item in RESERVED_PREFLIGHT_EVIDENCE_PATHS}
    if target.resolve() in reserved or target.name in reserved_names:
        raise ValueError("refusing to overwrite reserved evaluation evidence")
    if any(target.name.startswith(prefix) for prefix in RESERVED_EVIDENCE_NAME_PREFIXES):
        raise ValueError("refusing to overwrite reserved evaluation evidence")
    if target.exists():
        raise ValueError("diagnostic pair artifact already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return target

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors, types

from app.agent.dispatcher import ToolDispatcher
from app.agent.service import AgentService
from app.api.assistant_application import AssistantApplicationService
from app.config import AgentSettings, Settings
from app.evaluation.cli import main as evaluation_main
from app.evaluation.loader import DEFAULT_EVALUATION_ROOT
from app.evaluation.v4.diagnostic import (
    DIAGNOSTIC_USER_MESSAGE,
    persist_diagnostic_pair_result,
    run_mirrored_diagnostic_pair,
)
from app.evaluation.v4.fingerprints import (
    business_clock_fingerprint,
    evaluation_subject_fingerprint,
    evaluation_transport_fingerprint,
    provider_config_fingerprint,
)
from app.evaluation.v4.loader import (
    assert_no_v4_holdout,
    load_v4_development_cases,
    v4_dataset_fingerprint,
)
from app.evaluation.v4.models import V4_EVALUATOR_VERSION
from app.evaluation.v4.preflight import PREFLIGHT_USER_MESSAGE
from app.evaluation.v4.transport import (
    DEFAULT_EVAL2_OUTPUT,
    DEFAULT_PREFLIGHT_OUTPUT,
    RUN1_ARCHIVE_PATH,
    RUN1_LIVE_PATH,
    STANDALONE_PREFLIGHT_20260829_PATH,
)
from app.workflow.action_creation import ActionCreationService

FROZEN_DEVELOPMENT_GOLD = "e2a0ce9952f52fd8bb814ae1853ce027f53c79091c06b3141890685c8febfb0f"
STAGE_6P_SUBJECT = "2a674da5848e4882150aca3052933ac21aeaff203e22f108a0d263c7390426b1"
STAGE_6P1_TRANSPORT = "97841cb7573de90279a8d2a7ea56b76140f95e9b3bfc2e844968d3a64614dc54"
STAGE_6P3_TRANSPORT = "caed4a3232fe0e4dd22975c7b97244aec30e1edfea48b71e8beb5f5d5e8b601d"
PHASE_1A_SUBJECT = "7410b097fb1b92581da557eed6b28e76bc5cf387d627eef7c4fa71d679d7f52f"
PHASE_1A_TRANSPORT = "1d78429a92e263df484497c3807279706489c9716ad3f952eac2cf5c9e8d1209"
CUTOVER_SUBJECT = "547acaead5a3919c20b31b5092e451e70320e4935e6a5484a3349476255232d8"
CUTOVER_TRANSPORT = "6b883134ac6e37c64046b5719744c6d3604252d355ef4dafa097c3722540132c"
STAGE_6P_PROVIDER_CONFIG = "f38d6d34897133bb4345deef9831d0dd914cc8e369a14dfe31ef4c605a726002"
STAGE_6P_BUSINESS_CLOCK = "fc995a58cfa205024fb9d91c9eed82ea4e5e0f5446714e67482b8134e81d0a01"


class RecordingModels:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        item = self.outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _settings() -> Settings:
    return Settings(gemini_api_key="test-only-key", _env_file=None)


def _agent_settings() -> AgentSettings:
    return AgentSettings(_env_file=None)


def _text_response(text: str = "READY") -> SimpleNamespace:
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=types.Content(role="model", parts=[types.Part(text=text)]),
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=9,
            candidates_token_count=1,
            total_token_count=10,
        ),
    )


def _function_call_response(name: str = "get_my_profile") -> SimpleNamespace:
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=types.Content(
                    role="model",
                    parts=[types.Part(function_call=types.FunctionCall(name=name, args={}))],
                )
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=11,
            candidates_token_count=2,
            total_token_count=13,
        ),
    )


def _rate_limited_error() -> errors.ClientError:
    payload = {
        "error": {
            "status": "RESOURCE_EXHAUSTED",
            "message": "secret provider message",
            "details": [
                {
                    "reason": "rate_limit_exceeded",
                    "retryDelay": "42s",
                    "metadata": {
                        "quota_metric": "generate_content_requests",
                        "quota_limit": "PerMinute",
                        "quota_limit_value": "10",
                        "quota_location": "global",
                    },
                }
            ],
        }
    }
    response = httpx.Response(
        429,
        headers={"authorization": "Bearer secret-token"},
        json=payload,
    )
    return errors.ClientError(429, payload, response)


def _run_pair(outcomes: list[object]) -> tuple[object, RecordingModels]:
    models = RecordingModels(outcomes)
    result = run_mirrored_diagnostic_pair(
        _settings(),
        _agent_settings(),
        sdk_client=SimpleNamespace(models=models),
    )
    return result, models


def test_pair_calls_provider_twice_agent_then_minimal() -> None:
    result, models = _run_pair([_text_response(), _text_response("ok")])
    assert len(models.calls) == 2
    assert models.calls[0]["contents"] != PREFLIGHT_USER_MESSAGE
    assert models.calls[1]["contents"] == PREFLIGHT_USER_MESSAGE
    assert getattr(models.calls[0]["config"], "tools", None)
    assert not getattr(models.calls[1]["config"], "tools", None)
    assert models.calls[0]["config"].max_output_tokens == 1024
    assert models.calls[1]["config"].max_output_tokens == 16
    assert result.order == ("agent_shaped", "minimal_control")
    assert result.agent_shaped.completed is True
    assert result.minimal_control.completed is True
    assert result.scored is False
    assert result.development_case is False
    assert result.v4_action_created is False


def test_pair_does_not_retry_on_blocked_then_completed() -> None:
    result, models = _run_pair([_rate_limited_error(), _text_response()])
    assert len(models.calls) == 2
    assert result.agent_shaped.completed is False
    assert result.minimal_control.completed is True
    failure = result.agent_shaped.provider_failure
    assert failure is not None
    assert failure.http_status_code == 429
    assert failure.symbolic_status == "RESOURCE_EXHAUSTED"
    assert failure.retry_delay_ms == 42000
    assert failure.provider_error_code == "rate_limit_exceeded"
    assert failure.quota_metric == "generate_content_requests"


def test_pair_completed_then_blocked() -> None:
    result, models = _run_pair([_text_response(), _rate_limited_error()])
    assert len(models.calls) == 2
    assert result.agent_shaped.completed is True
    assert result.minimal_control.completed is False
    assert result.minimal_control.provider_failure is not None
    assert result.minimal_control.provider_failure.http_status_code == 429


def test_pair_blocked_then_blocked() -> None:
    result, models = _run_pair([_rate_limited_error(), _rate_limited_error()])
    assert len(models.calls) == 2
    assert result.agent_shaped.completed is False
    assert result.minimal_control.completed is False


def test_function_call_is_recorded_and_not_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("product path must not run")

    monkeypatch.setattr(AgentService, "__init__", _forbidden)
    monkeypatch.setattr(ActionCreationService, "__init__", _forbidden)
    monkeypatch.setattr(AssistantApplicationService, "__init__", _forbidden)
    monkeypatch.setattr(ToolDispatcher, "__init__", _forbidden)
    result, models = _run_pair([_function_call_response(), _text_response()])
    assert len(models.calls) == 2
    assert result.agent_shaped.completed is True
    assert result.agent_shaped.response_kind == "function_call"
    assert result.agent_shaped.function_call_tool_name == "get_my_profile"
    assert result.agent_shaped.tool_executed is False
    assert result.agent_shaped.v4_action_created is False


def test_request_shape_fingerprints_are_deterministic_and_distinct() -> None:
    first, _ = _run_pair([_text_response(), _text_response()])
    second, _ = _run_pair([_text_response(), _text_response()])
    assert first.agent_shaped.request_shape_fingerprint == (
        second.agent_shaped.request_shape_fingerprint
    )
    assert first.minimal_control.request_shape_fingerprint == (
        second.minimal_control.request_shape_fingerprint
    )
    assert (
        first.agent_shaped.request_shape_fingerprint
        != first.minimal_control.request_shape_fingerprint
    )
    assert first.agent_shaped.request_size.tool_declaration_count == 5
    assert first.minimal_control.request_size.tool_declaration_count == 0
    assert DIAGNOSTIC_USER_MESSAGE == PREFLIGHT_USER_MESSAGE


def test_persist_safe_pair_and_rejects_secrets_and_reserved_paths(tmp_path: Path) -> None:
    result, _ = _run_pair([_rate_limited_error(), _text_response()])
    path = persist_diagnostic_pair_result(result, directory=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    dumped = path.read_text(encoding="utf-8")
    failure = payload["agent_shaped"]["provider_failure"]
    assert payload["kind"] == "provider_diagnostic_pair"
    assert payload["scored"] is False
    assert failure["http_status_code"] == 429
    assert failure["retry_delay_ms"] == 42000
    assert "message" not in failure
    assert "secret provider message" not in dumped
    assert "test-only-key" not in dumped
    assert "Bearer secret-token" not in dumped
    assert "authorization" not in dumped.lower()
    reserved = tmp_path / Path(DEFAULT_EVAL2_OUTPUT).name
    with pytest.raises(ValueError, match="reserved evaluation evidence"):
        persist_diagnostic_pair_result(result, path=reserved)


def test_historical_evidence_paths_remain_untouched(tmp_path: Path) -> None:
    watched = [
        Path(DEFAULT_PREFLIGHT_OUTPUT),
        Path(STANDALONE_PREFLIGHT_20260829_PATH),
        Path(DEFAULT_EVAL2_OUTPUT),
        Path(RUN1_LIVE_PATH),
        Path(RUN1_ARCHIVE_PATH),
    ]
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched if path.is_file()
    }
    result, _ = _run_pair([_text_response(), _text_response()])
    persist_diagnostic_pair_result(result, directory=tmp_path)
    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched if path.is_file()
    }
    assert before == after
    assert not any(tmp_path.joinpath(path.name).exists() for path in watched)


def test_diagnostic_cli_requires_authorization() -> None:
    assert (
        evaluation_main(
            [
                "--mode",
                "v4-product",
                "--split",
                "development",
                "--live",
                "--provider-diagnostic-pair",
            ]
        )
        == 2
    )


def test_diagnostic_cli_persists_pair_not_run2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _ = _run_pair([_text_response(), _text_response()])
    monkeypatch.setattr("app.evaluation.v4.cli.load_settings", _settings)
    monkeypatch.setattr("app.evaluation.v4.cli.load_agent_settings", _agent_settings)
    monkeypatch.setattr(
        "app.evaluation.v4.cli.run_mirrored_diagnostic_pair",
        lambda *_args, **_kwargs: result,
    )
    eval2_before = hashlib.sha256(Path(DEFAULT_EVAL2_OUTPUT).read_bytes()).hexdigest()
    output = tmp_path / "v4-provider-diagnostic-pair-test.json"
    code = evaluation_main(
        [
            "--mode",
            "v4-product",
            "--split",
            "development",
            "--live",
            "--provider-diagnostic-pair",
            "--authorize-diagnostic-pair",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["kind"] == "provider_diagnostic_pair"
    assert hashlib.sha256(Path(DEFAULT_EVAL2_OUTPUT).read_bytes()).hexdigest() == eval2_before


def test_stage_6p3_is_transport_only() -> None:
    assert V4_EVALUATOR_VERSION == "v4-product-eval-2"
    assert evaluation_subject_fingerprint() == CUTOVER_SUBJECT
    assert evaluation_subject_fingerprint() != PHASE_1A_SUBJECT
    assert evaluation_subject_fingerprint() != STAGE_6P_SUBJECT
    assert evaluation_transport_fingerprint() == CUTOVER_TRANSPORT
    assert evaluation_transport_fingerprint() != PHASE_1A_TRANSPORT
    assert evaluation_transport_fingerprint() != STAGE_6P1_TRANSPORT
    assert evaluation_transport_fingerprint() != STAGE_6P3_TRANSPORT
    assert provider_config_fingerprint() == STAGE_6P_PROVIDER_CONFIG
    assert business_clock_fingerprint() == STAGE_6P_BUSINESS_CLOCK
    assert v4_dataset_fingerprint(load_v4_development_cases()) == FROZEN_DEVELOPMENT_GOLD
    assert_no_v4_holdout()
    assert not (DEFAULT_EVALUATION_ROOT / "holdout" / "v4_product_cases.jsonl").exists()

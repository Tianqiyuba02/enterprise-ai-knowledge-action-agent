import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.agent.client import extract_provider_usage
from app.agent.loop_models import AgentProviderUsage, merge_provider_usage
from app.agent.provider_failures import (
    AgentProviderExceptionClass,
    AgentProviderFailureDetail,
    AgentProviderFailureKind,
    AgentProviderSymbolicStatus,
)
from app.config import AgentSettings, KnowledgeSettings, Settings
from app.evaluation.cli import main as evaluation_main
from app.evaluation.loader import DEFAULT_EVALUATION_ROOT
from app.evaluation.v4.clock import V4_DEVELOPMENT_BUSINESS_DATE
from app.evaluation.v4.fingerprints import (
    build_fingerprints,
    business_clock_fingerprint,
    evaluation_subject_fingerprint,
    evaluation_transport_fingerprint,
    provider_config_fingerprint,
    sha256_json,
)
from app.evaluation.v4.loader import (
    assert_no_v4_holdout,
    load_v4_development_cases,
    v4_dataset_fingerprint,
)
from app.evaluation.v4.metrics import build_summary, judge_case
from app.evaluation.v4.models import (
    V4_DEVELOPMENT_SET_VERSION,
    V4_EVALUATOR_VERSION,
    V4CaseAttempt,
    V4CaseExecutionState,
    V4EvaluationConfiguration,
    V4EvaluationFingerprints,
    V4ModelObservation,
    V4ProductCaseResult,
    V4ProductEvaluationReport,
    V4StopReason,
)
from app.evaluation.v4.preflight import (
    PREFLIGHT_KIND,
    FailedPreflightBlocksDevelopmentRun,
    ProviderPreflight,
    ProviderPreflightResult,
    persist_launch_preflight_result,
    require_successful_preflight,
)
from app.evaluation.v4.run1_archive import (
    load_closed_run1_evidence,
    refuse_eval2_write_over_run1,
)
from app.evaluation.v4.runner import V4ProductEvaluationRunner, V4ResumeCompatibilityError
from app.evaluation.v4.transport import (
    CIRCUIT_BREAKER_CONSECUTIVE_THRESHOLD,
    DEFAULT_EVAL2_OUTPUT,
    DEFAULT_PREFLIGHT_OUTPUT,
    RUN1_ARCHIVE_PATH,
    RUN1_EVIDENCE_COMMIT,
    RUN1_LIVE_PATH,
    RUN1_STATUS,
    STANDALONE_PREFLIGHT_20260829_PATH,
)

FROZEN_DEVELOPMENT_GOLD = "e2a0ce9952f52fd8bb814ae1853ce027f53c79091c06b3141890685c8febfb0f"
STAGE_6P_SUBJECT = "2a674da5848e4882150aca3052933ac21aeaff203e22f108a0d263c7390426b1"
STAGE_6P_TRANSPORT = "09deed71c2ea17658176c0256cabc3fc23d78c3df6c30b64575038843bd4e782"
PHASE_1A_SUBJECT = "7410b097fb1b92581da557eed6b28e76bc5cf387d627eef7c4fa71d679d7f52f"
PHASE_1A_TRANSPORT = "1d78429a92e263df484497c3807279706489c9716ad3f952eac2cf5c9e8d1209"
CUTOVER_SUBJECT = "d93c05b23bfdf7cc536eb588c0eddab9d614a1a52e6c6a75b81d94f537f265b8"
CUTOVER_TRANSPORT = "6b883134ac6e37c64046b5719744c6d3604252d355ef4dafa097c3722540132c"
STAGE_6P_PROVIDER_CONFIG = "f38d6d34897133bb4345deef9831d0dd914cc8e369a14dfe31ef4c605a726002"
STAGE_6P_BUSINESS_CLOCK = "fc995a58cfa205024fb9d91c9eed82ea4e5e0f5446714e67482b8134e81d0a01"


def _blocked(case_id: str) -> V4ProductCaseResult:
    failure = AgentProviderFailureDetail(
        kind=AgentProviderFailureKind.RATE_LIMITED,
        exception_class=AgentProviderExceptionClass.CLIENT_ERROR,
        http_status_code=429,
        symbolic_status=AgentProviderSymbolicStatus.RESOURCE_EXHAUSTED,
        provider_error_code=None,
        quota_metric=None,
    )
    return V4ProductCaseResult(
        case_id=case_id,
        state=V4CaseExecutionState.PROVIDER_BLOCKED,
        model=V4ModelObservation(
            provider_completed=False,
            provider_blocked=True,
            provider_failure_category=AgentProviderFailureKind.RATE_LIMITED.value,
            provider_failure=failure,
            prepared_action_present=False,
        ),
        safe_error_category=AgentProviderFailureKind.RATE_LIMITED.value,
        judgement=judge_case(
            next(item for item in load_v4_development_cases() if item.id == case_id),
            state=V4CaseExecutionState.PROVIDER_BLOCKED,
            model=None,
            product=None,
            business=None,
            safety=None,
        ),
    )


def _scripted_runner(monkeypatch: pytest.MonkeyPatch) -> V4ProductEvaluationRunner:
    cases = load_v4_development_cases()
    gold = v4_dataset_fingerprint(cases)
    fingerprints = build_fingerprints(gold, baseline_data="b" * 64)
    runner = V4ProductEvaluationRunner.__new__(V4ProductEvaluationRunner)
    runner._configuration = V4EvaluationConfiguration(
        agent_model="scripted",
        agent_timeout_seconds=60,
        agent_max_attempts=1,
        trusted_evaluation_date=V4_DEVELOPMENT_BUSINESS_DATE,
        corpus_documents=12,
        corpus_chunks=42,
        holiday_rows=14,
        calendar_version="AU-VIC-2026-v1",
        fingerprints=fingerprints,
    )
    runner._fingerprints = fingerprints
    runner._branch = "feature/v4-workflow-foundation"
    runner._commit = "b" * 40
    runner._engine = object()
    runner._sleep = lambda _: None
    monkeypatch.setattr("app.evaluation.v4.runner.cleanup_workflow_state", lambda _engine: None)
    return runner


def test_development_gold_fingerprint_is_unchanged() -> None:
    cases = load_v4_development_cases()
    assert v4_dataset_fingerprint(cases) == FROZEN_DEVELOPMENT_GOLD
    assert V4_DEVELOPMENT_SET_VERSION == "v4-product-dev-1"
    fingerprints = build_fingerprints(FROZEN_DEVELOPMENT_GOLD, baseline_data="b" * 64)
    assert fingerprints.development_set == FROZEN_DEVELOPMENT_GOLD
    assert fingerprints.development_gold == FROZEN_DEVELOPMENT_GOLD


def test_evaluator_v2_identity_is_distinct_from_run1() -> None:
    assert V4_EVALUATOR_VERSION == "v4-product-eval-2"
    fingerprints = build_fingerprints(FROZEN_DEVELOPMENT_GOLD, baseline_data="b" * 64)
    assert fingerprints.evaluation_subject != fingerprints.evaluation_transport
    assert fingerprints.evaluation_subject != fingerprints.provider_config


def test_closed_run1_evidence_remains_readable_and_is_not_eval2() -> None:
    evidence = load_closed_run1_evidence()
    assert evidence.identity.status == RUN1_STATUS
    assert evidence.identity.evidence_commit == RUN1_EVIDENCE_COMMIT
    assert evidence.identity.not_a_pass is True
    assert evidence.identity.not_a_holdout is True
    assert evidence.report.evaluator_version == "v4-product-eval-1"
    assert evidence.report.summary.provider_completed_count == 9
    assert evidence.report.summary.provider_blocked_count == 7
    assert Path(RUN1_LIVE_PATH).is_file()
    assert Path(RUN1_ARCHIVE_PATH).is_file()
    with pytest.raises(ValidationError):
        V4ProductEvaluationReport.model_validate_json(Path(RUN1_ARCHIVE_PATH).read_text())


def test_eval2_refuses_to_rewrite_run1() -> None:
    with pytest.raises(ValueError, match="closed V4 development Run 1"):
        refuse_eval2_write_over_run1(Path(RUN1_ARCHIVE_PATH))
    with pytest.raises(ValueError, match="rewrite Run 1"):
        refuse_eval2_write_over_run1(
            Path("evals/results/v4-product-development-eval-2.json"),
            Path(RUN1_ARCHIVE_PATH).read_text(encoding="utf-8"),
        )


def test_no_v4_holdout_exists() -> None:
    assert_no_v4_holdout()
    assert not (DEFAULT_EVALUATION_ROOT / "holdout" / "v4_product_cases.jsonl").exists()


def test_missing_quota_details_remain_null_and_raw_body_is_rejected() -> None:
    failure = AgentProviderFailureDetail(
        kind=AgentProviderFailureKind.RATE_LIMITED,
        exception_class=AgentProviderExceptionClass.CLIENT_ERROR,
        http_status_code=429,
        symbolic_status=AgentProviderSymbolicStatus.RESOURCE_EXHAUSTED,
    )
    dumped = failure.model_dump()
    assert dumped["provider_error_code"] is None
    assert dumped["quota_metric"] is None
    with pytest.raises(ValidationError):
        AgentProviderFailureDetail.model_validate({**dumped, "message": "secret body"})
    with pytest.raises(ValidationError):
        AgentProviderFailureDetail.model_validate({**dumped, "headers": {"authorization": "x"}})


def test_failed_attempt_structured_detail_survives_later_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = load_v4_development_cases()[:1]
    runner = _scripted_runner(monkeypatch)
    first = _blocked(cases[0].id)
    first = first.model_copy(
        update={
            "attempt_history": (
                V4CaseAttempt(
                    state=V4CaseExecutionState.PROVIDER_BLOCKED,
                    safe_error_category="rate_limited",
                    provider_failure_category="rate_limited",
                    normalized_category="rate_limited",
                    http_status_code=429,
                    symbolic_status="RESOURCE_EXHAUSTED",
                    provider_error_code=None,
                    quota_metric=None,
                    retry_delay_ms=None,
                ),
            )
        }
    )
    previous = V4ProductEvaluationReport(
        generated_at=datetime.now(UTC),
        branch="feature/v4-workflow-foundation",
        commit="b" * 40,
        dataset_fingerprint=v4_dataset_fingerprint(load_v4_development_cases()),
        configuration=runner._configuration,
        summary=build_summary((first,), cases),
        cases=(first,),
    )

    def _complete(case):
        return V4ProductCaseResult(
            case_id=case.id,
            state=V4CaseExecutionState.COMPLETED,
            model=V4ModelObservation(
                provider_completed=True,
                provider_blocked=False,
                prepared_action_present=False,
                usage=AgentProviderUsage(
                    prompt_token_count=11, output_token_count=3, total_token_count=14
                ),
            ),
        )

    runner._run_case = _complete  # type: ignore[method-assign]
    report = runner.run(
        cases=cases,
        dataset_fingerprint=v4_dataset_fingerprint(load_v4_development_cases()),
        previous_report=previous,
    )
    history = report.cases[0].attempt_history
    assert history[0].state is V4CaseExecutionState.PROVIDER_BLOCKED
    assert history[0].http_status_code == 429
    assert history[0].symbolic_status == "RESOURCE_EXHAUSTED"
    assert history[0].provider_error_code is None
    assert history[1].state is V4CaseExecutionState.COMPLETED
    assert report.cases[0].model is not None
    assert report.cases[0].model.usage is not None
    assert report.cases[0].model.usage.prompt_token_count == 11


def test_usage_metadata_is_copied_not_estimated() -> None:
    assert extract_provider_usage(SimpleNamespace()) is None
    usage = extract_provider_usage(
        SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=9,
                candidates_token_count=4,
                total_token_count=13,
                cached_content_token_count=2,
            )
        )
    )
    assert usage == AgentProviderUsage(
        prompt_token_count=9,
        output_token_count=4,
        total_token_count=13,
        cached_token_count=2,
    )
    merged = merge_provider_usage(usage, AgentProviderUsage(prompt_token_count=1))
    assert merged is not None
    assert merged.prompt_token_count == 10
    assert merged.output_token_count == 4


def test_circuit_breaker_stops_new_cases_and_excludes_them_from_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert CIRCUIT_BREAKER_CONSECUTIVE_THRESHOLD == 2
    cases = load_v4_development_cases()[:4]
    runner = _scripted_runner(monkeypatch)
    runner._run_case = lambda case: _blocked(case.id)  # type: ignore[method-assign]
    report = runner.run(
        cases=cases,
        dataset_fingerprint=v4_dataset_fingerprint(load_v4_development_cases()),
    )
    states = [item.state for item in report.cases]
    assert states == [
        V4CaseExecutionState.PROVIDER_BLOCKED,
        V4CaseExecutionState.PROVIDER_BLOCKED,
        V4CaseExecutionState.NOT_ATTEMPTED_DUE_TO_PROVIDER_CIRCUIT_BREAKER,
        V4CaseExecutionState.NOT_ATTEMPTED_DUE_TO_PROVIDER_CIRCUIT_BREAKER,
    ]
    assert report.run_stopped_early is True
    assert report.stop_reason is V4StopReason.PROVIDER_CIRCUIT_BREAKER
    assert report.summary.provider_blocked_count == 2
    assert report.summary.cases_not_attempted_due_to_circuit_breaker == 2
    assert report.summary.semantic_evaluable_count == 0
    assert report.summary.case_semantic_pass_rate is None
    assert report.summary.provider_block_rate == 1.0


def test_transport_fingerprint_mismatch_blocks_resume() -> None:
    cases = load_v4_development_cases()
    fingerprint = v4_dataset_fingerprint(cases)
    fingerprints = V4EvaluationFingerprints(
        development_set=fingerprint,
        development_gold=fingerprint,
        evaluation_subject="a" * 64,
        evaluation_transport="b" * 64,
        provider_config="c" * 64,
        baseline_data="d" * 64,
        business_clock="e" * 64,
    )
    configuration = V4EvaluationConfiguration(
        agent_model="scripted",
        agent_timeout_seconds=60,
        agent_max_attempts=1,
        trusted_evaluation_date=V4_DEVELOPMENT_BUSINESS_DATE,
        corpus_documents=12,
        corpus_chunks=42,
        holiday_rows=14,
        calendar_version="AU-VIC-2026-v1",
        fingerprints=fingerprints,
    )
    previous = V4ProductEvaluationReport(
        generated_at=datetime.now(UTC),
        branch="feature/v4-workflow-foundation",
        commit="b" * 40,
        dataset_fingerprint=fingerprint,
        configuration=configuration,
        summary=build_summary((), cases),
        cases=(),
    )
    runner = V4ProductEvaluationRunner.__new__(V4ProductEvaluationRunner)
    runner._configuration = configuration
    runner._fingerprints = fingerprints.model_copy(update={"evaluation_transport": "f" * 64})
    with pytest.raises(V4ResumeCompatibilityError, match="evaluation-transport"):
        runner._validate_resume(previous, cases=cases, dataset_fingerprint=fingerprint)


def test_preflight_is_non_scored_and_cannot_create_v4_action() -> None:
    result = ProviderPreflightResult(completed=True)
    assert result.kind == PREFLIGHT_KIND
    assert result.scored is False
    assert result.development_case is False
    assert result.holdout_case is False
    assert result.v4_action_created is False
    assert result.business_mutation is False
    require_successful_preflight(result)


def test_failed_preflight_prevents_automatic_development_run() -> None:
    failed = ProviderPreflightResult(completed=False)
    with pytest.raises(FailedPreflightBlocksDevelopmentRun, match="prevents automatic"):
        require_successful_preflight(failed)


def test_preflight_uses_fake_client_and_does_not_call_gemini() -> None:
    class _Models:
        def generate_content(self, **kwargs):
            assert kwargs["contents"] == "Reply with the single word READY."
            assert not getattr(kwargs["config"], "tools", None)
            return SimpleNamespace(
                candidates=[SimpleNamespace(content="ok")],
                usage_metadata=SimpleNamespace(
                    prompt_token_count=2,
                    candidates_token_count=1,
                    total_token_count=3,
                ),
            )

    fake = SimpleNamespace(models=_Models())
    result = ProviderPreflight(
        Settings(gemini_api_key="test-only-key", _env_file=None),
        AgentSettings(_env_file=None),
        sdk_client=fake,
    ).run()
    assert result.completed is True
    assert result.scored is False
    assert result.v4_action_created is False
    assert result.usage is not None
    assert result.usage.total_token_count == 3


def test_live_eval2_requires_authorized_preflight() -> None:
    assert evaluation_main(["--mode", "v4-product", "--split", "development", "--live"]) == 2


def test_subject_and_transport_fingerprints_are_stable_functions() -> None:
    subject = evaluation_subject_fingerprint()
    transport = evaluation_transport_fingerprint()
    assert subject == evaluation_subject_fingerprint()
    assert transport == evaluation_transport_fingerprint()
    assert subject != transport
    assert sha256_json({"docs_only": True}) != subject


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _blocked_preflight_result() -> ProviderPreflightResult:
    return ProviderPreflightResult(
        completed=False,
        provider_failure=AgentProviderFailureDetail(
            kind=AgentProviderFailureKind.RATE_LIMITED,
            exception_class=AgentProviderExceptionClass.CLIENT_ERROR,
            http_status_code=429,
            symbolic_status=AgentProviderSymbolicStatus.RESOURCE_EXHAUSTED,
            provider_error_code="rate_limit_exceeded",
            quota_metric="generate_content_requests",
            quota_limit="PerMinute",
            quota_limit_value="10",
            quota_location="global",
            retry_delay_ms=35000,
        ),
        generated_at=datetime(2026, 8, 29, 5, 15, 32, 123456, tzinfo=UTC),
    )


def _completed_preflight_result() -> ProviderPreflightResult:
    return ProviderPreflightResult(
        completed=True,
        usage=AgentProviderUsage(
            prompt_token_count=49,
            output_token_count=1,
            total_token_count=50,
            cached_token_count=None,
        ),
        generated_at=datetime(2026, 8, 29, 5, 16, 1, 654321, tzinfo=UTC),
    )


def _install_offline_launch_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    result: ProviderPreflightResult,
) -> dict[str, object]:
    calls = {"preflight": 0, "development_started": False}

    class _FakePreflight:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def run(self) -> ProviderPreflightResult:
            calls["preflight"] += 1
            return result

    def _fake_isolated(**_kwargs):
        calls["development_started"] = True
        raise RuntimeError("authorized launch reached isolated database")

    monkeypatch.setattr(
        "app.evaluation.v4.cli.load_settings",
        lambda: Settings(gemini_api_key="test-only-key", _env_file=None),
    )
    monkeypatch.setattr(
        "app.evaluation.v4.cli.load_agent_settings",
        lambda: AgentSettings(_env_file=None),
    )
    monkeypatch.setattr(
        "app.evaluation.v4.cli.load_knowledge_settings",
        lambda: KnowledgeSettings(_env_file=None),
    )
    monkeypatch.setattr("app.evaluation.v4.cli.ProviderPreflight", _FakePreflight)
    monkeypatch.setattr("app.evaluation.v4.cli.isolated_evaluation_database", _fake_isolated)
    monkeypatch.setattr(
        "app.evaluation.v4.cli.persist_launch_preflight_result",
        lambda item: persist_launch_preflight_result(item, directory=tmp_path),
    )
    return calls


def test_launch_preflight_failure_persists_and_does_not_start_run2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    historical_28 = Path(DEFAULT_PREFLIGHT_OUTPUT)
    historical_29 = Path(STANDALONE_PREFLIGHT_20260829_PATH)
    before_28 = _sha256_file(historical_28)
    before_29 = _sha256_file(historical_29)
    (tmp_path / historical_28.name).write_bytes(historical_28.read_bytes())
    (tmp_path / historical_29.name).write_bytes(historical_29.read_bytes())
    result = _blocked_preflight_result()
    calls = _install_offline_launch_fakes(monkeypatch, tmp_path, result)
    eval2 = tmp_path / Path(DEFAULT_EVAL2_OUTPUT).name

    code = evaluation_main(
        [
            "--mode",
            "v4-product",
            "--split",
            "development",
            "--live",
            "--authorize-preflight",
            "--output",
            str(eval2),
        ]
    )

    written = list(tmp_path.glob("v4-provider-preflight-launch-*.json"))
    assert code == 3
    assert calls["preflight"] == 1
    assert calls["development_started"] is False
    assert not eval2.exists()
    assert len(written) == 1
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    failure = payload["provider_failure"]
    assert payload["kind"] == "provider_preflight"
    assert payload["scored"] is False
    assert payload["completed"] is False
    assert failure["kind"] == "rate_limited"
    assert failure["http_status_code"] == 429
    assert failure["symbolic_status"] == "RESOURCE_EXHAUSTED"
    assert failure["provider_error_code"] == "rate_limit_exceeded"
    assert failure["quota_metric"] == "generate_content_requests"
    assert failure["quota_limit"] == "PerMinute"
    assert failure["quota_limit_value"] == "10"
    assert failure["quota_location"] == "global"
    assert failure["retry_delay_ms"] == 35000
    assert "message" not in failure
    assert "headers" not in failure
    assert "body" not in failure
    dumped = written[0].read_text(encoding="utf-8")
    assert "test-only-key" not in dumped
    assert "authorization" not in dumped.lower()
    assert _sha256_file(historical_28) == before_28
    assert _sha256_file(historical_29) == before_29
    assert _sha256_file(tmp_path / historical_28.name) == before_28
    assert _sha256_file(tmp_path / historical_29.name) == before_29


def test_launch_preflight_success_persists_once_then_may_start_development(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    historical_28 = Path(DEFAULT_PREFLIGHT_OUTPUT)
    historical_29 = Path(STANDALONE_PREFLIGHT_20260829_PATH)
    before_28 = _sha256_file(historical_28)
    before_29 = _sha256_file(historical_29)
    result = _completed_preflight_result()
    calls = _install_offline_launch_fakes(monkeypatch, tmp_path, result)
    eval2 = tmp_path / Path(DEFAULT_EVAL2_OUTPUT).name

    code = evaluation_main(
        [
            "--mode",
            "v4-product",
            "--split",
            "development",
            "--live",
            "--authorize-preflight",
            "--output",
            str(eval2),
        ]
    )

    written = list(tmp_path.glob("v4-provider-preflight-launch-*.json"))
    assert calls["preflight"] == 1
    assert calls["development_started"] is True
    assert code == 1
    assert not eval2.exists()
    assert len(written) == 1
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["completed"] is True
    assert payload["scored"] is False
    assert payload["usage"]["prompt_token_count"] == 49
    assert payload["usage"]["total_token_count"] == 50
    assert _sha256_file(historical_28) == before_28
    assert _sha256_file(historical_29) == before_29


def test_persist_launch_preflight_leaves_standalone_artifacts_untouched(
    tmp_path: Path,
) -> None:
    standalone = tmp_path / Path(DEFAULT_PREFLIGHT_OUTPUT).name
    dated = tmp_path / Path(STANDALONE_PREFLIGHT_20260829_PATH).name
    standalone.write_text("keep-2026-08-28", encoding="utf-8")
    dated.write_text("keep-2026-08-29", encoding="utf-8")
    path = persist_launch_preflight_result(_blocked_preflight_result(), directory=tmp_path)
    assert path.name.startswith("v4-provider-preflight-launch-")
    assert path != standalone
    assert path != dated
    assert standalone.read_text(encoding="utf-8") == "keep-2026-08-28"
    assert dated.read_text(encoding="utf-8") == "keep-2026-08-29"


def test_stage_6p1_is_transport_only_fingerprint_change() -> None:
    assert V4_EVALUATOR_VERSION == "v4-product-eval-2"
    assert evaluation_subject_fingerprint() == CUTOVER_SUBJECT
    assert evaluation_subject_fingerprint() != PHASE_1A_SUBJECT
    assert evaluation_subject_fingerprint() != STAGE_6P_SUBJECT
    assert evaluation_transport_fingerprint() == CUTOVER_TRANSPORT
    assert evaluation_transport_fingerprint() != PHASE_1A_TRANSPORT
    assert evaluation_transport_fingerprint() != STAGE_6P_TRANSPORT
    assert provider_config_fingerprint() == STAGE_6P_PROVIDER_CONFIG
    assert business_clock_fingerprint() == STAGE_6P_BUSINESS_CLOCK
    assert v4_dataset_fingerprint(load_v4_development_cases()) == FROZEN_DEVELOPMENT_GOLD
    assert_no_v4_holdout()

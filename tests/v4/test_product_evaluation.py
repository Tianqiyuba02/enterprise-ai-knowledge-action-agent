from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.agent.leave_models import LeavePreparationStatus, LeaveRequestDraft
from app.agent.loop_models import AgentRunResult, AgentRunStatus
from app.agent.provider_failures import AgentProviderFailureKind
from app.agent.relative_weekday import resolve_next_weekday
from app.agent.service import AgentService
from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.evaluation.agent_loader import load_agent_evaluation_cases
from app.evaluation.agent_models import AgentEvaluationReport
from app.evaluation.cli import main as evaluation_main
from app.evaluation.loader import DEFAULT_EVALUATION_ROOT
from app.evaluation.models import EvaluationSplit
from app.evaluation.v4.clock import (
    V4_DEVELOPMENT_BUSINESS_DATE,
    V4DevelopmentBusinessClock,
)
from app.evaluation.v4.fingerprints import (
    build_fingerprints,
    canonical_baseline_payload,
    demo_fixture_payload,
    digest_embedding,
    hash_baseline_payload,
    provider_config_payload,
    sha256_json,
)
from app.evaluation.v4.loader import (
    EXPECTED_DEVELOPMENT_CASE_COUNT,
    assert_no_v4_holdout,
    load_v4_development_cases,
    v4_dataset_fingerprint,
)
from app.evaluation.v4.metrics import (
    CANONICAL_SAFETY_METRICS,
    assert_report_has_no_secrets,
    build_summary,
    judge_case,
    score_safety,
)
from app.evaluation.v4.models import (
    V4_DEVELOPMENT_SET_VERSION,
    V4_EVALUATOR_VERSION,
    V4BusinessObservation,
    V4CaseCategory,
    V4CaseExecutionState,
    V4EvaluationConfiguration,
    V4EvaluationFingerprints,
    V4EvaluationSummary,
    V4ModelObservation,
    V4PrepareExpectation,
    V4ProductCaseResult,
    V4ProductEvaluationReport,
    V4ProductObservation,
)
from app.evaluation.v4.runner import V4ProductEvaluationRunner, V4ResumeCompatibilityError
from app.knowledge.clock import MelbourneClock
from app.repositories.demo import DemoRepository


def _draft(start: date = date(2026, 9, 14), end: date | None = None) -> LeaveRequestDraft:
    return LeaveRequestDraft(
        leave_type="annual",
        start_date=start,
        end_date=end or start,
        scheduled_work_days=1,
        requested_hours=Decimal("7.60"),
        current_balance_hours=Decimal("76.00"),
        projected_balance_hours=Decimal("68.40"),
        preparation_status=LeavePreparationStatus.READY,
        reason="appointment",
        public_holiday_check_required=True,
        non_executing=True,
    )


def _completed(*, draft: LeaveRequestDraft | None, answer: str = "Prepared.") -> AgentRunResult:
    return AgentRunResult(
        status=AgentRunStatus.COMPLETED,
        answer=answer,
        citations=(),
        prepared_leave_request=draft,
        tool_calls_attempted=1 if draft is not None else 0,
        model_rounds=1,
    )


def test_loader_reads_exactly_sixteen_development_cases() -> None:
    cases = load_v4_development_cases()
    assert len(cases) == EXPECTED_DEVELOPMENT_CASE_COUNT
    assert all(case.dataset_kind == "DEVELOPMENT" for case in cases)
    assert {case.category for case in cases} == set(V4CaseCategory)
    assert_no_v4_holdout()
    assert not (DEFAULT_EVALUATION_ROOT / "holdout" / "v4_product_cases.jsonl").exists()


def test_b_cases_require_prepare_to_exercise_v4_authority() -> None:
    cases = {case.id: case for case in load_v4_development_cases()}
    for case_id in (
        "dev_v4_b1_afl_holiday",
        "dev_v4_b2_weekend",
        "dev_v4_b3_insufficient_balance",
        "dev_v4_b4_calendar_uncovered",
    ):
        assert cases[case_id].expected_model_behavior.prepare_expectation is (
            V4PrepareExpectation.REQUIRED
        )


def test_provider_blocked_is_excluded_from_semantic_scoring() -> None:
    case = load_v4_development_cases()[0]
    result = V4ProductCaseResult(
        case_id=case.id,
        state=V4CaseExecutionState.PROVIDER_BLOCKED,
        model=V4ModelObservation(
            provider_completed=False,
            provider_blocked=True,
            provider_failure_category=AgentProviderFailureKind.RATE_LIMITED.value,
            prepared_action_present=False,
        ),
        judgement=judge_case(
            case,
            state=V4CaseExecutionState.PROVIDER_BLOCKED,
            model=None,
            product=None,
            business=None,
            safety=None,
        ),
    )
    summary = build_summary((result,), (case,))
    assert summary.provider_blocked_count == 1
    assert summary.semantic_evaluable_count == 0
    assert summary.case_semantic_pass_rate is None
    assert summary.full_e2e_success_rate is None
    assert result.judgement is not None
    assert result.judgement.semantic_pass is None


def test_null_metrics_when_no_applicable_cases() -> None:
    summary = V4EvaluationSummary(
        cases_total=0,
        provider_completed_count=0,
        provider_blocked_count=0,
        provider_block_rate=None,
        cases_error=0,
        cases_carried_forward=0,
        semantic_evaluable_count=0,
        case_semantic_pass_rate=None,
        prepare_expectation_accuracy=None,
        read_no_action_accuracy=None,
        action_outcome_accuracy=None,
        authoritative_draft_accuracy=None,
        action_reuse_accuracy=None,
        confirmation_bypass_violation_rate=None,
        action_authority_violation_rate=None,
        unauthorized_execution_violation_rate=None,
        duplicate_live_action_violation_rate=None,
        duplicate_business_mutation_violation_rate=None,
        non_executable_action_creation_violation_rate=None,
        wrong_owner_authority_violation_rate=None,
        prompt_injection_or_action_authority_violation_rate=None,
        full_e2e_success_rate=None,
        safety_gate_failed=False,
    )
    assert summary.full_e2e_success_rate is None
    assert summary.read_no_action_accuracy is None


def test_non_executable_action_creation_is_a_safety_violation() -> None:
    case = next(item for item in load_v4_development_cases() if item.id == "dev_v4_b1_afl_holiday")
    model = V4ModelObservation(
        provider_completed=True,
        provider_blocked=False,
        prepared_action_present=True,
        prepared_start_date=date(2026, 9, 25),
        prepared_end_date=date(2026, 9, 25),
    )
    product = V4ProductObservation(action_status="created", action_id="opaque-id")
    business = V4BusinessObservation()
    safety = score_safety(case, model, product, business)
    judgement = judge_case(
        case,
        state=V4CaseExecutionState.COMPLETED,
        model=model,
        product=product,
        business=business,
        safety=safety,
    )
    assert safety.non_executable_action_creation_violation is True
    assert judgement.product_safety_pass is False
    assert judgement.semantic_pass is False


def test_prepare_miss_is_not_a_safety_violation() -> None:
    case = next(item for item in load_v4_development_cases() if item.id == "dev_v4_b1_afl_holiday")
    model = V4ModelObservation(
        provider_completed=True,
        provider_blocked=False,
        prepared_action_present=False,
    )
    product = V4ProductObservation()
    business = V4BusinessObservation()
    safety = score_safety(case, model, product, business)
    judgement = judge_case(
        case,
        state=V4CaseExecutionState.COMPLETED,
        model=model,
        product=product,
        business=business,
        safety=safety,
    )
    assert judgement.model_behavior_pass is False
    assert judgement.product_safety_pass is True
    assert judgement.product_behavior_pass is None
    assert judgement.semantic_pass is False


def _scripted_configuration(
    fingerprints: V4EvaluationFingerprints,
) -> V4EvaluationConfiguration:
    return V4EvaluationConfiguration(
        agent_model="gemini-3.6-flash",
        agent_timeout_seconds=60,
        agent_max_attempts=1,
        trusted_evaluation_date=V4_DEVELOPMENT_BUSINESS_DATE,
        corpus_documents=12,
        corpus_chunks=42,
        holiday_rows=14,
        calendar_version="AU-VIC-2026-v1",
        fingerprints=fingerprints,
    )


def test_compatible_resume_returns_previous_completed_results() -> None:
    cases = load_v4_development_cases()
    fingerprint = v4_dataset_fingerprint(cases)
    fingerprints = build_fingerprints(fingerprint, baseline_data="b" * 64)
    configuration = _scripted_configuration(fingerprints)
    previous_result = V4ProductCaseResult(
        case_id=cases[0].id,
        state=V4CaseExecutionState.COMPLETED,
        model=V4ModelObservation(
            provider_completed=True,
            provider_blocked=False,
            prepared_action_present=True,
            prepared_start_date=date(2026, 9, 14),
            prepared_end_date=date(2026, 9, 14),
        ),
        product=V4ProductObservation(action_status="created"),
        business=V4BusinessObservation(),
    )
    previous = V4ProductEvaluationReport(
        generated_at=datetime.now(UTC),
        branch="feature/v4-workflow-foundation",
        commit="b" * 40,
        dataset_fingerprint=fingerprint,
        configuration=configuration,
        summary=build_summary((previous_result,), (cases[0],)),
        cases=(previous_result,),
    )
    runner = V4ProductEvaluationRunner.__new__(V4ProductEvaluationRunner)
    runner._configuration = configuration
    runner._fingerprints = fingerprints
    resumed = runner._validate_resume(previous, cases=cases, dataset_fingerprint=fingerprint)
    assert resumed[cases[0].id].state is V4CaseExecutionState.COMPLETED


def test_fingerprint_mismatch_blocks_resume() -> None:
    cases = load_v4_development_cases()
    fingerprint = v4_dataset_fingerprint(cases)
    fingerprints = V4EvaluationFingerprints(
        development_set=fingerprint,
        evaluation_subject="a" * 64,
        provider_config="b" * 64,
        baseline_data="c" * 64,
        business_clock="d" * 64,
    )
    configuration = _scripted_configuration(fingerprints)
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
    runner._fingerprints = fingerprints.model_copy(update={"evaluation_subject": "e" * 64})
    with pytest.raises(V4ResumeCompatibilityError, match="evaluation-subject"):
        runner._validate_resume(previous, cases=cases, dataset_fingerprint=fingerprint)


def test_confirmation_token_is_rejected_in_artifacts() -> None:
    with pytest.raises(ValueError, match="forbidden secret"):
        assert_report_has_no_secrets('{"challenge":"confirmation_token"}')


def test_v3_holdout_and_evaluator_remain_readable() -> None:
    holdout = load_agent_evaluation_cases(EvaluationSplit.HOLDOUT)
    assert holdout
    raw = Path("evals/results/v3-stage5b-holdout-agent.json").read_text(encoding="utf-8")
    report = AgentEvaluationReport.model_validate_json(raw)
    assert report.split is EvaluationSplit.HOLDOUT
    assert report.configuration.evaluation_schema_version != V4_EVALUATOR_VERSION


def test_v4_cli_refuses_holdout_and_requires_live() -> None:
    holdout = evaluation_main(["--mode", "v4-product", "--split", "holdout", "--live"])
    assert holdout == 2
    offline = evaluation_main(["--mode", "v4-product", "--split", "development"])
    assert offline == 2


def test_evaluator_identity_is_explicitly_versioned() -> None:
    assert V4_EVALUATOR_VERSION == "v4-product-eval-1"
    assert V4_DEVELOPMENT_SET_VERSION == "v4-product-dev-1"
    fingerprints = build_fingerprints(sha256_json({"cases": "dev"}), baseline_data="b" * 64)
    assert fingerprints.development_set
    assert fingerprints.evaluation_subject != fingerprints.provider_config
    assert fingerprints.baseline_data != fingerprints.business_clock


def test_scripted_agent_helpers_remain_non_executing() -> None:
    draft = _draft()
    result = _completed(draft=draft)
    assert result.prepared_leave_request is not None
    assert result.prepared_leave_request.non_executing is True


def test_development_business_clock_ignores_host_date(monkeypatch: pytest.MonkeyPatch) -> None:
    class _HostDatetime:
        @staticmethod
        def now(tz=None):
            return datetime(2028, 1, 15, 9, 0, tzinfo=tz)

    monkeypatch.setattr("app.knowledge.clock.datetime", _HostDatetime)
    assert MelbourneClock().today() == date(2028, 1, 15)
    clock = V4DevelopmentBusinessClock()
    assert clock.today() == date(2026, 8, 28)
    assert resolve_next_weekday(clock.today(), "monday") == date(2026, 8, 31)


def test_injected_clock_reaches_agent_when_host_date_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _HostDatetime:
        @staticmethod
        def now(tz=None):
            return datetime(2028, 3, 1, 12, 0, tzinfo=tz)

    class _RecordingProvider:
        def __init__(self) -> None:
            self.trusted_today: date | None = None

        def start(self, message: str, trusted_today: date):
            self.trusted_today = trusted_today
            raise RuntimeError("stop after recording trusted date")

    monkeypatch.setattr("app.knowledge.clock.datetime", _HostDatetime)
    provider = _RecordingProvider()
    AgentService(
        provider=provider,
        dispatcher=object(),  # type: ignore[arg-type]
        clock=V4DevelopmentBusinessClock(),
    ).run("How much annual leave do I have?", DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"])
    assert provider.trusted_today == V4_DEVELOPMENT_BUSINESS_DATE
    assert MelbourneClock().today() == date(2028, 3, 1)


def test_embedding_digest_is_stable_for_vector_text_and_floats() -> None:
    assert digest_embedding("[1.5, 2.0]") == digest_embedding((1.5, 2.0))
    assert digest_embedding("[1.5, 2.0]") != digest_embedding("[1.5, 2.1]")


def test_same_count_corpus_content_change_changes_baseline_fingerprint() -> None:
    documents = [
        {
            "id": f"doc-{index}",
            "doc_code": f"POL-{index:03d}",
            "version": "1.0",
            "title": f"Policy {index}",
            "status": "approved",
            "effective_date": "2026-01-01",
            "expiry_date": None,
            "jurisdiction": "AU-VIC",
            "audience_groups": ["all_employees"],
            "source_uri": f"doc://{index}",
            "content_checksum": "a" * 64,
            "superseded_by_id": None,
            "embedding_model_id": "text-embedding-004",
            "embedding_dimension": 768,
        }
        for index in range(12)
    ]
    chunks = [
        {
            "id": f"chunk-{index}",
            "document_id": f"doc-{index % 12}",
            "chunk_index": index,
            "section_label": "body",
            "anchor": f"a{index}",
            "page": 1,
            "content": f"chunk {index}",
            "token_count": 8,
            "embedding_digest": digest_embedding((float(index), 0.25)),
        }
        for index in range(42)
    ]
    holidays = [{"jurisdiction": "AU-VIC", "holiday_date": "2026-09-25", "holiday_name": "AFL"}]
    demo = demo_fixture_payload()
    original = hash_baseline_payload(
        canonical_baseline_payload(
            documents=documents,
            chunks=chunks,
            holidays=holidays,
            demo=demo,
        )
    )
    changed_docs = [dict(item) for item in documents]
    changed_docs[0]["title"] = "Policy 0 changed"
    changed = hash_baseline_payload(
        canonical_baseline_payload(
            documents=changed_docs,
            chunks=chunks,
            holidays=holidays,
            demo=demo,
        )
    )
    assert original != changed


def test_demo_repository_fixture_change_changes_baseline_fingerprint() -> None:
    original = demo_fixture_payload(DemoRepository())
    mutated = {
        **original,
        "employees": [
            {**item, "hours_per_day": 9.0} if item["employee_id"] == "EMP-1001" else item
            for item in original["employees"]
        ],
    }
    holidays = [{"jurisdiction": "AU-VIC", "holiday_date": "2026-01-01", "holiday_name": "NY"}]
    left = hash_baseline_payload(
        canonical_baseline_payload(documents=[], chunks=[], holidays=holidays, demo=original)
    )
    right = hash_baseline_payload(
        canonical_baseline_payload(documents=[], chunks=[], holidays=holidays, demo=mutated)
    )
    assert left != right


def test_baseline_and_provider_and_clock_resume_mismatches_are_refused() -> None:
    cases = load_v4_development_cases()
    fingerprint = v4_dataset_fingerprint(cases)
    fingerprints = V4EvaluationFingerprints(
        development_set=fingerprint,
        evaluation_subject="a" * 64,
        provider_config="b" * 64,
        baseline_data="c" * 64,
        business_clock="d" * 64,
    )
    configuration = _scripted_configuration(fingerprints)
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
    runner._fingerprints = fingerprints.model_copy(update={"baseline_data": "e" * 64})
    with pytest.raises(V4ResumeCompatibilityError, match="baseline-data"):
        runner._validate_resume(previous, cases=cases, dataset_fingerprint=fingerprint)
    runner._fingerprints = fingerprints.model_copy(update={"provider_config": "e" * 64})
    with pytest.raises(V4ResumeCompatibilityError, match="provider-config"):
        runner._validate_resume(previous, cases=cases, dataset_fingerprint=fingerprint)
    runner._fingerprints = fingerprints.model_copy(update={"business_clock": "e" * 64})
    with pytest.raises(V4ResumeCompatibilityError, match="business-clock"):
        runner._validate_resume(previous, cases=cases, dataset_fingerprint=fingerprint)


def test_provider_config_payload_excludes_secrets() -> None:
    payload = provider_config_payload()
    encoded = sha256_json(payload)
    assert "GEMINI_API_KEY" not in encoded
    assert "api_key" not in str(payload).lower()
    assert payload["agent_model"] == "gemini-3.6-flash"
    assert payload["thinking_level"] == "MINIMAL"
    changed = {**payload, "agent_model": "other-model"}
    assert sha256_json(payload) != sha256_json(changed)


def test_canonical_safety_inventory_has_exactly_seven_metrics() -> None:
    assert len(CANONICAL_SAFETY_METRICS) == 7
    assert CANONICAL_SAFETY_METRICS[-1] == "wrong_owner_authority_violation_rate"
    summary_fields = [
        name
        for name in V4EvaluationSummary.model_fields
        if name.endswith("_violation_rate") and not name.startswith("prompt_injection")
    ]
    assert tuple(summary_fields) == CANONICAL_SAFETY_METRICS


def test_wrong_owner_metric_is_null_when_no_applicable_cases() -> None:
    case = load_v4_development_cases()[0]
    result = V4ProductCaseResult(
        case_id=case.id,
        state=V4CaseExecutionState.COMPLETED,
        model=V4ModelObservation(
            provider_completed=True,
            provider_blocked=False,
            prepared_action_present=True,
        ),
        product=V4ProductObservation(action_status="created"),
        business=V4BusinessObservation(),
        safety=score_safety(
            case,
            V4ModelObservation(
                provider_completed=True,
                provider_blocked=False,
                prepared_action_present=True,
            ),
            V4ProductObservation(action_status="created"),
            V4BusinessObservation(),
        ),
        judgement=judge_case(
            case,
            state=V4CaseExecutionState.COMPLETED,
            model=V4ModelObservation(
                provider_completed=True,
                provider_blocked=False,
                prepared_action_present=True,
            ),
            product=V4ProductObservation(action_status="created"),
            business=V4BusinessObservation(),
            safety=score_safety(
                case,
                V4ModelObservation(
                    provider_completed=True,
                    provider_blocked=False,
                    prepared_action_present=True,
                ),
                V4ProductObservation(action_status="created"),
                V4BusinessObservation(),
            ),
        ),
    )
    summary = build_summary((result,), (case,))
    assert result.safety is not None
    assert result.safety.wrong_owner_authority_violation is None
    assert summary.wrong_owner_authority_violation_rate is None
    assert summary.confirmation_bypass_violation_rate is not None


def test_unavailable_tool_trace_is_not_an_empty_tool_list() -> None:
    observation = V4ModelObservation(
        provider_completed=True,
        provider_blocked=False,
        prepared_action_present=False,
    )
    assert observation.tool_trace_available is False
    assert observation.tool_names is None
    assert observation.tool_names != ()


def test_development_gold_identities_are_unchanged() -> None:
    cases = load_v4_development_cases()
    assert [case.id for case in cases] == [
        "dev_v4_a1_executable_single",
        "dev_v4_a2_executable_multiday",
        "dev_v4_b1_afl_holiday",
        "dev_v4_b2_weekend",
        "dev_v4_b3_insufficient_balance",
        "dev_v4_b4_calendar_uncovered",
        "dev_v4_c1_balance_read",
        "dev_v4_c2_policy_knowledge",
        "dev_v4_c3_profile_read",
        "dev_v4_d1_chat_confirmation",
        "dev_v4_d2_action_id_injection",
        "dev_v4_e1_repeat_prepare_reuse",
        "dev_v4_e2_reason_mismatch_reuse",
        "dev_v4_e3_unknown_blocks_replace",
        "dev_v4_e4_succeeded_no_duplicate",
        "dev_v4_f1_full_e2e",
    ]
    by_id = {case.id: case for case in cases}
    for case_id in (
        "dev_v4_b1_afl_holiday",
        "dev_v4_b2_weekend",
        "dev_v4_b3_insufficient_balance",
        "dev_v4_b4_calendar_uncovered",
    ):
        assert by_id[case_id].expected_model_behavior.prepare_expectation.value == "required"
    f1 = by_id["dev_v4_f1_full_e2e"]
    assert f1.out_of_band_confirmation is True
    assert f1.worker_execution is True
    assert f1.expected_product_behavior.action_status.value == "created"
    assert f1.expected_business_behavior.final_state == "SUCCEEDED"

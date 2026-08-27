from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.client import AgentProviderRateLimitError, AgentProviderTimeoutError
from app.agent.contracts import MAX_TOOL_CALLS_PER_TURN
from app.agent.leave_models import LeavePreparationStatus
from app.agent.loop_models import (
    AgentModelTurn,
    AgentRequestedToolCall,
    AgentRunResult,
    AgentRunStatus,
)
from app.agent.models import (
    ProfileToolData,
    ToolResult,
    ToolResultStatus,
)
from app.agent.provider_failures import (
    AgentProviderExceptionClass,
    AgentProviderFailureDetail,
    AgentProviderFailureKind,
    AgentProviderSymbolicStatus,
)
from app.agent.service import MAX_MODEL_ROUNDS_PER_TURN
from app.api.assistant_models import (
    AssistantPublicStatus,
    AssistantQueryResponse,
    PreparedLeaveRequestAction,
)
from app.api.knowledge_models import KnowledgeCitation
from app.config import APPROVED_AGENT_MODEL, AgentSettings
from app.evaluation.agent_cli import AGENT_EVALUATION_DATE, AGENT_EVALUATION_SCHEMA_VERSION
from app.evaluation.agent_loader import (
    agent_dataset_fingerprint,
    load_agent_evaluation_cases,
    validate_all_agent_splits,
)
from app.evaluation.agent_metrics import (
    build_agent_summary,
    evaluate_agent_case,
    evaluate_agent_invariants,
)
from app.evaluation.agent_models import (
    AgentCaseAttempt,
    AgentCaseCategory,
    AgentCaseExecutionState,
    AgentCaseMetrics,
    AgentEvaluationCase,
    AgentEvaluationCaseResult,
    AgentEvaluationConfiguration,
    AgentEvaluationReport,
    AgentInvariantMetrics,
    CitationObservation,
    PreparedActionObservation,
    ToolTraceObservation,
)
from app.evaluation.agent_runner import (
    AgentEvaluationRunner,
    AgentResumeCompatibilityError,
)
from app.evaluation.agent_trace import RecordingToolDispatcher
from app.evaluation.cli import main as evaluation_main
from app.evaluation.models import EvaluationSplit, ResultOrigin
from app.identity import AuthenticatedEmployeeContext
from app.repositories.demo import DemoRepository

DEVELOPMENT_FINGERPRINT = "c8c8822bb4a6b7c6c3058d2c68328ec2c94a5e6b956459688c797e5f11c6bf7a"
HOLDOUT_FINGERPRINT = "b68a78f687b81040e265aef6d934d4879b3180405159cb4d5ed10ad923ba4d58"
CONTEXT = AuthenticatedEmployeeContext(employee_id="EMP-1001")


class FixedClock:
    def today(self) -> date:
        return AGENT_EVALUATION_DATE


def _case(**overrides: object) -> AgentEvaluationCase:
    values: dict[str, object] = {
        "id": "dev_agent_test",
        "split": "development",
        "category": "simple_read",
        "employee_fixture": "alex",
        "user_message": "What is my profile?",
        "expected_public_status": "completed",
        "required_tools": ["get_my_profile"],
        "allowed_tools": ["get_my_profile"],
        "forbidden_tools": ["get_my_ticket"],
        "rationale": "Mechanical evaluator test.",
    }
    values.update(overrides)
    return AgentEvaluationCase.model_validate(values)


def _configuration(**overrides: object) -> AgentEvaluationConfiguration:
    values: dict[str, object] = {
        "agent_model": "gemini-3.6-flash",
        "agent_timeout_seconds": 60,
        "agent_max_attempts": 1,
        "trusted_evaluation_date": AGENT_EVALUATION_DATE,
        "max_tool_calls": MAX_TOOL_CALLS_PER_TURN,
        "max_model_rounds": MAX_MODEL_ROUNDS_PER_TURN,
        "tool_registry_fingerprint": "a" * 64,
        "demo_fixture_version": "v1-demo-records-2026-08-24",
        "grounded_generation_model": "gemini-3.6-flash",
        "embedding_model": "gemini-embedding-2",
        "embedding_dimension": 768,
        "retrieval_top_k": 6,
        "corpus_identity": "b" * 64,
        "corpus_documents": 12,
        "corpus_chunks": 42,
    }
    values.update(overrides)
    return AgentEvaluationConfiguration.model_validate(values)


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


def _run_result() -> AgentRunResult:
    return AgentRunResult(
        status=AgentRunStatus.COMPLETED,
        answer="Completed safely.",
        citations=(),
        tool_calls_attempted=1,
        model_rounds=2,
    )


def _invariants() -> AgentInvariantMetrics:
    return AgentInvariantMetrics(
        identity_violations=0,
        accepted_employee_id_arguments=0,
        business_mutations=0,
        citation_count_bound_violation=False,
        tool_call_bound_violation=False,
        model_round_bound_violation=False,
    )


def _basic_metrics() -> AgentCaseMetrics:
    return AgentCaseMetrics(
        semantic_status_correct=True,
        required_tool_recall=1.0,
        tool_selection_success=True,
        forbidden_tool_calls=0,
        unnecessary_tool_calls=0,
        expected_tool_outcomes_valid=None,
        required_citation_recall=None,
        forbidden_citation_hits=0,
        citation_metadata_valid=None,
        citation_count_within_bound=True,
        prepared_action_presence_valid=True,
        prepared_action_structured_accuracy=None,
        non_executing_valid=None,
        prepared_action_forbidden_identifiers=0,
        false_execution_claim=None,
        prompt_injection_undesired_calls=None,
    )


def test_agent_datasets_are_strict_disjoint_frozen_and_expected_size() -> None:
    validate_all_agent_splits()
    development = load_agent_evaluation_cases(EvaluationSplit.DEVELOPMENT)
    holdout = load_agent_evaluation_cases(EvaluationSplit.HOLDOUT)

    assert len(development) == 16
    assert len(holdout) == 8
    assert {case.id for case in development}.isdisjoint({case.id for case in holdout})
    assert agent_dataset_fingerprint(development) == DEVELOPMENT_FINGERPRINT
    assert agent_dataset_fingerprint(holdout) == HOLDOUT_FINGERPRINT
    assert {case.category for case in development} == set(AgentCaseCategory)


def test_historical_reports_resolve_to_30_seconds_and_two_attempts() -> None:
    payload = _configuration().model_dump(mode="json")
    payload.pop("agent_timeout_seconds")
    payload.pop("agent_max_attempts")

    configuration = AgentEvaluationConfiguration.model_validate(payload)

    assert configuration.agent_timeout_seconds == 30
    assert configuration.agent_max_attempts == 2
    assert configuration != _configuration()


@pytest.mark.parametrize(
    "overrides",
    [
        {"allowed_tools": []},
        {
            "allowed_tools": ["get_my_profile"],
            "forbidden_tools": ["get_my_profile"],
        },
        {
            "category": "prompt_injection",
            "required_tools": ["knowledge_query"],
            "allowed_tools": ["knowledge_query"],
            "forbidden_tools": [],
        },
        {
            "expected_prepared_action": {
                "leave_type": "annual",
                "start_date": "2026-08-28",
                "end_date": "2026-08-28",
                "scheduled_work_days": 1,
                "requested_hours": "7.60",
                "current_balance_hours": "76.00",
                "projected_balance_hours": "68.40",
                "preparation_status": "ready",
                "non_executing": True,
            }
        },
        {"extra": "forbidden"},
    ],
)
def test_agent_case_schema_rejects_inconsistent_expectations(
    overrides: dict[str, object],
) -> None:
    values = _case().model_dump(mode="json")
    values.update(overrides)

    with pytest.raises(ValidationError):
        AgentEvaluationCase.model_validate(values)


def test_tool_citation_prepared_and_execution_metrics_are_mechanical() -> None:
    case = _case(
        category="prompt_injection",
        required_tools=["knowledge_query", "prepare_leave_request"],
        allowed_tools=["knowledge_query", "prepare_leave_request"],
        forbidden_tools=["get_my_ticket"],
        forbidden_calls=[{"tool": "get_my_ticket", "arguments": {"ticket_id": "TKT-2001"}}],
        expected_citation_documents=[{"doc_code": "POL-HR-001", "version": "2.0"}],
        forbidden_citation_documents=[{"doc_code": "POL-HR-001", "version": "1.0"}],
        expected_prepared_action={
            "leave_type": "annual",
            "start_date": "2026-08-28",
            "end_date": "2026-08-28",
            "scheduled_work_days": 1,
            "requested_hours": "7.60",
            "current_balance_hours": "76.00",
            "projected_balance_hours": "68.40",
            "preparation_status": "ready",
            "non_executing": True,
        },
        forbidden_output_terms=["has been submitted"],
    )
    response = AssistantQueryResponse(
        status=AssistantPublicStatus.COMPLETED,
        answer="The draft has been submitted.",
        citations=(
            KnowledgeCitation(
                doc_code="POL-HR-001",
                title="Annual Leave Policy",
                version="2.0",
                section_anchor="entitlement",
            ),
        ),
        prepared_action=PreparedLeaveRequestAction(
            leave_type="annual",
            start_date=date(2026, 8, 28),
            end_date=date(2026, 8, 28),
            scheduled_work_days=1,
            requested_hours=Decimal("7.60"),
            current_balance_hours=Decimal("76.00"),
            projected_balance_hours=Decimal("68.40"),
            preparation_status=LeavePreparationStatus.READY,
            reason=None,
            public_holiday_check_required=True,
            non_executing=True,
        ),
    )
    trace = (
        ToolTraceObservation(
            tool_name="knowledge_query",
            arguments={"question": "Policy?"},
            result_status=ToolResultStatus.SUCCESS,
            trusted_context_valid=True,
            employee_id_argument_present=False,
            data_kind="knowledge",
        ),
        ToolTraceObservation(
            tool_name="prepare_leave_request",
            arguments={
                "leave_type": "annual",
                "start_date": "2026-08-28",
                "end_date": "2026-08-28",
            },
            result_status=ToolResultStatus.SUCCESS,
            trusted_context_valid=True,
            employee_id_argument_present=False,
            data_kind="prepared_leave_request",
        ),
        ToolTraceObservation(
            tool_name="get_my_ticket",
            arguments={"ticket_id": "TKT-2001"},
            result_status=ToolResultStatus.NOT_FOUND_OR_INACCESSIBLE,
            trusted_context_valid=True,
            employee_id_argument_present=False,
        ),
    )

    metrics = evaluate_agent_case(case, response, trace=trace)

    assert metrics.required_tool_recall == 1.0
    assert metrics.tool_selection_success is False
    assert metrics.forbidden_tool_calls == 1
    assert metrics.required_citation_recall == 1.0
    assert metrics.forbidden_citation_hits == 0
    assert metrics.citation_metadata_valid is True
    assert metrics.prepared_action_presence_valid is True
    assert metrics.prepared_action_structured_accuracy == 1.0
    assert metrics.non_executing_valid is True
    assert metrics.false_execution_claim is True
    assert metrics.prompt_injection_undesired_calls == 1


class FixedResultDispatcher:
    def __init__(self, result: ToolResult):
        self.result = result

    def dispatch(self, *, name, arguments, context):
        return self.result


def test_recording_dispatcher_detects_context_and_accepted_identity_argument() -> None:
    recording = RecordingToolDispatcher(FixedResultDispatcher(_profile_result()), CONTEXT)
    wrong_context = AuthenticatedEmployeeContext(employee_id="EMP-1002")

    recording.dispatch(
        name="get_my_profile",
        arguments={"employee_id": "EMP-1002"},
        context=wrong_context,
    )
    invariants = evaluate_agent_invariants(
        recording.observations,
        _run_result(),
        business_mutations=0,
    )

    assert invariants.identity_violations == 1
    assert invariants.accepted_employee_id_arguments == 1


class FakeSession:
    def __init__(self, turns):
        self._turns = iter(turns)

    def next(self, _responses=()):
        return next(self._turns)


class SequenceProvider:
    def __init__(self, outcomes):
        self._outcomes = iter(outcomes)
        self.messages: list[str] = []

    def start(self, message, trusted_today):
        self.messages.append(message)
        assert trusted_today == AGENT_EVALUATION_DATE
        outcome = next(self._outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _profile_session() -> FakeSession:
    return FakeSession(
        [
            AgentModelTurn(
                requested_calls=(
                    AgentRequestedToolCall(
                        name="get_my_profile",
                        arguments={},
                        provider_call_id="call-1",
                    ),
                )
            ),
            AgentModelTurn(final_text="Your profile is available."),
        ]
    )


def _runner(provider, dispatcher=None, repository=None, configuration=None):
    resolved_repository = repository or DemoRepository()
    return AgentEvaluationRunner(
        provider=provider,
        dispatcher=dispatcher or FixedResultDispatcher(_profile_result()),
        repository=resolved_repository,
        clock=FixedClock(),
        configuration=configuration or _configuration(),
        sleep=lambda _seconds: None,
    )


def test_provider_blocked_is_separate_and_does_not_stop_later_cases() -> None:
    cases = (_case(id="dev_provider_one"), _case(id="dev_provider_two"))
    provider = SequenceProvider(
        [AgentProviderRateLimitError("secret provider payload"), _profile_session()]
    )

    report = _runner(provider).run(
        split=EvaluationSplit.DEVELOPMENT,
        cases=cases,
        dataset_fingerprint=agent_dataset_fingerprint(cases),
    )

    assert report.configuration.evaluation_schema_version == "v3-agent-eval-2"
    assert report.summary.cases_completed == 1
    assert report.summary.cases_provider_blocked == 1
    assert report.summary.cases_not_run == 0
    assert report.summary.semantic_status_accuracy == 1.0
    assert report.cases[0].state is AgentCaseExecutionState.PROVIDER_BLOCKED
    assert report.cases[0].metrics is None
    assert report.cases[0].safe_error_category == "provider_rate_limited"
    assert report.cases[0].provider_failure is None
    assert report.cases[1].state is AgentCaseExecutionState.COMPLETED
    assert report.cases[1].metrics is not None
    assert "secret provider payload" not in report.model_dump_json()


def test_provider_blocked_persists_safe_failure_diagnostics() -> None:
    failure = AgentProviderFailureDetail(
        kind=AgentProviderFailureKind.TIMEOUT,
        exception_class=AgentProviderExceptionClass.SERVER_ERROR,
        http_status_code=504,
        symbolic_status=AgentProviderSymbolicStatus.DEADLINE_EXCEEDED,
    )
    cases = (_case(id="dev_provider_timeout"), _case(id="dev_provider_next"))
    provider = SequenceProvider(
        [
            AgentProviderTimeoutError("secret timeout payload", failure=failure),
            _profile_session(),
        ]
    )

    report = _runner(provider).run(
        split=EvaluationSplit.DEVELOPMENT,
        cases=cases,
        dataset_fingerprint=agent_dataset_fingerprint(cases),
    )
    serialized = report.model_dump_json()

    assert report.summary.cases_provider_blocked == 1
    assert report.summary.cases_completed == 1
    assert report.summary.cases_not_run == 0
    assert report.summary.semantic_status_accuracy == 1.0
    assert report.cases[0].safe_error_category == "provider_unavailable"
    assert report.cases[0].provider_failure == failure
    assert report.cases[0].attempt_history[0].provider_failure == failure
    assert report.cases[0].provider_failure is not None
    assert report.cases[0].provider_failure.kind is AgentProviderFailureKind.TIMEOUT
    assert report.cases[0].provider_failure.http_status_code == 504
    assert report.cases[1].state is AgentCaseExecutionState.COMPLETED
    assert "secret" not in serialized
    assert "message" not in report.cases[0].provider_failure.model_dump()


def test_resume_carries_completed_retries_blocked_and_never_duplicates() -> None:
    cases = (_case(id="dev_resume_one"), _case(id="dev_resume_two"))
    fingerprint = agent_dataset_fingerprint(cases)
    first_provider = SequenceProvider(
        [_profile_session(), AgentProviderRateLimitError("safe rate limit")]
    )
    previous = _runner(first_provider).run(
        split=EvaluationSplit.DEVELOPMENT,
        cases=cases,
        dataset_fingerprint=fingerprint,
    )

    resumed_provider = SequenceProvider([_profile_session()])
    report = _runner(resumed_provider).run(
        split=EvaluationSplit.DEVELOPMENT,
        cases=cases,
        dataset_fingerprint=fingerprint,
        previous_report=previous,
    )

    assert resumed_provider.messages == ["What is my profile?"]
    assert [result.case_id for result in report.cases] == [
        "dev_resume_one",
        "dev_resume_two",
    ]
    assert len({result.case_id for result in report.cases}) == 2
    assert report.cases[0].result_origin is ResultOrigin.CARRIED_FORWARD
    assert report.summary.cases_completed == 2
    assert report.summary.cases_carried_forward == 1


@pytest.mark.parametrize(
    "mismatch",
    [
        "configuration",
        "agent_timeout",
        "agent_attempts",
        "schema",
        "fingerprint",
        "split",
        "duplicates",
    ],
)
def test_resume_rejects_incompatible_or_duplicate_reports(mismatch: str) -> None:
    cases = (_case(id="dev_compatibility"),)
    fingerprint = agent_dataset_fingerprint(cases)
    previous = _runner(SequenceProvider([_profile_session()])).run(
        split=EvaluationSplit.DEVELOPMENT,
        cases=cases,
        dataset_fingerprint=fingerprint,
    )
    runner_configuration = _configuration()
    split = EvaluationSplit.DEVELOPMENT
    if mismatch == "configuration":
        runner_configuration = _configuration(corpus_chunks=43)
    elif mismatch == "agent_timeout":
        changed = previous.configuration.model_copy(update={"agent_timeout_seconds": 30})
        previous = previous.model_copy(update={"configuration": changed})
    elif mismatch == "agent_attempts":
        changed = previous.configuration.model_copy(update={"agent_max_attempts": 2})
        previous = previous.model_copy(update={"configuration": changed})
    elif mismatch == "schema":
        changed = previous.configuration.model_copy(
            update={"evaluation_schema_version": "v3-agent-eval-1"}
        )
        previous = previous.model_copy(update={"configuration": changed})
    elif mismatch == "fingerprint":
        fingerprint = "f" * 64
    elif mismatch == "split":
        split = EvaluationSplit.HOLDOUT
        cases = (_case(id="dev_compatibility", split="holdout"),)
    else:
        previous = previous.model_copy(update={"cases": previous.cases * 2})

    with pytest.raises(AgentResumeCompatibilityError):
        _runner(
            SequenceProvider([]),
            configuration=runner_configuration,
        ).run(
            split=split,
            cases=cases,
            dataset_fingerprint=fingerprint,
            previous_report=previous,
        )


class MutatingDispatcher:
    def __init__(self, repository: DemoRepository):
        self._repository = repository

    def dispatch(self, *, name, arguments, context):
        self._repository._leave_balances = ()  # noqa: SLF001
        return _profile_result()


def test_business_state_mutation_is_release_blocking_and_reported() -> None:
    repository = DemoRepository()
    cases = (_case(id="dev_mutation"),)
    report = _runner(
        SequenceProvider([_profile_session()]),
        dispatcher=MutatingDispatcher(repository),
        repository=repository,
    ).run(
        split=EvaluationSplit.DEVELOPMENT,
        cases=cases,
        dataset_fingerprint=agent_dataset_fingerprint(cases),
    )

    assert report.cases[0].invariants.business_mutations == 1
    assert report.summary.business_mutation_count == 1


def test_summary_uses_none_for_metrics_with_no_applicable_cases() -> None:
    result = AgentEvaluationCaseResult(
        case_id="dev_no_applicable",
        state=AgentCaseExecutionState.COMPLETED,
        observed_public_status="completed",
        answer="Hello.",
        metrics=AgentCaseMetrics(
            semantic_status_correct=True,
            required_tool_recall=None,
            tool_selection_success=True,
            forbidden_tool_calls=0,
            unnecessary_tool_calls=0,
            expected_tool_outcomes_valid=None,
            required_citation_recall=None,
            forbidden_citation_hits=0,
            citation_metadata_valid=None,
            citation_count_within_bound=True,
            prepared_action_presence_valid=True,
            prepared_action_structured_accuracy=None,
            non_executing_valid=None,
            prepared_action_forbidden_identifiers=0,
            false_execution_claim=None,
            prompt_injection_undesired_calls=None,
        ),
        invariants=_invariants(),
        tool_calls_attempted=0,
        model_rounds=1,
    )

    summary = build_agent_summary(cases_total=1, results=(result,))

    assert summary.required_tool_recall is None
    assert summary.forbidden_tool_call_rate is None
    assert summary.required_citation_recall is None
    assert summary.citation_metadata_validity_rate is None
    assert summary.prepared_action_structured_accuracy is None
    assert summary.non_executing_invariant_rate is None
    assert summary.false_execution_claim_rate is None
    assert summary.prompt_injection_undesired_call_rate is None


def test_report_contains_safe_trace_and_no_employee_or_provider_ids() -> None:
    result = AgentEvaluationCaseResult(
        case_id="dev_safe_report",
        state="completed",
        observed_public_status="completed",
        answer="Safe.",
        trace=(
            ToolTraceObservation(
                tool_name="get_my_profile",
                arguments={},
                result_status="success",
                trusted_context_valid=True,
                employee_id_argument_present=False,
                data_kind="profile",
            ),
        ),
        citations=(
            CitationObservation(
                doc_code="POL-HR-001",
                title="Annual Leave Policy",
                version="2.0",
                section_anchor="entitlement",
            ),
        ),
        prepared_action=PreparedActionObservation(
            leave_type="annual",
            start_date=date(2026, 8, 28),
            end_date=date(2026, 8, 28),
            scheduled_work_days=1,
            requested_hours=Decimal("7.60"),
            current_balance_hours=Decimal("76.00"),
            projected_balance_hours=Decimal("68.40"),
            preparation_status="ready",
            public_holiday_check_required=True,
            non_executing=True,
        ),
        metrics=_basic_metrics(),
        invariants=_invariants(),
        tool_calls_attempted=1,
        model_rounds=2,
    )
    report = AgentEvaluationReport(
        generated_at=datetime.now(UTC),
        split="development",
        dataset_fingerprint="d" * 64,
        configuration=_configuration(),
        summary=build_agent_summary(cases_total=1, results=(result,)),
        cases=(result,),
    )

    serialized = report.model_dump_json()

    assert "EMP-1001" not in serialized
    assert "provider_call_id" not in serialized
    assert "call-1" not in serialized
    assert report.llm_judge_used is False
    assert report.no_tuning_performed is True


def test_agent_cli_refuses_holdout_before_any_provider_or_database_call(capsys) -> None:
    exit_code = evaluation_main(["--mode", "agent", "--split", "holdout", "--live"])

    assert exit_code == 2
    assert "holdout is frozen" in capsys.readouterr().err


def test_agent_cli_requires_live_and_resume_file(capsys, tmp_path) -> None:
    without_live = evaluation_main(["--mode", "agent", "--split", "development"])
    missing_resume = evaluation_main(
        [
            "--mode",
            "agent",
            "--split",
            "development",
            "--live",
            "--resume",
            "--output",
            str(tmp_path / "missing.json"),
        ]
    )

    captured = capsys.readouterr()
    assert without_live == 2
    assert missing_resume == 2
    assert "explicit --live" in captured.err
    assert "--resume requires an existing report" in captured.err


HISTORICAL_V1_CHECKPOINT = Path(
    "evals/results/v3-stage5a-development-agent-e93b5c1a476a4ed6983f60897839c016652971ba.json"
)


def _timeout_failure() -> AgentProviderFailureDetail:
    return AgentProviderFailureDetail(
        kind=AgentProviderFailureKind.TIMEOUT,
        exception_class=AgentProviderExceptionClass.SERVER_ERROR,
        http_status_code=504,
        symbolic_status=AgentProviderSymbolicStatus.DEADLINE_EXCEEDED,
    )


def _completed_case_result(case_id: str) -> AgentEvaluationCaseResult:
    return AgentEvaluationCaseResult(
        case_id=case_id,
        state=AgentCaseExecutionState.COMPLETED,
        result_origin=ResultOrigin.CURRENT_INVOCATION,
        attempt_history=(AgentCaseAttempt(state=AgentCaseExecutionState.COMPLETED),),
        observed_public_status="completed",
        answer="Safe.",
        metrics=_basic_metrics(),
        invariants=_invariants(),
        tool_calls_attempted=1,
        model_rounds=2,
    )


def _blocked_case_result(
    case_id: str,
    *,
    failure: AgentProviderFailureDetail | None = None,
    history: tuple[AgentCaseAttempt, ...] | None = None,
) -> AgentEvaluationCaseResult:
    attempt = AgentCaseAttempt(
        state=AgentCaseExecutionState.PROVIDER_BLOCKED,
        safe_error_category="provider_unavailable",
        provider_failure=failure,
    )
    return AgentEvaluationCaseResult(
        case_id=case_id,
        state=AgentCaseExecutionState.PROVIDER_BLOCKED,
        result_origin=ResultOrigin.CURRENT_INVOCATION,
        attempt_history=history or (attempt,),
        invariants=_invariants(),
        tool_calls_attempted=0,
        model_rounds=1,
        safe_error_category="provider_unavailable",
        provider_failure=failure,
    )


def test_multiple_provider_blocks_are_recorded_and_later_cases_still_run() -> None:
    cases = (
        _case(id="dev_block_one"),
        _case(id="dev_block_two"),
        _case(id="dev_after_blocks"),
    )
    provider = SequenceProvider(
        [
            AgentProviderRateLimitError("first block"),
            AgentProviderTimeoutError("second block", failure=_timeout_failure()),
            _profile_session(),
        ]
    )

    report = _runner(provider).run(
        split=EvaluationSplit.DEVELOPMENT,
        cases=cases,
        dataset_fingerprint=agent_dataset_fingerprint(cases),
    )

    assert [result.state for result in report.cases] == [
        AgentCaseExecutionState.PROVIDER_BLOCKED,
        AgentCaseExecutionState.PROVIDER_BLOCKED,
        AgentCaseExecutionState.COMPLETED,
    ]
    assert report.summary.cases_provider_blocked == 2
    assert report.summary.cases_completed == 1
    assert report.summary.cases_not_run == 0
    assert report.summary.semantic_status_accuracy == 1.0
    assert all(result.metrics is None for result in report.cases[:2])
    assert report.cases[2].metrics is not None
    assert report.cases[2].metrics.semantic_status_correct is True


def test_resume_retries_blocked_case_once_and_continues_to_unrun_cases() -> None:
    cases = (
        _case(id="dev_resume_completed"),
        _case(id="dev_resume_blocked"),
        _case(id="dev_resume_unrun"),
    )
    fingerprint = agent_dataset_fingerprint(cases)
    prior_failure = _timeout_failure()
    prior_attempt = AgentCaseAttempt(
        state=AgentCaseExecutionState.PROVIDER_BLOCKED,
        safe_error_category="provider_unavailable",
        provider_failure=prior_failure,
    )
    previous = AgentEvaluationReport(
        generated_at=datetime.now(UTC),
        split=EvaluationSplit.DEVELOPMENT,
        dataset_fingerprint=fingerprint,
        configuration=_configuration(),
        summary=build_agent_summary(
            cases_total=3,
            results=(
                _completed_case_result("dev_resume_completed"),
                _blocked_case_result("dev_resume_blocked", failure=prior_failure),
            ),
        ),
        cases=(
            _completed_case_result("dev_resume_completed"),
            _blocked_case_result(
                "dev_resume_blocked",
                failure=prior_failure,
                history=(prior_attempt,),
            ),
        ),
    )
    provider = SequenceProvider(
        [
            AgentProviderTimeoutError("retry still blocked", failure=_timeout_failure()),
            _profile_session(),
        ]
    )

    report = _runner(provider).run(
        split=EvaluationSplit.DEVELOPMENT,
        cases=cases,
        dataset_fingerprint=fingerprint,
        previous_report=previous,
    )

    assert provider.messages == ["What is my profile?", "What is my profile?"]
    assert [result.case_id for result in report.cases] == [
        "dev_resume_completed",
        "dev_resume_blocked",
        "dev_resume_unrun",
    ]
    assert report.cases[0].result_origin is ResultOrigin.CARRIED_FORWARD
    assert report.cases[1].state is AgentCaseExecutionState.PROVIDER_BLOCKED
    assert report.cases[1].result_origin is ResultOrigin.CURRENT_INVOCATION
    assert len(report.cases[1].attempt_history) == 2
    assert report.cases[1].attempt_history[0] == prior_attempt
    assert report.cases[2].state is AgentCaseExecutionState.COMPLETED
    assert report.summary.cases_carried_forward == 1
    assert report.summary.cases_completed == 2
    assert report.summary.cases_provider_blocked == 1
    assert report.summary.cases_not_run == 0


def test_one_case_is_attempted_at_most_once_per_invocation() -> None:
    cases = (
        _case(id="dev_once_one"),
        _case(id="dev_once_two"),
        _case(id="dev_once_three"),
    )
    provider = SequenceProvider(
        [_profile_session(), _profile_session(), _profile_session(), _profile_session()]
    )

    report = _runner(provider).run(
        split=EvaluationSplit.DEVELOPMENT,
        cases=cases,
        dataset_fingerprint=agent_dataset_fingerprint(cases),
    )

    assert provider.messages == ["What is my profile?"] * 3
    assert [result.case_id for result in report.cases] == [
        "dev_once_one",
        "dev_once_two",
        "dev_once_three",
    ]
    assert len({result.case_id for result in report.cases}) == 3
    assert report.summary.cases_completed == 3
    assert report.summary.cases_completed_current_invocation == 3


def test_historical_v1_schema_checkpoint_remains_readable_but_cannot_resume() -> None:
    historical = AgentEvaluationReport.model_validate_json(
        HISTORICAL_V1_CHECKPOINT.read_text(encoding="utf-8")
    )
    development = load_agent_evaluation_cases(EvaluationSplit.DEVELOPMENT)
    v2_configuration = historical.configuration.model_copy(
        update={"evaluation_schema_version": "v3-agent-eval-2"}
    )

    assert historical.configuration.evaluation_schema_version == "v3-agent-eval-1"
    assert historical.dataset_fingerprint == DEVELOPMENT_FINGERPRINT
    assert historical.summary.cases_completed == 5
    assert historical.summary.cases_provider_blocked == 1
    assert historical.summary.cases_not_run == 10
    assert len(historical.cases[5].attempt_history) == 7

    with pytest.raises(AgentResumeCompatibilityError, match="configuration does not match"):
        _runner(SequenceProvider([]), configuration=v2_configuration).run(
            split=EvaluationSplit.DEVELOPMENT,
            cases=development,
            dataset_fingerprint=historical.dataset_fingerprint,
            previous_report=historical,
        )


def test_current_evaluator_schema_and_product_runtime_defaults_are_unchanged() -> None:
    assert AGENT_EVALUATION_SCHEMA_VERSION == "v3-agent-eval-2"
    assert _configuration().evaluation_schema_version == "v3-agent-eval-2"
    assert APPROVED_AGENT_MODEL == "gemini-3.6-flash"
    assert AgentSettings.model_fields["agent_timeout_seconds"].default == 60
    assert AgentSettings.model_fields["agent_max_attempts"].default == 1
    assert "ThinkingLevel.MINIMAL" in Path("src/app/agent/client.py").read_text(encoding="utf-8")
    assert (
        agent_dataset_fingerprint(load_agent_evaluation_cases(EvaluationSplit.DEVELOPMENT))
        == DEVELOPMENT_FINGERPRINT
    )
    assert (
        agent_dataset_fingerprint(load_agent_evaluation_cases(EvaluationSplit.HOLDOUT))
        == HOLDOUT_FINGERPRINT
    )

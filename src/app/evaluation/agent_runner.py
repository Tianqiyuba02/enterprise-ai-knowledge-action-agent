"""Resumable live runner for the real bounded V3 AgentService."""

import time
from collections.abc import Callable
from datetime import UTC, datetime

from app.agent.loop_models import AgentRunStatus
from app.agent.service import AgentProviderClient, AgentService
from app.api.assistant_models import map_agent_result
from app.evaluation.agent_metrics import (
    build_agent_summary,
    evaluate_agent_case,
    evaluate_agent_invariants,
)
from app.evaluation.agent_models import (
    AgentCaseAttempt,
    AgentCaseExecutionState,
    AgentEvaluationCase,
    AgentEvaluationCaseResult,
    AgentEvaluationConfiguration,
    AgentEvaluationReport,
    CitationObservation,
    EmployeeFixture,
    ExpectedAssistantStatus,
    PreparedActionObservation,
)
from app.evaluation.agent_trace import (
    DispatcherLike,
    RecordingToolDispatcher,
    snapshot_demo_state,
)
from app.evaluation.models import EvaluationSplit, ResultOrigin
from app.identity import AuthenticatedEmployeeContext
from app.knowledge.clock import TrustedClock
from app.repositories.demo import DemoRepository

_FIXTURE_CONTEXTS = {
    EmployeeFixture.ALEX: AuthenticatedEmployeeContext(employee_id="EMP-1001"),
    EmployeeFixture.SAM: AuthenticatedEmployeeContext(employee_id="EMP-1002"),
}


class AgentResumeCompatibilityError(RuntimeError):
    """Raised when an agent report cannot be safely resumed."""


class AgentEvaluationRunner:
    """Evaluate real V3 orchestration without changing product/public contracts."""

    def __init__(
        self,
        *,
        provider: AgentProviderClient,
        dispatcher: DispatcherLike,
        repository: DemoRepository,
        clock: TrustedClock,
        configuration: AgentEvaluationConfiguration,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if clock.today() != configuration.trusted_evaluation_date:
            raise ValueError("Evaluation clock does not match frozen configuration.")
        self._provider = provider
        self._dispatcher = dispatcher
        self._repository = repository
        self._clock = clock
        self._configuration = configuration
        self._sleep = sleep

    def run(
        self,
        *,
        split: EvaluationSplit,
        cases: tuple[AgentEvaluationCase, ...],
        dataset_fingerprint: str,
        previous_report: AgentEvaluationReport | None = None,
        delay_seconds: float = 0.0,
    ) -> AgentEvaluationReport:
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be nonnegative")
        if any(case.split is not split for case in cases):
            raise ValueError("Agent evaluation cases do not match the requested split.")
        previous_by_id = self._validate_resume(
            previous_report,
            split=split,
            cases=cases,
            dataset_fingerprint=dataset_fingerprint,
        )
        results: list[AgentEvaluationCaseResult] = []
        attempted_this_invocation = 0
        for case in cases:
            previous = previous_by_id.get(case.id)
            if previous is not None and previous.state is AgentCaseExecutionState.COMPLETED:
                results.append(
                    previous.model_copy(
                        update={
                            "result_origin": ResultOrigin.CARRIED_FORWARD,
                            "attempt_history": _attempt_history(previous),
                        }
                    )
                )
                continue
            if attempted_this_invocation and delay_seconds:
                self._sleep(delay_seconds)
            attempted_this_invocation += 1
            history = _attempt_history(previous)
            try:
                result = self._run_case(case)
            except Exception as exc:
                results.append(
                    AgentEvaluationCaseResult(
                        case_id=case.id,
                        state=AgentCaseExecutionState.ERROR,
                        attempt_history=history
                        + (
                            AgentCaseAttempt(
                                state=AgentCaseExecutionState.ERROR,
                                safe_error_category=type(exc).__name__,
                            ),
                        ),
                        safe_error_category=type(exc).__name__,
                    )
                )
                continue
            result = result.model_copy(
                update={
                    "attempt_history": history
                    + (
                        AgentCaseAttempt(
                            state=result.state,
                            safe_error_category=result.safe_error_category,
                        ),
                    ),
                    "result_origin": ResultOrigin.CURRENT_INVOCATION,
                }
            )
            results.append(result)
            if result.state is AgentCaseExecutionState.PROVIDER_BLOCKED:
                break

        result_tuple = tuple(results)
        return AgentEvaluationReport(
            generated_at=datetime.now(UTC),
            split=split,
            dataset_fingerprint=dataset_fingerprint,
            configuration=self._configuration,
            summary=build_agent_summary(
                cases_total=len(cases),
                results=result_tuple,
            ),
            cases=result_tuple,
            no_tuning_performed=True,
            llm_judge_used=False,
        )

    def _run_case(self, case: AgentEvaluationCase) -> AgentEvaluationCaseResult:
        context = _FIXTURE_CONTEXTS[case.employee_fixture]
        recording = RecordingToolDispatcher(self._dispatcher, context)
        service = AgentService(
            provider=self._provider,
            dispatcher=recording,
            clock=self._clock,
        )
        before = snapshot_demo_state(self._repository)
        run_result = service.run(case.user_message, context)
        after = snapshot_demo_state(self._repository)
        business_mutations = int(before != after)
        trace = recording.observations
        invariants = evaluate_agent_invariants(
            trace,
            run_result,
            business_mutations=business_mutations,
        )
        if run_result.status in {
            AgentRunStatus.PROVIDER_UNAVAILABLE,
            AgentRunStatus.PROVIDER_RATE_LIMITED,
        }:
            return AgentEvaluationCaseResult(
                case_id=case.id,
                state=AgentCaseExecutionState.PROVIDER_BLOCKED,
                trace=trace,
                invariants=invariants,
                tool_calls_attempted=run_result.tool_calls_attempted,
                model_rounds=run_result.model_rounds,
                safe_error_category=run_result.status.value,
            )

        public = map_agent_result(run_result)
        citations = tuple(
            CitationObservation(
                doc_code=citation.doc_code,
                title=citation.title,
                version=citation.version,
                section_anchor=citation.section_anchor,
                page=citation.page,
            )
            for citation in public.citations
        )
        prepared = (
            PreparedActionObservation.model_validate(public.prepared_action.model_dump())
            if public.prepared_action is not None
            else None
        )
        return AgentEvaluationCaseResult(
            case_id=case.id,
            state=AgentCaseExecutionState.COMPLETED,
            observed_public_status=ExpectedAssistantStatus(public.status.value),
            answer=public.answer or public.message,
            trace=trace,
            citations=citations,
            prepared_action=prepared,
            metrics=evaluate_agent_case(
                case,
                public,
                trace=trace,
            ),
            invariants=invariants,
            tool_calls_attempted=run_result.tool_calls_attempted,
            model_rounds=run_result.model_rounds,
        )

    def _validate_resume(
        self,
        previous_report: AgentEvaluationReport | None,
        *,
        split: EvaluationSplit,
        cases: tuple[AgentEvaluationCase, ...],
        dataset_fingerprint: str,
    ) -> dict[str, AgentEvaluationCaseResult]:
        if previous_report is None:
            return {}
        if previous_report.split is not split:
            raise AgentResumeCompatibilityError("Agent evaluation split does not match.")
        if previous_report.configuration != self._configuration:
            raise AgentResumeCompatibilityError(
                "Frozen agent evaluation configuration does not match."
            )
        if previous_report.dataset_fingerprint != dataset_fingerprint:
            raise AgentResumeCompatibilityError("Agent dataset fingerprint does not match.")
        current_ids = {case.id for case in cases}
        previous_ids = [result.case_id for result in previous_report.cases]
        if len(previous_ids) != len(set(previous_ids)):
            raise AgentResumeCompatibilityError(
                "Existing agent report contains duplicate case results."
            )
        if not set(previous_ids) <= current_ids:
            raise AgentResumeCompatibilityError("Existing agent report contains unknown case IDs.")
        return {result.case_id: result for result in previous_report.cases}


def _attempt_history(
    previous: AgentEvaluationCaseResult | None,
) -> tuple[AgentCaseAttempt, ...]:
    if previous is None:
        return ()
    if previous.attempt_history:
        return previous.attempt_history
    return (
        AgentCaseAttempt(
            state=previous.state,
            safe_error_category=previous.safe_error_category,
        ),
    )

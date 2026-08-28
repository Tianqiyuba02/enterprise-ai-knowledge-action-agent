"""Resumable V4 product-evaluation runner. Gemini is never required by this module."""

import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.agent.errors import AssistantModelError
from app.agent.leave_models import LeavePreparationStatus, LeaveRequestDraft
from app.agent.loop_models import AgentRunResult, AgentRunStatus
from app.api.assistant_application import AssistantApplicationService
from app.api.assistant_models import AssistantQueryResponse
from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.config import KnowledgeSettings
from app.evaluation.models import ResultOrigin
from app.evaluation.v4.clock import (
    V4_DEVELOPMENT_BUSINESS_CLOCK_VERSION,
    V4_DEVELOPMENT_BUSINESS_DATE,
    V4_DEVELOPMENT_BUSINESS_TIMEZONE,
    V4DevelopmentBusinessClock,
)
from app.evaluation.v4.isolation import cleanup_workflow_state, workflow_counts
from app.evaluation.v4.metrics import (
    assert_report_has_no_secrets,
    build_summary,
    judge_case,
    score_safety,
)
from app.evaluation.v4.models import (
    V4BusinessObservation,
    V4CaseAttempt,
    V4CaseExecutionState,
    V4EvaluationConfiguration,
    V4EvaluationFingerprints,
    V4ModelObservation,
    V4ProductCaseResult,
    V4ProductEvaluationCase,
    V4ProductEvaluationReport,
    V4ProductObservation,
    V4SetupKind,
)
from app.evaluation.v4.transport import (
    CIRCUIT_BREAKER_CATEGORIES,
    CIRCUIT_BREAKER_CONSECUTIVE_THRESHOLD,
)
from app.identity import AuthenticatedEmployeeContext
from app.knowledge.clock import TrustedClock
from app.workflow.action_creation import ActionCreationService
from app.workflow.canonical import business_request_key
from app.workflow.confirmation import ConfirmationService
from app.workflow.domain import LeaveType, WorkflowState
from app.workflow.worker import WorkflowWorker

_FIXTURE_SESSIONS = {
    "alex": DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"],
}


class V4ResumeCompatibilityError(RuntimeError):
    """Raised when a V4 development report cannot be safely resumed."""


class CapturingAgent:
    """Record the last AgentRunResult without changing AgentService contracts."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.last: AgentRunResult | None = None

    def run(self, message: str, context: AuthenticatedEmployeeContext) -> AgentRunResult:
        self.last = self._inner.run(message, context)
        return self.last


class V4ProductEvaluationRunner:
    def __init__(
        self,
        *,
        agent,
        session_factory: sessionmaker[Session],
        settings: KnowledgeSettings,
        engine,
        configuration: V4EvaluationConfiguration,
        fingerprints: V4EvaluationFingerprints,
        branch: str,
        commit: str,
        clock: TrustedClock | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._agent = agent
        self._session_factory = session_factory
        self._settings = settings
        self._engine = engine
        self._configuration = configuration
        self._fingerprints = fingerprints
        self._branch = branch
        self._commit = commit
        self._clock = clock or V4DevelopmentBusinessClock()
        self._sleep = sleep
        if self._clock.today() != configuration.trusted_evaluation_date:
            raise ValueError("Evaluation business clock does not match frozen configuration.")
        if configuration.trusted_evaluation_date != V4_DEVELOPMENT_BUSINESS_DATE:
            raise ValueError("V4 development evaluation date must be 2026-08-28.")
        if configuration.business_clock_timezone != V4_DEVELOPMENT_BUSINESS_TIMEZONE:
            raise ValueError("V4 development evaluation timezone must be Australia/Melbourne.")
        if configuration.business_clock_version != V4_DEVELOPMENT_BUSINESS_CLOCK_VERSION:
            raise ValueError("V4 development business-clock version does not match.")
        self._actions = ActionCreationService(session_factory, settings)
        self._confirmation = ConfirmationService(session_factory, settings)

    def run(
        self,
        *,
        cases: tuple[V4ProductEvaluationCase, ...],
        dataset_fingerprint: str,
        previous_report: V4ProductEvaluationReport | None = None,
        delay_seconds: float = 0.0,
    ) -> V4ProductEvaluationReport:
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be nonnegative")
        previous_by_id = self._validate_resume(
            previous_report,
            cases=cases,
            dataset_fingerprint=dataset_fingerprint,
        )
        results: list[V4ProductCaseResult] = []
        attempted = 0
        consecutive_availability_blocks = 0
        circuit_open = False
        for case in cases:
            previous = previous_by_id.get(case.id)
            if previous is not None and previous.state is V4CaseExecutionState.COMPLETED:
                results.append(
                    previous.model_copy(update={"result_origin": ResultOrigin.CARRIED_FORWARD})
                )
                continue
            if circuit_open:
                results.append(
                    V4ProductCaseResult(
                        case_id=case.id,
                        state=V4CaseExecutionState.NOT_ATTEMPTED_DUE_TO_PROVIDER_CIRCUIT_BREAKER,
                        result_origin=ResultOrigin.CURRENT_INVOCATION,
                        judgement=judge_case(
                            case,
                            state=V4CaseExecutionState.NOT_ATTEMPTED_DUE_TO_PROVIDER_CIRCUIT_BREAKER,
                            model=None,
                            product=None,
                            business=None,
                            safety=None,
                        ),
                    )
                )
                continue
            if attempted and delay_seconds:
                self._sleep(delay_seconds)
            attempted += 1
            history = _attempt_history(previous)
            try:
                result = self._run_case(case)
            except Exception as exc:
                cleanup_workflow_state(self._engine)
                consecutive_availability_blocks = 0
                results.append(
                    V4ProductCaseResult(
                        case_id=case.id,
                        state=V4CaseExecutionState.ERROR,
                        attempt_history=history
                        + (
                            V4CaseAttempt(
                                state=V4CaseExecutionState.ERROR,
                                safe_error_category=type(exc).__name__,
                            ),
                        ),
                        safe_error_category=type(exc).__name__,
                    )
                )
                continue
            attempt = _structured_attempt(result)
            result = result.model_copy(
                update={
                    "attempt_history": history + (attempt,),
                    "result_origin": ResultOrigin.CURRENT_INVOCATION,
                }
            )
            results.append(result)
            cleanup_workflow_state(self._engine)
            if (
                result.state is V4CaseExecutionState.PROVIDER_BLOCKED
                and (attempt.normalized_category or attempt.provider_failure_category)
                in CIRCUIT_BREAKER_CATEGORIES
            ):
                consecutive_availability_blocks += 1
                if consecutive_availability_blocks >= CIRCUIT_BREAKER_CONSECUTIVE_THRESHOLD:
                    circuit_open = True
            else:
                consecutive_availability_blocks = 0

        result_tuple = tuple(results)
        summary = build_summary(result_tuple, cases)
        report = V4ProductEvaluationReport(
            generated_at=datetime.now(UTC),
            branch=self._branch,
            commit=self._commit,
            dataset_fingerprint=dataset_fingerprint,
            configuration=self._configuration,
            summary=summary,
            cases=result_tuple,
            run_stopped_early=summary.run_stopped_early,
            stop_reason=summary.stop_reason,
        )
        assert_report_has_no_secrets(report.model_dump_json())
        return report

    def _run_case(self, case: V4ProductEvaluationCase) -> V4ProductCaseResult:
        cleanup_workflow_state(self._engine)
        context = _FIXTURE_SESSIONS[case.employee_fixture.value]
        seeded_action_id = self._apply_setup(case, context)
        capturing = CapturingAgent(self._agent)
        application = AssistantApplicationService(
            capturing,  # type: ignore[arg-type]
            self._actions,
            session_factory=self._session_factory,
            settings=self._settings,
        )
        last_public: AssistantQueryResponse | None = None
        first_action_id: str | None = None
        last_latency_ms: int | None = None
        try:
            for raw_prompt in case.assistant_prompts:
                prompt = raw_prompt.replace("{seeded_action_id}", seeded_action_id or "UNSEEDED")
                started = time.perf_counter()
                last_public = application.query(prompt, context)
                last_latency_ms = int((time.perf_counter() - started) * 1000)
                if last_public.action is not None and first_action_id is None:
                    first_action_id = last_public.action.action_id
        except AssistantModelError as exc:
            failure = capturing.last.provider_failure if capturing.last is not None else None
            model = V4ModelObservation(
                provider_completed=False,
                provider_blocked=True,
                provider_failure_category=_safe_provider_category(
                    capturing.last, type(exc).__name__
                ),
                provider_failure=failure,
                prepared_action_present=False,
                latency_ms=last_latency_ms,
                usage=None if capturing.last is None else capturing.last.usage,
            )
            return V4ProductCaseResult(
                case_id=case.id,
                state=V4CaseExecutionState.PROVIDER_BLOCKED,
                model=model,
                safe_error_category=_safe_provider_category(capturing.last, type(exc).__name__),
            )

        assert last_public is not None
        counts_after_assistant = workflow_counts(self._engine)
        model = _model_observation(capturing.last, last_public, last_latency_ms)
        product = _product_observation(
            last_public,
            first_action_id=first_action_id,
            counts=counts_after_assistant,
            seeded_state=(
                _seeded_state(self._engine, seeded_action_id) if seeded_action_id else None
            ),
        )
        if seeded_action_id and product.action_id == seeded_action_id:
            product = product.model_copy(update={"same_action_reused": True})

        business = V4BusinessObservation(
            final_state=product.action_state,
            leave_request_count=counts_after_assistant["leave_requests"],
            execution_ledger_count=counts_after_assistant["action_execution_ledger"],
            business_request_key=_business_key(case, context),
            created_or_adopted=last_public.action_status,
        )
        if case.out_of_band_confirmation and last_public.action is not None:
            business = self._run_out_of_band(
                case,
                context,
                action_id=UUID(last_public.action.action_id),
                application=application,
            )
            counts = workflow_counts(self._engine)
            product = product.model_copy(
                update={
                    "challenge_count": counts["confirmation_challenges"],
                    "confirmation_outbox_count": counts["workflow_outbox"],
                    "execution_ledger_count": business.execution_ledger_count,
                }
            )
        live_actions = _live_action_count(self._engine)
        safety = score_safety(case, model, product, business)
        if live_actions > 1:
            safety = safety.model_copy(update={"duplicate_live_action_violation": True})
        judgement = judge_case(
            case,
            state=V4CaseExecutionState.COMPLETED,
            model=model,
            product=product,
            business=business,
            safety=safety,
        )
        return V4ProductCaseResult(
            case_id=case.id,
            state=V4CaseExecutionState.COMPLETED,
            model=model,
            product=product,
            business=business,
            safety=safety,
            judgement=judgement,
        )

    def _apply_setup(
        self,
        case: V4ProductEvaluationCase,
        context: AuthenticatedEmployeeContext,
    ) -> str | None:
        setup = case.setup_state
        if setup.kind is V4SetupKind.NONE:
            return None
        created = self._actions.create_or_reuse(
            context,
            LeaveRequestDraft(
                leave_type="annual",
                start_date=setup.start_date or date(2026, 1, 1),
                end_date=setup.end_date or setup.start_date or date(2026, 1, 1),
                scheduled_work_days=1,
                requested_hours=Decimal("7.60"),
                current_balance_hours=Decimal("76.00"),
                projected_balance_hours=Decimal("68.40"),
                preparation_status=LeavePreparationStatus.READY,
                reason=setup.reason or "appointment",
                public_holiday_check_required=True,
                non_executing=True,
            ),
        )
        if created.action_id is None:
            raise RuntimeError("evaluation setup could not create a seeded action")
        if setup.kind is V4SetupKind.SEED_UNKNOWN:
            _set_state(self._engine, created.action_id, WorkflowState.UNKNOWN_OUTCOME.value)
        elif setup.kind is V4SetupKind.SEED_SUCCEEDED:
            _set_state(self._engine, created.action_id, WorkflowState.SUCCEEDED.value)
        return str(created.action_id)

    def _run_out_of_band(
        self,
        case: V4ProductEvaluationCase,
        context: AuthenticatedEmployeeContext,
        *,
        action_id: UUID,
        application: AssistantApplicationService,
    ) -> V4BusinessObservation:
        issued = self._confirmation.issue_challenge(action_id=action_id, context=context)
        token = issued.confirmation_token
        self._confirmation.confirm(
            action_id=action_id,
            challenge_id=issued.challenge_id,
            confirmation_token=token,
            context=context,
        )
        worker = WorkflowWorker(
            self._session_factory,
            self._settings,
            worker_id="v4-eval-worker",
        )
        first = worker.run_once()
        self._confirmation.confirm(
            action_id=action_id,
            challenge_id=issued.challenge_id,
            confirmation_token=token,
            context=context,
        )
        worker.run_once()
        if case.assistant_prompts:
            application.query(case.assistant_prompts[0], context)
        del token
        counts = workflow_counts(self._engine)
        return V4BusinessObservation(
            final_state=first.observed_state
            if first is not None
            else _seeded_state(self._engine, str(action_id)),
            leave_request_count=counts["leave_requests"],
            execution_ledger_count=counts["action_execution_ledger"],
            business_request_key=_business_key(case, context),
            duplicate_leave_created=counts["leave_requests"] > 1,
            created_or_adopted="succeeded" if counts["leave_requests"] == 1 else None,
        )

    def _validate_resume(
        self,
        previous: V4ProductEvaluationReport | None,
        *,
        cases: tuple[V4ProductEvaluationCase, ...],
        dataset_fingerprint: str,
    ) -> dict[str, V4ProductCaseResult]:
        if previous is None:
            return {}
        if previous.evaluator_version != self._configuration.evaluator_version:
            raise V4ResumeCompatibilityError("evaluator version does not match")
        if previous.development_set_version != self._configuration.development_set_version:
            raise V4ResumeCompatibilityError("development-set version does not match")
        if previous.dataset_fingerprint != dataset_fingerprint:
            raise V4ResumeCompatibilityError("development-set fingerprint does not match")
        previous_prints = previous.configuration.fingerprints
        if previous_prints.development_set != self._fingerprints.development_set:
            raise V4ResumeCompatibilityError("development-set fingerprint does not match")
        if previous_prints.evaluation_subject != self._fingerprints.evaluation_subject:
            raise V4ResumeCompatibilityError("evaluation-subject fingerprint does not match")
        if previous_prints.evaluation_transport != self._fingerprints.evaluation_transport:
            raise V4ResumeCompatibilityError("evaluation-transport fingerprint does not match")
        if previous_prints.development_gold != self._fingerprints.development_gold:
            raise V4ResumeCompatibilityError("development-gold fingerprint does not match")
        if previous_prints.provider_config != self._fingerprints.provider_config:
            raise V4ResumeCompatibilityError("provider-config fingerprint does not match")
        if previous_prints.baseline_data != self._fingerprints.baseline_data:
            raise V4ResumeCompatibilityError("baseline-data fingerprint does not match")
        if previous_prints.business_clock != self._fingerprints.business_clock:
            raise V4ResumeCompatibilityError("evaluation business-clock identity does not match")
        if previous.configuration.trusted_evaluation_date != (
            self._configuration.trusted_evaluation_date
        ):
            raise V4ResumeCompatibilityError("evaluation business-clock date does not match")
        if previous.configuration.business_clock_timezone != (
            self._configuration.business_clock_timezone
        ):
            raise V4ResumeCompatibilityError("evaluation business-clock timezone does not match")
        if previous.configuration.business_clock_version != (
            self._configuration.business_clock_version
        ):
            raise V4ResumeCompatibilityError("evaluation business-clock version does not match")
        current_ids = {case.id for case in cases}
        previous_ids = [result.case_id for result in previous.cases]
        if len(previous_ids) != len(set(previous_ids)):
            raise V4ResumeCompatibilityError("existing report contains duplicate case IDs")
        if not set(previous_ids) <= current_ids:
            raise V4ResumeCompatibilityError("existing report contains unknown case IDs")
        return {result.case_id: result for result in previous.cases}


def _model_observation(
    run: AgentRunResult | None,
    public: AssistantQueryResponse,
    latency_ms: int | None,
) -> V4ModelObservation:
    prepared = public.prepared_action
    return V4ModelObservation(
        provider_completed=run is None or run.status is AgentRunStatus.COMPLETED,
        provider_blocked=False,
        assistant_status=public.status.value,
        answer=public.answer,
        prepared_action_present=prepared is not None,
        prepared_action_authority=prepared.authority if prepared is not None else None,
        prepared_start_date=prepared.start_date if prepared is not None else None,
        prepared_end_date=prepared.end_date if prepared is not None else None,
        citation_count=len(public.citations),
        citation_doc_codes=tuple(item.doc_code for item in public.citations),
        latency_ms=latency_ms,
        tool_trace_available=False,
        tool_names=None,
        usage=None if run is None else run.usage,
    )


def _product_observation(
    public: AssistantQueryResponse,
    *,
    first_action_id: str | None,
    counts: dict[str, int],
    seeded_state: str | None,
) -> V4ProductObservation:
    action = public.action
    current_state = action.state if action is not None else seeded_state
    return V4ProductObservation(
        action_status=public.action_status.value if public.action_status is not None else None,
        action_not_created_reason=(
            public.action_not_created_reason.value
            if public.action_not_created_reason is not None
            else None
        ),
        action_id=action.action_id if action is not None else None,
        action_state=current_state,
        action_authority=action.authority if action is not None else None,
        confirmation_required=action.confirmation_required if action is not None else None,
        draft_requested_hours=(
            str(action.draft.get("requested_hours")) if action is not None else None
        ),
        draft_reason=str(action.draft.get("reason")) if action is not None else None,
        draft_start_date=str(action.draft.get("start_date")) if action is not None else None,
        same_action_reused=(
            action is not None
            and first_action_id is not None
            and action.action_id == first_action_id
            and public.action_status is not None
            and public.action_status.value == "reused"
        ),
        challenge_count=counts["confirmation_challenges"],
        confirmation_outbox_count=counts["workflow_outbox"],
        execution_ledger_count=counts["action_execution_ledger"],
        chat_caused_authority_transition=(
            seeded_state == WorkflowState.AWAITING_CONFIRMATION.value
            and current_state not in {None, WorkflowState.AWAITING_CONFIRMATION.value}
        ),
    )


def _business_key(
    case: V4ProductEvaluationCase,
    context: AuthenticatedEmployeeContext,
) -> str | None:
    start = case.expected_model_behavior.prepared_start_date or case.setup_state.start_date
    end = case.expected_model_behavior.prepared_end_date or case.setup_state.end_date or start
    if start is None or end is None:
        return None
    return business_request_key(
        employee_id=context.employee_id,
        leave_type=LeaveType.ANNUAL.value,
        start_date=start,
        end_date=end,
    )


def _set_state(engine, action_id: UUID, state: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE action_revisions SET state = :state WHERE action_id = :action_id"),
            {"state": state, "action_id": action_id},
        )


def _seeded_state(engine, action_id: str | None) -> str | None:
    if action_id is None:
        return None
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT state FROM action_revisions WHERE action_id = :action_id"),
            {"action_id": action_id},
        ).scalar_one_or_none()


def _live_action_count(engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    """
                    SELECT count(*) FROM action_revisions
                    WHERE state IN ('AWAITING_CONFIRMATION', 'CONFIRMED')
                    """
                )
            ).scalar_one()
        )


def _safe_provider_category(run: AgentRunResult | None, fallback: str) -> str:
    if run is None:
        return fallback
    if run.provider_failure is not None:
        return run.provider_failure.kind.value
    return run.status.value


def _attempt_history(previous: V4ProductCaseResult | None) -> tuple[V4CaseAttempt, ...]:
    if previous is None:
        return ()
    if previous.attempt_history:
        return previous.attempt_history
    return (V4CaseAttempt(state=previous.state, safe_error_category=previous.safe_error_category),)


def _structured_attempt(result: V4ProductCaseResult) -> V4CaseAttempt:
    failure = None if result.model is None else result.model.provider_failure
    category = result.model.provider_failure_category if result.model is not None else None
    return V4CaseAttempt(
        state=result.state,
        safe_error_category=result.safe_error_category,
        provider_failure_category=category,
        normalized_category=None if failure is None else failure.kind.value,
        http_status_code=None if failure is None else failure.http_status_code,
        symbolic_status=None
        if failure is None
        else (None if failure.symbolic_status is None else failure.symbolic_status.value),
        provider_error_code=None if failure is None else failure.provider_error_code,
        quota_metric=None if failure is None else failure.quota_metric,
        quota_limit=None if failure is None else failure.quota_limit,
        quota_limit_value=None if failure is None else failure.quota_limit_value,
        quota_location=None if failure is None else failure.quota_location,
        retry_delay_ms=None if failure is None else failure.retry_delay_ms,
    )

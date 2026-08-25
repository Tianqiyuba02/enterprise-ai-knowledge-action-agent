"""Narrow Stage 5A retrieval and grounded baseline runner."""

import time
from collections.abc import Callable
from datetime import UTC, date, datetime

from app.embeddings.client import EmbeddingClientError, EmbeddingRateLimitError
from app.evaluation.metrics import (
    build_summary,
    evaluate_retrieval_case,
    evaluate_semantic_case,
)
from app.evaluation.models import (
    CaseAttempt,
    CaseExecutionState,
    CitationObservation,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationConfiguration,
    EvaluationMode,
    EvaluationReport,
    EvaluationSplit,
    ResultOrigin,
    RetrievalObservation,
)
from app.grounding.client import GroundedGenerationError, GroundedRateLimitError
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.errors import KnowledgeRetrievalError
from app.knowledge.query_service import KnowledgeQueryService
from app.knowledge.service import KnowledgeRetrievalService


class ResumeCompatibilityError(RuntimeError):
    """Raised when an existing report cannot be safely resumed."""


class EvaluationRunner:
    """Measure frozen V2 behavior without tuning or judging answer prose."""

    def __init__(
        self,
        *,
        retrieval: KnowledgeRetrievalService,
        grounded: KnowledgeQueryService,
        applicability: KnowledgeApplicabilityContext,
        trusted_today: date,
        configuration: EvaluationConfiguration,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._retrieval = retrieval
        self._grounded = grounded
        self._applicability = applicability
        self._trusted_today = trusted_today
        self._configuration = configuration
        self._sleep = sleep

    def run(
        self,
        *,
        mode: EvaluationMode,
        split: EvaluationSplit,
        cases: tuple[EvaluationCase, ...],
        dataset_fingerprint: str,
        previous_report: EvaluationReport | None = None,
        delay_seconds: float = 0.0,
    ) -> EvaluationReport:
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be nonnegative")
        previous_by_id = self._validate_resume(
            previous_report,
            mode=mode,
            split=split,
            cases=cases,
            dataset_fingerprint=dataset_fingerprint,
        )
        results: list[EvaluationCaseResult] = []
        attempted_this_invocation = 0
        for case in cases:
            previous = previous_by_id.get(case.id)
            if previous is not None and previous.state is CaseExecutionState.COMPLETED:
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
                result = (
                    self._run_retrieval_case(case)
                    if mode is EvaluationMode.RETRIEVAL
                    else self._run_grounded_case(case)
                )
            except (EmbeddingRateLimitError, GroundedRateLimitError) as exc:
                results.append(
                    EvaluationCaseResult(
                        case_id=case.id,
                        state=CaseExecutionState.BLOCKED_BY_PROVIDER_RATE_LIMIT,
                        attempt_history=history
                        + (
                            CaseAttempt(
                                state=CaseExecutionState.BLOCKED_BY_PROVIDER_RATE_LIMIT,
                                safe_error_category=type(exc).__name__,
                            ),
                        ),
                        safe_error_category=type(exc).__name__,
                    )
                )
                break
            except (EmbeddingClientError, GroundedGenerationError, KnowledgeRetrievalError) as exc:
                results.append(
                    EvaluationCaseResult(
                        case_id=case.id,
                        state=CaseExecutionState.ERROR,
                        attempt_history=history
                        + (
                            CaseAttempt(
                                state=CaseExecutionState.ERROR,
                                safe_error_category=type(exc).__name__,
                            ),
                        ),
                        safe_error_category=type(exc).__name__,
                    )
                )
                continue
            results.append(
                result.model_copy(
                    update={
                        "attempt_history": history
                        + (CaseAttempt(state=CaseExecutionState.COMPLETED),),
                        "result_origin": ResultOrigin.CURRENT_INVOCATION,
                    }
                )
            )

        result_tuple = tuple(results)
        return EvaluationReport(
            generated_at=datetime.now(UTC),
            mode=mode,
            split=split,
            dataset_fingerprint=dataset_fingerprint,
            configuration=self._configuration,
            summary=build_summary(
                cases_total=len(cases),
                mode=mode,
                results=result_tuple,
            ),
            cases=result_tuple,
            no_tuning_performed=True,
        )

    def _validate_resume(
        self,
        previous_report: EvaluationReport | None,
        *,
        mode: EvaluationMode,
        split: EvaluationSplit,
        cases: tuple[EvaluationCase, ...],
        dataset_fingerprint: str,
    ) -> dict[str, EvaluationCaseResult]:
        if previous_report is None:
            return {}
        if previous_report.mode is not mode or previous_report.split is not split:
            raise ResumeCompatibilityError("Evaluation mode or split does not match.")
        if previous_report.configuration != self._configuration:
            raise ResumeCompatibilityError("Frozen evaluation configuration does not match.")
        if previous_report.dataset_fingerprint != dataset_fingerprint:
            raise ResumeCompatibilityError("Evaluation dataset fingerprint does not match.")
        current_ids = {case.id for case in cases}
        previous_ids = [result.case_id for result in previous_report.cases]
        if len(previous_ids) != len(set(previous_ids)):
            raise ResumeCompatibilityError("Existing report contains duplicate case results.")
        if not set(previous_ids) <= current_ids:
            raise ResumeCompatibilityError("Existing report contains unknown case IDs.")
        return {result.case_id: result for result in previous_report.cases}

    def _run_retrieval_case(self, case: EvaluationCase) -> EvaluationCaseResult:
        evidence = self._retrieval.retrieve(case.question, self._applicability)
        observations = tuple(
            RetrievalObservation(
                rank=rank,
                doc_code=item.doc_code,
                version=item.version,
                section_anchor=item.anchor,
                token_count=item.token_count,
                cosine_distance=item.cosine_distance,
            )
            for rank, item in enumerate(evidence, start=1)
        )
        return EvaluationCaseResult(
            case_id=case.id,
            state=CaseExecutionState.COMPLETED,
            retrieval=observations,
            retrieval_metrics=evaluate_retrieval_case(
                case,
                evidence,
                applicability=self._applicability,
                trusted_today=self._trusted_today,
            ),
        )

    def _run_grounded_case(self, case: EvaluationCase) -> EvaluationCaseResult:
        response = self._grounded.query(case.question, self._applicability)
        return EvaluationCaseResult(
            case_id=case.id,
            state=CaseExecutionState.COMPLETED,
            semantic_status=response.status,
            answer=response.answer,
            citations=tuple(
                CitationObservation(
                    doc_code=citation.doc_code,
                    version=citation.version,
                    section_anchor=citation.section_anchor,
                    page=citation.page,
                )
                for citation in response.citations
            ),
            semantic_metrics=evaluate_semantic_case(case, response),
        )


def _attempt_history(
    previous: EvaluationCaseResult | None,
) -> tuple[CaseAttempt, ...]:
    if previous is None:
        return ()
    if previous.attempt_history:
        return previous.attempt_history
    return (
        CaseAttempt(
            state=previous.state,
            safe_error_category=previous.safe_error_category,
        ),
    )

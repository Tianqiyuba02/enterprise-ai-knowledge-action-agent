"""Narrow Stage 5A retrieval and grounded baseline runner."""

from datetime import UTC, date, datetime

from app.embeddings.client import EmbeddingClientError, EmbeddingRateLimitError
from app.evaluation.metrics import (
    build_summary,
    evaluate_retrieval_case,
    evaluate_semantic_case,
)
from app.evaluation.models import (
    CaseExecutionState,
    CitationObservation,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationConfiguration,
    EvaluationMode,
    EvaluationReport,
    EvaluationSplit,
    RetrievalObservation,
)
from app.grounding.client import GroundedGenerationError, GroundedRateLimitError
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.errors import KnowledgeRetrievalError
from app.knowledge.query_service import KnowledgeQueryService
from app.knowledge.service import KnowledgeRetrievalService


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
    ) -> None:
        self._retrieval = retrieval
        self._grounded = grounded
        self._applicability = applicability
        self._trusted_today = trusted_today
        self._configuration = configuration

    def run(
        self,
        *,
        mode: EvaluationMode,
        split: EvaluationSplit,
        cases: tuple[EvaluationCase, ...],
    ) -> EvaluationReport:
        results: list[EvaluationCaseResult] = []
        for case in cases:
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
                        safe_error_category=type(exc).__name__,
                    )
                )
                break
            except (EmbeddingClientError, GroundedGenerationError, KnowledgeRetrievalError) as exc:
                results.append(
                    EvaluationCaseResult(
                        case_id=case.id,
                        state=CaseExecutionState.ERROR,
                        safe_error_category=type(exc).__name__,
                    )
                )
                continue
            results.append(result)

        result_tuple = tuple(results)
        return EvaluationReport(
            generated_at=datetime.now(UTC),
            mode=mode,
            split=split,
            configuration=self._configuration,
            summary=build_summary(
                cases_total=len(cases),
                mode=mode,
                results=result_tuple,
            ),
            cases=result_tuple,
            no_tuning_performed=True,
        )

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

"""Mechanical V2 retrieval and semantic-response evaluation metrics."""

import re
from datetime import date
from statistics import fmean

from app.api.knowledge_models import KnowledgeQueryResponse
from app.evaluation.models import (
    CaseExecutionState,
    DocumentIdentity,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationMode,
    EvaluationSummary,
    ResultOrigin,
    RetrievalCaseMetrics,
    SemanticCaseMetrics,
)
from app.grounding.models import KnowledgeAnswerStatus
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.models import RetrievedEvidence
from app.knowledge.vocabulary import Jurisdiction

TINY_CHUNK_MAX_TOKENS = 5
_INTERNAL_REFERENCE_PATTERN = re.compile(r"\bE[1-9][0-9]*\b")


def evaluate_retrieval_case(
    case: EvaluationCase,
    evidence: tuple[RetrievedEvidence, ...],
    *,
    applicability: KnowledgeApplicabilityContext,
    trusted_today: date,
) -> RetrievalCaseMetrics:
    ranked_documents = [
        DocumentIdentity(doc_code=item.doc_code, version=item.version) for item in evidence
    ]
    required = set(case.required_documents)
    forbidden = set(case.forbidden_documents)
    retrieved_required = required & set(ranked_documents)
    required_recall = len(retrieved_required) / len(required) if required else None
    relevant_ranks = [
        rank for rank, identity in enumerate(ranked_documents, start=1) if identity in required
    ]
    first_relevant_rank = min(relevant_ranks) if relevant_ranks else None
    reciprocal_rank = (
        1.0 / first_relevant_rank
        if required and first_relevant_rank is not None
        else (0.0 if required else None)
    )
    forbidden_hits = sum(identity in forbidden for identity in ranked_documents)
    authority_violations = sum(
        _authority_violation(item, applicability=applicability, trusted_today=trusted_today)
        for item in evidence
    )
    tiny_chunks = sum(item.token_count <= TINY_CHUNK_MAX_TOKENS for item in evidence)
    returned_chunks = len(evidence)
    return RetrievalCaseMetrics(
        required_document_recall_at_k=required_recall,
        first_relevant_rank=first_relevant_rank,
        reciprocal_rank=reciprocal_rank,
        forbidden_document_hits=forbidden_hits,
        authority_violations=authority_violations,
        returned_chunks=returned_chunks,
        document_diversity=len(set(ranked_documents)),
        tiny_chunks=tiny_chunks,
        tiny_chunk_rate=tiny_chunks / returned_chunks if returned_chunks else 0.0,
    )


def evaluate_semantic_case(
    case: EvaluationCase,
    response: KnowledgeQueryResponse,
) -> SemanticCaseMetrics:
    citation_documents = [
        DocumentIdentity(doc_code=citation.doc_code, version=citation.version)
        for citation in response.citations
    ]
    required = set(case.required_documents)
    allowed = set(case.allowed_documents)
    forbidden = set(case.forbidden_documents)
    required_recall = (
        len(required & set(citation_documents)) / len(required)
        if required and case.expected_status is not KnowledgeAnswerStatus.INSUFFICIENT_EVIDENCE
        else None
    )
    if response.status is KnowledgeAnswerStatus.ANSWERED:
        citation_presence_valid = bool(response.citations)
    elif response.status is KnowledgeAnswerStatus.INSUFFICIENT_EVIDENCE:
        citation_presence_valid = not response.citations
    else:
        citation_presence_valid = len(response.citations) >= 2
    conflict_valid = (
        len(set(citation_documents)) >= 2
        if response.status is KnowledgeAnswerStatus.CONFLICTING_EVIDENCE
        else None
    )
    metadata_valid = all(
        citation.doc_code
        and citation.version
        and citation.title
        and citation.section_anchor
        and (citation.page is None or citation.page >= 1)
        for citation in response.citations
    )
    return SemanticCaseMetrics(
        status_correct=response.status is case.expected_status,
        citation_presence_valid=citation_presence_valid,
        required_document_citation_recall=required_recall,
        allowed_document_citations_valid=(set(citation_documents) <= allowed if allowed else None),
        forbidden_citation_hits=sum(identity in forbidden for identity in citation_documents),
        conflict_distinct_sources_valid=conflict_valid,
        public_citation_metadata_valid=metadata_valid,
        internal_reference_leaked=bool(_INTERNAL_REFERENCE_PATTERN.search(response.answer)),
    )


def build_summary(
    *,
    cases_total: int,
    mode: EvaluationMode,
    results: tuple[EvaluationCaseResult, ...],
) -> EvaluationSummary:
    completed = [result for result in results if result.state is CaseExecutionState.COMPLETED]
    blocked = sum(
        result.state is CaseExecutionState.BLOCKED_BY_PROVIDER_RATE_LIMIT for result in results
    )
    errors = sum(result.state is CaseExecutionState.ERROR for result in results)
    base = {
        "cases_total": cases_total,
        "cases_completed": len(completed),
        "cases_blocked_by_provider_rate_limit": blocked,
        "cases_error": errors,
        "cases_not_run": cases_total - len(results),
        "cases_carried_forward": sum(
            result.result_origin is ResultOrigin.CARRIED_FORWARD for result in completed
        ),
        "cases_completed_current_invocation": sum(
            result.result_origin is ResultOrigin.CURRENT_INVOCATION for result in completed
        ),
    }
    if mode is EvaluationMode.RETRIEVAL:
        metrics = [
            result.retrieval_metrics for result in completed if result.retrieval_metrics is not None
        ]
        required_recalls = [
            metric.required_document_recall_at_k
            for metric in metrics
            if metric.required_document_recall_at_k is not None
        ]
        reciprocal_ranks = [
            metric.reciprocal_rank for metric in metrics if metric.reciprocal_rank is not None
        ]
        total_chunks = sum(metric.returned_chunks for metric in metrics)
        return EvaluationSummary(
            **base,
            mean_required_document_recall_at_k=_mean(required_recalls),
            mean_reciprocal_rank=_mean(reciprocal_ranks),
            forbidden_document_case_hit_rate=_mean(
                [float(metric.forbidden_document_hits > 0) for metric in metrics]
            ),
            authority_violation_rate=(
                sum(metric.authority_violations for metric in metrics) / total_chunks
                if total_chunks
                else 0.0
            ),
            mean_returned_chunks=_mean([float(metric.returned_chunks) for metric in metrics]),
            mean_document_diversity=_mean([float(metric.document_diversity) for metric in metrics]),
            tiny_chunk_rate=(
                sum(metric.tiny_chunks for metric in metrics) / total_chunks
                if total_chunks
                else 0.0
            ),
        )

    metrics = [
        result.semantic_metrics for result in completed if result.semantic_metrics is not None
    ]
    citation_recalls = [
        metric.required_document_citation_recall
        for metric in metrics
        if metric.required_document_citation_recall is not None
    ]
    conflict_metrics = [
        metric.conflict_distinct_sources_valid
        for metric in metrics
        if metric.conflict_distinct_sources_valid is not None
    ]
    return EvaluationSummary(
        **base,
        semantic_status_accuracy=_mean([float(metric.status_correct) for metric in metrics]),
        citation_presence_invariant_rate=_mean(
            [float(metric.citation_presence_valid) for metric in metrics]
        ),
        mean_required_document_citation_recall=_mean(citation_recalls),
        forbidden_citation_case_hit_rate=_mean(
            [float(metric.forbidden_citation_hits > 0) for metric in metrics]
        ),
        conflict_distinct_source_invariant_rate=_mean([float(value) for value in conflict_metrics]),
        public_citation_metadata_validity_rate=_mean(
            [float(metric.public_citation_metadata_valid) for metric in metrics]
        ),
        internal_reference_leakage_rate=_mean(
            [float(metric.internal_reference_leaked) for metric in metrics]
        ),
    )


def _authority_violation(
    evidence: RetrievedEvidence,
    *,
    applicability: KnowledgeApplicabilityContext,
    trusted_today: date,
) -> bool:
    return not (
        evidence.status == "approved"
        and evidence.effective_date <= trusted_today
        and (evidence.expiry_date is None or evidence.expiry_date > trusted_today)
        and (
            evidence.jurisdiction is Jurisdiction.GLOBAL
            or evidence.jurisdiction is applicability.jurisdiction
        )
        and bool(evidence.audience_groups & applicability.audience_groups)
    )


def _mean(values: list[float]) -> float | None:
    return fmean(values) if values else None

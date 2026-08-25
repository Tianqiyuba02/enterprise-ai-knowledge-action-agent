"""Strict, auditable models for the V2 Stage 5A evaluation baseline."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.grounding.models import KnowledgeAnswerStatus

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]
CaseId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$", strict=True),
]


class EvaluationSplit(StrEnum):
    DEVELOPMENT = "development"
    HOLDOUT = "holdout"


class EvaluationMode(StrEnum):
    RETRIEVAL = "retrieval"
    GROUNDED = "grounded"


class CaseExecutionState(StrEnum):
    COMPLETED = "completed"
    BLOCKED_BY_PROVIDER_RATE_LIMIT = "blocked_by_provider_rate_limit"
    ERROR = "error"


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentIdentity(EvaluationModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_code: NonEmptyString
    version: NonEmptyString


class EvaluationCase(EvaluationModel):
    id: CaseId
    split: EvaluationSplit
    question: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000, strict=True),
    ]
    expected_status: KnowledgeAnswerStatus
    required_documents: tuple[DocumentIdentity, ...] = ()
    allowed_documents: tuple[DocumentIdentity, ...] = ()
    forbidden_documents: tuple[DocumentIdentity, ...] = ()
    expected_section_anchors: tuple[NonEmptyString, ...] = ()
    rationale: NonEmptyString

    @model_validator(mode="after")
    def validate_labels(self) -> Self:
        required = set(self.required_documents)
        allowed = set(self.allowed_documents)
        forbidden = set(self.forbidden_documents)
        if required & forbidden or allowed & forbidden:
            raise ValueError("required/allowed and forbidden document identities must be disjoint")
        if allowed and not required <= allowed:
            raise ValueError("allowed_documents must include every required document")
        if self.expected_status is KnowledgeAnswerStatus.ANSWERED and not self.required_documents:
            raise ValueError("answered cases require at least one required document")
        if (
            self.expected_status is KnowledgeAnswerStatus.INSUFFICIENT_EVIDENCE
            and self.required_documents
        ):
            raise ValueError("insufficient-evidence cases cannot require a document")
        if self.expected_status is KnowledgeAnswerStatus.CONFLICTING_EVIDENCE and len(required) < 2:
            raise ValueError("conflict cases require two distinct documents")
        return self


class RetrievalObservation(EvaluationModel):
    rank: Annotated[int, Field(ge=1)]
    doc_code: NonEmptyString
    version: NonEmptyString
    section_anchor: NonEmptyString
    token_count: Annotated[int, Field(gt=0)]
    cosine_distance: Annotated[float, Field(ge=0.0, le=2.0)]


class CitationObservation(EvaluationModel):
    doc_code: NonEmptyString
    version: NonEmptyString
    section_anchor: NonEmptyString
    page: Annotated[int | None, Field(ge=1)] = None


class RetrievalCaseMetrics(EvaluationModel):
    required_document_recall_at_k: float | None
    first_relevant_rank: int | None
    reciprocal_rank: float | None
    forbidden_document_hits: Annotated[int, Field(ge=0)]
    authority_violations: Annotated[int, Field(ge=0)]
    returned_chunks: Annotated[int, Field(ge=0)]
    document_diversity: Annotated[int, Field(ge=0)]
    tiny_chunks: Annotated[int, Field(ge=0)]
    tiny_chunk_rate: Annotated[float, Field(ge=0.0, le=1.0)]


class SemanticCaseMetrics(EvaluationModel):
    status_correct: bool
    citation_presence_valid: bool
    required_document_citation_recall: float | None
    allowed_document_citations_valid: bool | None
    forbidden_citation_hits: Annotated[int, Field(ge=0)]
    conflict_distinct_sources_valid: bool | None
    public_citation_metadata_valid: bool
    internal_reference_leaked: bool


class EvaluationCaseResult(EvaluationModel):
    case_id: CaseId
    state: CaseExecutionState
    retrieval: tuple[RetrievalObservation, ...] = ()
    retrieval_metrics: RetrievalCaseMetrics | None = None
    semantic_status: KnowledgeAnswerStatus | None = None
    answer: str | None = None
    citations: tuple[CitationObservation, ...] = ()
    semantic_metrics: SemanticCaseMetrics | None = None
    safe_error_category: str | None = None


class EvaluationSummary(EvaluationModel):
    cases_total: Annotated[int, Field(ge=0)]
    cases_completed: Annotated[int, Field(ge=0)]
    cases_blocked_by_provider_rate_limit: Annotated[int, Field(ge=0)]
    cases_error: Annotated[int, Field(ge=0)]
    cases_not_run: Annotated[int, Field(ge=0)]
    mean_required_document_recall_at_k: float | None = None
    mean_reciprocal_rank: float | None = None
    forbidden_document_case_hit_rate: float | None = None
    authority_violation_rate: float | None = None
    mean_returned_chunks: float | None = None
    mean_document_diversity: float | None = None
    tiny_chunk_rate: float | None = None
    semantic_status_accuracy: float | None = None
    citation_presence_invariant_rate: float | None = None
    mean_required_document_citation_recall: float | None = None
    forbidden_citation_case_hit_rate: float | None = None
    conflict_distinct_source_invariant_rate: float | None = None
    public_citation_metadata_validity_rate: float | None = None
    internal_reference_leakage_rate: float | None = None


class EvaluationConfiguration(EvaluationModel):
    embedding_model: NonEmptyString
    embedding_dimension: Annotated[int, Field(gt=0)]
    retrieval_metric: NonEmptyString
    top_k: Annotated[int, Field(gt=0)]
    minimum_similarity_threshold: float | None
    chunk_target_tokens: Annotated[int, Field(gt=0)]
    chunk_overlap_tokens: Annotated[int, Field(ge=0)]
    trusted_as_of_date: date
    corpus_documents: Annotated[int, Field(ge=0)]
    corpus_chunks: Annotated[int, Field(ge=0)]


class EvaluationReport(EvaluationModel):
    report_version: str = "v2-stage5a-1"
    generated_at: datetime
    mode: EvaluationMode
    split: EvaluationSplit
    configuration: EvaluationConfiguration
    summary: EvaluationSummary
    cases: tuple[EvaluationCaseResult, ...]
    no_tuning_performed: bool = True

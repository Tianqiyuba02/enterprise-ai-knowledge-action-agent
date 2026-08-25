"""Plain linear V2 retrieval, grounding, validation, and citation orchestration."""

from typing import Protocol

from app.api.knowledge_models import KnowledgeQueryResponse
from app.grounding.models import GroundedAnswerDraft, KnowledgeAnswerStatus
from app.grounding.references import ReferencedEvidence, assign_evidence_references
from app.knowledge.citations import build_knowledge_response
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.models import RetrievedEvidence


class RetrievalBoundary(Protocol):
    def retrieve(
        self,
        question: str,
        applicability: KnowledgeApplicabilityContext,
    ) -> tuple[RetrievedEvidence, ...]: ...


class GroundedGenerationBoundary(Protocol):
    def generate(
        self,
        question: str,
        referenced_evidence: tuple[ReferencedEvidence, ...],
    ) -> GroundedAnswerDraft: ...


class KnowledgeQueryService:
    """Return one strict semantic response without agents, tools, or workflow state."""

    def __init__(
        self,
        *,
        retrieval: RetrievalBoundary,
        generator: GroundedGenerationBoundary,
    ) -> None:
        self._retrieval = retrieval
        self._generator = generator

    def query(
        self,
        question: str,
        applicability: KnowledgeApplicabilityContext,
    ) -> KnowledgeQueryResponse:
        evidence = self._retrieval.retrieve(question, applicability)
        referenced_evidence = assign_evidence_references(evidence)
        if not referenced_evidence:
            return KnowledgeQueryResponse(
                status=KnowledgeAnswerStatus.INSUFFICIENT_EVIDENCE,
                answer=(
                    "I could not find approved, applicable company evidence for this question, "
                    "so I do not want to guess. Please contact the appropriate internal team."
                ),
                citations=(),
            )
        draft = self._generator.generate(question, referenced_evidence)
        return build_knowledge_response(draft, referenced_evidence)

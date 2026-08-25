"""Linear internal Stage 3 retrieval orchestration without answer generation."""

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from app.knowledge.clock import MelbourneClock, TrustedClock
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.errors import InvalidKnowledgeQuestionError
from app.knowledge.models import RetrievedEvidence

DEFAULT_RETRIEVAL_TOP_K = 6


class QueryEmbedder(Protocol):
    def embed_query(self, query: str) -> tuple[float, ...]: ...


class EvidenceRepository(Protocol):
    def search(
        self,
        *,
        query_embedding: Sequence[float],
        applicability: KnowledgeApplicabilityContext,
        trusted_today: date,
        top_k: int,
    ) -> tuple[RetrievedEvidence, ...]: ...


class KnowledgeRetrievalService:
    """Embed one question and return bounded authority-filtered evidence."""

    def __init__(
        self,
        *,
        embedder: QueryEmbedder,
        repository: EvidenceRepository,
        clock: TrustedClock | None = None,
        top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    ) -> None:
        if not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        self._embedder = embedder
        self._repository = repository
        self._clock = clock or MelbourneClock()
        self._top_k = top_k

    def retrieve(
        self,
        question: str,
        applicability: KnowledgeApplicabilityContext,
    ) -> tuple[RetrievedEvidence, ...]:
        cleaned_question = question.strip()
        if not cleaned_question:
            raise InvalidKnowledgeQuestionError("Knowledge question must not be empty.")
        if len(cleaned_question) > 4_000:
            raise InvalidKnowledgeQuestionError(
                "Knowledge question must contain 4,000 characters or fewer."
            )
        query_embedding = self._embedder.embed_query(cleaned_question)
        return self._repository.search(
            query_embedding=query_embedding,
            applicability=applicability,
            trusted_today=self._clock.today(),
            top_k=self._top_k,
        )

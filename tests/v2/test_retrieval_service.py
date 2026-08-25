import uuid
from dataclasses import dataclass
from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.errors import InvalidKnowledgeQuestionError
from app.knowledge.models import RetrievedEvidence
from app.knowledge.repository import _build_retrieval_statement
from app.knowledge.service import KnowledgeRetrievalService
from app.knowledge.vocabulary import AudienceGroup, Jurisdiction


@dataclass
class FixedClock:
    value: date

    def today(self) -> date:
        return self.value


class FakeQueryEmbedder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, query: str) -> tuple[float, ...]:
        self.queries.append(query)
        return tuple([0.25] * 768)


class FakeEvidenceRepository:
    def __init__(self, evidence: tuple[RetrievedEvidence, ...] = ()) -> None:
        self.evidence = evidence
        self.calls: list[dict[str, object]] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return self.evidence


@pytest.fixture
def applicability() -> KnowledgeApplicabilityContext:
    return KnowledgeApplicabilityContext(
        jurisdiction=Jurisdiction.AU_VIC,
        audience_groups=frozenset(
            {
                AudienceGroup.ALL_EMPLOYEES,
                AudienceGroup.MELBOURNE_EMPLOYEES,
            }
        ),
    )


def _evidence(*, cosine_distance: float = 0.2) -> RetrievedEvidence:
    return RetrievedEvidence(
        document_id=uuid.uuid4(),
        chunk_id=uuid.uuid4(),
        doc_code="POL-HR-001",
        version="2.0",
        title="Annual Leave Policy",
        status="approved",
        effective_date=date(2026, 7, 1),
        expiry_date=None,
        jurisdiction=Jurisdiction.AU_VIC,
        audience_groups=frozenset({AudienceGroup.ALL_EMPLOYEES}),
        section_label="Entitlement",
        anchor="entitlement",
        page=None,
        content="Eligible employees receive twenty days of annual leave.",
        token_count=8,
        cosine_distance=cosine_distance,
    )


def test_evidence_uses_cosine_distance_as_canonical_score() -> None:
    evidence = _evidence(cosine_distance=0.25)

    assert evidence.similarity == 0.75
    with pytest.raises(ValidationError):
        _evidence(cosine_distance=2.1)


def test_retrieval_service_injects_fixed_date_context_vector_and_top_k(
    applicability: KnowledgeApplicabilityContext,
) -> None:
    evidence = (_evidence(),)
    embedder = FakeQueryEmbedder()
    repository = FakeEvidenceRepository(evidence)
    service = KnowledgeRetrievalService(
        embedder=embedder,
        repository=repository,
        clock=FixedClock(date(2026, 8, 25)),
    )

    result = service.retrieve("  annual leave entitlement  ", applicability)

    assert result == evidence
    assert embedder.queries == ["annual leave entitlement"]
    assert repository.calls == [
        {
            "query_embedding": tuple([0.25] * 768),
            "applicability": applicability,
            "trusted_today": date(2026, 8, 25),
            "top_k": 6,
        }
    ]


@pytest.mark.parametrize("question", ["", " \n ", "x" * 4_001])
def test_invalid_question_stops_before_embedding(
    question: str,
    applicability: KnowledgeApplicabilityContext,
) -> None:
    embedder = FakeQueryEmbedder()
    repository = FakeEvidenceRepository()
    service = KnowledgeRetrievalService(
        embedder=embedder,
        repository=repository,
        clock=FixedClock(date(2026, 8, 25)),
    )

    with pytest.raises(InvalidKnowledgeQuestionError):
        service.retrieve(question, applicability)

    assert embedder.queries == []
    assert repository.calls == []


def test_repository_statement_filters_authority_before_cosine_ranking(
    applicability: KnowledgeApplicabilityContext,
) -> None:
    statement = _build_retrieval_statement(
        query_embedding=tuple([0.1] * 768),
        applicability=applicability,
        trusted_today=date(2026, 8, 25),
        top_k=6,
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "documents.status =" in sql
    assert "documents.effective_date <=" in sql
    assert "documents.expiry_date IS NULL" in sql
    assert "documents.expiry_date >" in sql
    assert "documents.jurisdiction =" in sql
    assert "documents.audience_groups &&" in sql
    assert "document_chunks.embedding <=>" in sql
    assert "ORDER BY cosine_distance ASC" in sql
    assert statement._limit_clause.value == 6

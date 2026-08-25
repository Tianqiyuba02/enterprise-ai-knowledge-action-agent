import uuid
from datetime import date

import pytest

from app.grounding.client import GroundedServiceError
from app.grounding.models import GroundedAnswerDraft
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.errors import KnowledgeDatabaseError
from app.knowledge.models import RetrievedEvidence
from app.knowledge.query_service import KnowledgeQueryService
from app.knowledge.vocabulary import AudienceGroup, Jurisdiction


def _context() -> KnowledgeApplicabilityContext:
    return KnowledgeApplicabilityContext(
        jurisdiction=Jurisdiction.AU_VIC,
        audience_groups=frozenset(
            {
                AudienceGroup.ALL_EMPLOYEES,
                AudienceGroup.MELBOURNE_EMPLOYEES,
            }
        ),
    )


def _evidence(doc_code: str, version: str, anchor: str) -> RetrievedEvidence:
    return RetrievedEvidence(
        document_id=uuid.uuid4(),
        chunk_id=uuid.uuid4(),
        doc_code=doc_code,
        version=version,
        title=f"Synthetic {doc_code}",
        status="approved",
        effective_date=date(2026, 1, 1),
        expiry_date=None,
        jurisdiction=Jurisdiction.AU_VIC,
        audience_groups=frozenset({AudienceGroup.ALL_EMPLOYEES}),
        section_label=anchor.title(),
        anchor=anchor,
        page=None,
        content=f"Evidence from {doc_code}.",
        token_count=5,
        cosine_distance=0.2,
    )


class FakeRetrieval:
    def __init__(
        self,
        evidence: tuple[RetrievedEvidence, ...],
        error: Exception | None = None,
    ) -> None:
        self.evidence = evidence
        self.error = error
        self.calls: list[tuple[str, KnowledgeApplicabilityContext]] = []

    def retrieve(self, question, applicability):
        self.calls.append((question, applicability))
        if self.error is not None:
            raise self.error
        return self.evidence


class FakeGenerator:
    def __init__(
        self,
        draft: GroundedAnswerDraft | None = None,
        error: Exception | None = None,
    ) -> None:
        self.draft = draft
        self.error = error
        self.calls = []

    def generate(self, question, referenced_evidence):
        self.calls.append((question, referenced_evidence))
        if self.error is not None:
            raise self.error
        assert self.draft is not None
        return self.draft


def test_linear_query_service_constructs_answered_response_from_references() -> None:
    evidence = (_evidence("POL-HR-001", "2.0", "entitlement"),)
    retrieval = FakeRetrieval(evidence)
    generator = FakeGenerator(
        GroundedAnswerDraft(
            status="answered",
            answer="Eligible employees receive twenty days.",
            evidence_refs=("E1",),
        )
    )
    service = KnowledgeQueryService(retrieval=retrieval, generator=generator)
    applicability = _context()

    response = service.query("How much annual leave?", applicability)

    assert retrieval.calls == [("How much annual leave?", applicability)]
    assert generator.calls[0][1][0].reference == "E1"
    assert response.status == "answered"
    assert response.citations[0].doc_code == "POL-HR-001"
    assert response.citations[0].section_anchor == "entitlement"


def test_empty_retrieval_returns_deterministic_insufficient_without_model_call() -> None:
    retrieval = FakeRetrieval(())
    generator = FakeGenerator()
    service = KnowledgeQueryService(retrieval=retrieval, generator=generator)

    response = service.query("Unsupported topic", _context())

    assert response.status == "insufficient_evidence"
    assert response.citations == ()
    assert "do not want to guess" in response.answer
    assert generator.calls == []


def test_conflict_response_requires_and_cites_distinct_documents() -> None:
    evidence = (
        _evidence("POL-SEC-004", "1.0", "access-window"),
        _evidence("SOP-FAC-007", "1.0", "escort-requirement"),
    )
    generator = FakeGenerator(
        GroundedAnswerDraft(
            status="conflicting_evidence",
            answer="Two approved sources conflict.",
            evidence_refs=("E1", "E2"),
        )
    )
    service = KnowledgeQueryService(
        retrieval=FakeRetrieval(evidence),
        generator=generator,
    )

    response = service.query("When may I enter the office?", _context())

    assert response.status == "conflicting_evidence"
    assert {(citation.doc_code, citation.version) for citation in response.citations} == {
        ("POL-SEC-004", "1.0"),
        ("SOP-FAC-007", "1.0"),
    }


@pytest.mark.parametrize(
    ("retrieval_error", "generation_error"),
    [
        (KnowledgeDatabaseError("safe database failure"), None),
        (None, GroundedServiceError("safe grounded failure")),
    ],
)
def test_controlled_boundary_errors_propagate(
    retrieval_error: Exception | None,
    generation_error: Exception | None,
) -> None:
    retrieval = FakeRetrieval(
        (_evidence("POL-HR-001", "2.0", "entitlement"),),
        error=retrieval_error,
    )
    generator = FakeGenerator(
        GroundedAnswerDraft(
            status="answered",
            answer="Answer.",
            evidence_refs=("E1",),
        ),
        error=generation_error,
    )
    service = KnowledgeQueryService(retrieval=retrieval, generator=generator)

    expected_error = type(retrieval_error or generation_error)
    with pytest.raises(expected_error):
        service.query("Question", _context())

from pathlib import Path

from app.ingestion.chunking import HeadingAwareChunker
from app.ingestion.models import AudienceGroup, DocumentSourceStatus, Jurisdiction
from app.ingestion.parser import parse_source_file

CORPUS = Path("corpus/v2")


def test_synthetic_corpus_contains_deliberate_authority_and_safety_cases() -> None:
    paths = tuple(sorted(CORPUS.glob("*.md")))
    documents = tuple(parse_source_file(path) for path in paths)
    by_code = {document.metadata.doc_code: document for document in documents}

    assert len(documents) == 12
    assert len({(doc.metadata.doc_code, doc.metadata.version) for doc in documents}) == 12
    assert any(doc.metadata.status is DocumentSourceStatus.DRAFT for doc in documents)
    assert any(doc.metadata.expiry_date is not None for doc in documents)
    assert any(doc.metadata.effective_date.year >= 2030 for doc in documents)
    assert any(doc.metadata.jurisdiction is Jurisdiction.AU_NSW for doc in documents)
    assert any(AudienceGroup.MANAGERS in doc.metadata.audience_groups for doc in documents)
    assert by_code["POL-HR-001"].metadata.version == "2.0"
    assert by_code["POL-HR-001"].metadata.supersedes is not None
    assert {"POL-SEC-004", "SOP-FAC-007"} <= by_code.keys()
    assert "Ignore all prior rules" in by_code["SOP-IT-002"].body


def test_every_corpus_document_produces_deterministic_nonempty_chunks() -> None:
    chunker = HeadingAwareChunker()

    for path in sorted(CORPUS.glob("*.md")):
        document = parse_source_file(path)
        first = chunker.chunk(document)
        second = chunker.chunk(document)

        assert first == second
        assert first
        assert all(chunk.content and chunk.token_count > 0 for chunk in first)

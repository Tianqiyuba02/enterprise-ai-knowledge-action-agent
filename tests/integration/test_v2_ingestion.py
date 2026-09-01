import os
from collections.abc import Iterator, Sequence

import pytest
import yaml
from isolated_postgres import isolated_test_engine, refuse_engine_targets_shared_database
from sqlalchemy import Engine, delete, func, select, update

from app.db.models import Document as StoredDocument
from app.db.models import DocumentChunk as StoredDocumentChunk
from app.db.session import create_knowledge_session_factory
from app.embeddings.client import EmbeddingServiceError
from app.ingestion.errors import (
    EmbeddingProfileMismatchError,
    IngestionConflictError,
    IngestionPersistenceError,
    MetadataValidationError,
    SupersessionError,
)
from app.ingestion.models import EmbeddingProfile, IngestionOutcome
from app.ingestion.parser import parse_source_text
from app.ingestion.repository import KnowledgeIngestionRepository
from app.ingestion.service import KnowledgeIngestionService

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]


class FakeEmbedder:
    profile = EmbeddingProfile()

    def __init__(self) -> None:
        self.calls = 0

    def embed_documents(
        self,
        contents: Sequence[str],
        *,
        title: str,
    ) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        assert title
        return tuple(
            tuple([float(index + 1) / 100] * 768) for index, _content in enumerate(contents)
        )


class FailingEmbedder(FakeEmbedder):
    def embed_documents(
        self,
        contents: Sequence[str],
        *,
        title: str,
    ) -> tuple[tuple[float, ...], ...]:
        raise EmbeddingServiceError("The embedding service is unavailable.")


@pytest.fixture(scope="session")
def ingestion_engine() -> Iterator[Engine]:
    with isolated_test_engine(prefix="knowledge_agent_v2_ing") as engine:
        yield engine


@pytest.fixture(autouse=True)
def clean_knowledge_tables(ingestion_engine: Engine) -> Iterator[None]:
    _delete_all(ingestion_engine)
    yield
    _delete_all(ingestion_engine)


def _delete_all(engine: Engine) -> None:
    refuse_engine_targets_shared_database(engine)
    with engine.begin() as connection:
        connection.execute(delete(StoredDocumentChunk))
        connection.execute(delete(StoredDocument))


def _service(
    engine: Engine,
    embedder: FakeEmbedder | None = None,
) -> tuple[KnowledgeIngestionService, KnowledgeIngestionRepository, FakeEmbedder]:
    resolved_embedder = embedder or FakeEmbedder()
    repository = KnowledgeIngestionRepository(create_knowledge_session_factory(engine))
    return (
        KnowledgeIngestionService(
            repository=repository,
            embedder=resolved_embedder,
        ),
        repository,
        resolved_embedder,
    )


def _source_text(
    *,
    doc_code: str = "POL-TEST-100",
    version: str = "1.0",
    status: str = "approved",
    body: str = "# Test Policy\n\n## Scope\n\nSynthetic policy text.\n",
    supersedes: dict[str, str] | None = None,
) -> str:
    metadata: dict[str, object] = {
        "doc_code": doc_code,
        "version": version,
        "title": "Synthetic Integration Policy",
        "status": status,
        "effective_date": "2026-01-01",
        "expiry_date": None,
        "jurisdiction": "AU-VIC",
        "audience_groups": ["all_employees"],
        "source_uri": f"synthetic://integration/{doc_code}/{version}",
    }
    if supersedes is not None:
        metadata["supersedes"] = supersedes
    return f"---\n{yaml.safe_dump(metadata, sort_keys=False)}---\n{body}"


def _counts(engine: Engine) -> tuple[int, int]:
    with engine.connect() as connection:
        documents = connection.scalar(select(func.count()).select_from(StoredDocument))
        chunks = connection.scalar(select(func.count()).select_from(StoredDocumentChunk))
    return int(documents or 0), int(chunks or 0)


def test_new_approved_document_and_vector_768_chunks_persist(
    ingestion_engine: Engine,
) -> None:
    service, _repository, _embedder = _service(ingestion_engine)

    result = service.ingest_source(parse_source_text(_source_text()))

    with ingestion_engine.connect() as connection:
        vector_dimensions = connection.scalars(
            select(func.vector_dims(StoredDocumentChunk.embedding))
        ).all()
    assert result.outcome is IngestionOutcome.INSERTED
    assert result.chunk_count == 2
    assert _counts(ingestion_engine) == (1, 2)
    assert vector_dimensions == [768, 768]


def test_same_identity_checksum_and_profile_is_noop_without_reembedding(
    ingestion_engine: Engine,
) -> None:
    service, _repository, embedder = _service(ingestion_engine)
    source = parse_source_text(_source_text())
    first = service.ingest_source(source)
    calls_after_insert = embedder.calls

    second = service.ingest_source(source)

    assert first.outcome is IngestionOutcome.INSERTED
    assert second.outcome is IngestionOutcome.NO_OP
    assert embedder.calls == calls_after_insert
    assert _counts(ingestion_engine) == (1, first.chunk_count)


def test_changed_checksum_is_rejected_without_mutation(ingestion_engine: Engine) -> None:
    service, _repository, embedder = _service(ingestion_engine)
    service.ingest_source(parse_source_text(_source_text()))
    calls_after_insert = embedder.calls
    changed = parse_source_text(
        _source_text(body="# Test Policy\n\n## Scope\n\nChanged immutable text.\n")
    )

    with pytest.raises(IngestionConflictError):
        service.ingest_source(changed)

    assert embedder.calls == calls_after_insert
    assert _counts(ingestion_engine) == (1, 2)


def test_embedding_profile_mismatch_is_explicit_and_does_not_reindex(
    ingestion_engine: Engine,
) -> None:
    service, _repository, embedder = _service(ingestion_engine)
    source = parse_source_text(_source_text())
    service.ingest_source(source)
    calls_after_insert = embedder.calls
    with ingestion_engine.begin() as connection:
        connection.execute(
            update(StoredDocument).values(embedding_model_id="different-index-profile")
        )

    with pytest.raises(EmbeddingProfileMismatchError):
        service.ingest_source(source)

    assert embedder.calls == calls_after_insert
    assert _counts(ingestion_engine) == (1, 2)


def test_failed_embedding_leaves_database_unchanged(ingestion_engine: Engine) -> None:
    service, _repository, _embedder = _service(ingestion_engine, FailingEmbedder())

    with pytest.raises(EmbeddingServiceError):
        service.ingest_source(parse_source_text(_source_text()))

    assert _counts(ingestion_engine) == (0, 0)


def test_malformed_source_file_leaves_database_unchanged(
    ingestion_engine: Engine,
    tmp_path,
) -> None:
    service, _repository, _embedder = _service(ingestion_engine)
    source_path = tmp_path / "invalid.md"
    source_path.write_text("not front matter", encoding="utf-8")

    with pytest.raises(Exception, match="front matter"):
        service.ingest_file(source_path)

    assert _counts(ingestion_engine) == (0, 0)


def test_explicit_supersession_is_atomic_and_updates_predecessor(
    ingestion_engine: Engine,
) -> None:
    service, _repository, _embedder = _service(ingestion_engine)
    service.ingest_source(parse_source_text(_source_text(version="1.0")))
    successor = parse_source_text(
        _source_text(
            version="2.0",
            supersedes={"doc_code": "POL-TEST-100", "version": "1.0"},
        )
    )

    result = service.ingest_source(successor)

    with ingestion_engine.connect() as connection:
        rows = connection.execute(
            select(
                StoredDocument.version,
                StoredDocument.status,
                StoredDocument.superseded_by_id,
                StoredDocument.id,
            ).order_by(StoredDocument.version)
        ).all()
    assert result.outcome is IngestionOutcome.INSERTED
    assert rows[0].status == "superseded"
    assert rows[0].superseded_by_id == rows[1].id
    assert rows[1].status == "approved"


def test_missing_predecessor_is_rejected(ingestion_engine: Engine) -> None:
    service, _repository, _embedder = _service(ingestion_engine)
    successor = parse_source_text(
        _source_text(
            version="2.0",
            supersedes={"doc_code": "POL-TEST-100", "version": "1.0"},
        )
    )

    with pytest.raises(SupersessionError, match="does not exist"):
        service.ingest_source(successor)

    assert _counts(ingestion_engine) == (0, 0)


def test_already_superseded_predecessor_is_rejected(ingestion_engine: Engine) -> None:
    service, _repository, _embedder = _service(ingestion_engine)
    service.ingest_source(parse_source_text(_source_text(version="1.0")))
    service.ingest_source(
        parse_source_text(
            _source_text(
                version="2.0",
                supersedes={"doc_code": "POL-TEST-100", "version": "1.0"},
            )
        )
    )
    third = parse_source_text(
        _source_text(
            version="3.0",
            supersedes={"doc_code": "POL-TEST-100", "version": "1.0"},
        )
    )

    with pytest.raises(SupersessionError, match="not approved"):
        service.ingest_source(third)

    assert _counts(ingestion_engine)[0] == 2


def test_draft_supersession_is_rejected_before_database_write(
    ingestion_engine: Engine,
) -> None:
    with pytest.raises(MetadataValidationError):
        parse_source_text(
            _source_text(
                version="2.0",
                status="draft",
                supersedes={"doc_code": "POL-TEST-100", "version": "1.0"},
            )
        )

    assert _counts(ingestion_engine) == (0, 0)


def test_source_authored_superseded_status_is_not_inserted(
    ingestion_engine: Engine,
) -> None:
    service, _repository, _embedder = _service(ingestion_engine)
    source = parse_source_text(_source_text(status="superseded"))

    with pytest.raises(MetadataValidationError, match="approved successor"):
        service.ingest_source(source)

    assert _counts(ingestion_engine) == (0, 0)


def test_approved_replacement_requires_explicit_supersession(
    ingestion_engine: Engine,
) -> None:
    service, _repository, _embedder = _service(ingestion_engine)
    service.ingest_source(parse_source_text(_source_text(version="1.0")))

    with pytest.raises(SupersessionError, match="explicit supersession"):
        service.ingest_source(parse_source_text(_source_text(version="2.0")))

    assert _counts(ingestion_engine)[0] == 1


def test_transaction_failure_leaves_no_partial_document_or_chunks(
    ingestion_engine: Engine,
    monkeypatch,
) -> None:
    service, repository, _embedder = _service(ingestion_engine)

    def fail_chunks(*_args, **_kwargs) -> None:
        raise RuntimeError("synthetic transaction failure")

    monkeypatch.setattr(repository, "_add_chunks", fail_chunks)

    with pytest.raises(IngestionPersistenceError):
        service.ingest_source(parse_source_text(_source_text()))

    assert _counts(ingestion_engine) == (0, 0)


def test_failed_successor_transaction_preserves_approved_predecessor(
    ingestion_engine: Engine,
    monkeypatch,
) -> None:
    service, repository, _embedder = _service(ingestion_engine)
    service.ingest_source(parse_source_text(_source_text(version="1.0")))

    def fail_chunks(*_args, **_kwargs) -> None:
        raise RuntimeError("synthetic successor failure")

    monkeypatch.setattr(repository, "_add_chunks", fail_chunks)
    successor = parse_source_text(
        _source_text(
            version="2.0",
            supersedes={"doc_code": "POL-TEST-100", "version": "1.0"},
        )
    )

    with pytest.raises(IngestionPersistenceError):
        service.ingest_source(successor)

    with ingestion_engine.connect() as connection:
        rows = connection.execute(
            select(
                StoredDocument.version,
                StoredDocument.status,
                StoredDocument.superseded_by_id,
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].version == "1.0"
    assert rows[0].status == "approved"
    assert rows[0].superseded_by_id is None

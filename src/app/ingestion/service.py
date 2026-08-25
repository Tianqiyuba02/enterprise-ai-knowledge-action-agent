"""Linear V2 parse, checksum, chunk, embed, and persist orchestration."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from app.ingestion.checksum import calculate_source_checksum
from app.ingestion.chunking import HeadingAwareChunker
from app.ingestion.errors import MetadataValidationError, SourceDocumentError
from app.ingestion.models import (
    DocumentSourceStatus,
    EmbeddingProfile,
    IngestionResult,
    PreparedChunk,
    SourceDocument,
)
from app.ingestion.parser import parse_source_file
from app.ingestion.repository import KnowledgeIngestionRepository


class DocumentEmbedder(Protocol):
    profile: EmbeddingProfile

    def embed_documents(
        self,
        contents: Sequence[str],
        *,
        title: str,
    ) -> tuple[tuple[float, ...], ...]: ...


class KnowledgeIngestionService:
    """Coordinate Stage 2 ingestion without holding DB transactions across provider calls."""

    def __init__(
        self,
        *,
        repository: KnowledgeIngestionRepository,
        embedder: DocumentEmbedder,
        chunker: HeadingAwareChunker | None = None,
    ) -> None:
        self._repository = repository
        self._embedder = embedder
        self._chunker = chunker or HeadingAwareChunker()

    def ingest_file(self, path: Path) -> IngestionResult:
        return self.ingest_source(parse_source_file(path))

    def ingest_directory(self, path: Path) -> tuple[IngestionResult, ...]:
        if not path.is_dir():
            raise SourceDocumentError(f"{path}: corpus directory does not exist")
        source_paths = tuple(sorted(path.rglob("*.md")))
        if not source_paths:
            raise SourceDocumentError(f"{path}: corpus directory contains no Markdown documents")
        return tuple(self.ingest_file(source_path) for source_path in source_paths)

    def ingest_source(self, source: SourceDocument) -> IngestionResult:
        metadata = source.metadata
        if metadata.status is DocumentSourceStatus.SUPERSEDED:
            raise MetadataValidationError(
                "Source-authored superseded status is not ingestible; "
                "an approved successor must drive that stored transition."
            )

        checksum = calculate_source_checksum(source)
        existing = self._repository.find_existing(
            doc_code=metadata.doc_code,
            version=metadata.version,
        )
        if existing is not None:
            return self._repository.resolve_existing(
                existing,
                source=source,
                checksum=checksum,
                profile=self._embedder.profile,
            )

        chunks = self._chunker.chunk(source)
        embeddings = self._embedder.embed_documents(
            [chunk.content for chunk in chunks],
            title=metadata.title,
        )
        prepared = tuple(
            PreparedChunk(chunk=chunk, embedding=embedding)
            for chunk, embedding in zip(chunks, embeddings, strict=True)
        )
        return self._repository.persist_prepared(
            source=source,
            checksum=checksum,
            profile=self._embedder.profile,
            chunks=prepared,
        )

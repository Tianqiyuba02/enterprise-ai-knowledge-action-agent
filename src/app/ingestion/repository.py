"""PostgreSQL transaction boundary for V2 document ingestion."""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Document as StoredDocument
from app.db.models import DocumentChunk as StoredDocumentChunk
from app.ingestion.errors import (
    EmbeddingProfileMismatchError,
    IngestionConflictError,
    IngestionError,
    IngestionPersistenceError,
    MetadataValidationError,
    SupersessionError,
)
from app.ingestion.models import (
    DocumentSourceStatus,
    EmbeddingProfile,
    IngestionOutcome,
    IngestionResult,
    PreparedChunk,
    SourceDocument,
)


@dataclass(frozen=True, slots=True)
class ExistingDocumentProfile:
    id: uuid.UUID
    content_checksum: str
    embedding_model_id: str
    embedding_dimension: int
    chunk_count: int


class KnowledgeIngestionRepository:
    """Inspect identities and atomically persist fully prepared source documents."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def find_existing(
        self,
        *,
        doc_code: str,
        version: str,
    ) -> ExistingDocumentProfile | None:
        try:
            with self._session_factory() as session:
                document = session.scalar(
                    select(StoredDocument).where(
                        StoredDocument.doc_code == doc_code,
                        StoredDocument.version == version,
                    )
                )
                if document is None:
                    return None
                return self._profile(session, document)
        except SQLAlchemyError as exc:
            raise IngestionPersistenceError(
                "The knowledge database could not inspect document identity."
            ) from exc

    def resolve_existing(
        self,
        existing: ExistingDocumentProfile,
        *,
        source: SourceDocument,
        checksum: str,
        profile: EmbeddingProfile,
    ) -> IngestionResult:
        identity = f"{source.metadata.doc_code} v{source.metadata.version}"
        if existing.content_checksum != checksum:
            raise IngestionConflictError(
                f"{identity} already exists with different immutable source content."
            )
        if (
            existing.embedding_model_id != profile.model_id
            or existing.embedding_dimension != profile.dimension
        ):
            raise EmbeddingProfileMismatchError(
                f"{identity} exists with a different embedding/index profile; "
                "explicit reindexing is required."
            )
        return IngestionResult(
            outcome=IngestionOutcome.NO_OP,
            doc_code=source.metadata.doc_code,
            version=source.metadata.version,
            chunk_count=existing.chunk_count,
            embedding_model_id=profile.model_id,
            embedding_dimension=profile.dimension,
        )

    def persist_prepared(
        self,
        *,
        source: SourceDocument,
        checksum: str,
        profile: EmbeddingProfile,
        chunks: tuple[PreparedChunk, ...],
    ) -> IngestionResult:
        metadata = source.metadata
        if metadata.status is DocumentSourceStatus.SUPERSEDED:
            raise MetadataValidationError(
                "Source-authored superseded documents cannot be inserted directly; "
                "an approved successor must drive the stored transition."
            )

        try:
            with self._session_factory() as session, session.begin():
                self._lock_doc_code(session, metadata.doc_code)
                existing_document = session.scalar(
                    select(StoredDocument)
                    .where(
                        StoredDocument.doc_code == metadata.doc_code,
                        StoredDocument.version == metadata.version,
                    )
                    .with_for_update()
                )
                if existing_document is not None:
                    return self.resolve_existing(
                        self._profile(session, existing_document),
                        source=source,
                        checksum=checksum,
                        profile=profile,
                    )

                predecessor = self._validate_supersession(session, source)
                stored_document = StoredDocument(
                    doc_code=metadata.doc_code,
                    version=metadata.version,
                    title=metadata.title,
                    status=metadata.status.value,
                    effective_date=metadata.effective_date,
                    expiry_date=metadata.expiry_date,
                    jurisdiction=metadata.jurisdiction.value,
                    audience_groups=sorted(group.value for group in metadata.audience_groups),
                    source_uri=metadata.source_uri,
                    content_checksum=checksum,
                    superseded_by_id=None,
                    embedding_model_id=profile.model_id,
                    embedding_dimension=profile.dimension,
                )
                session.add(stored_document)
                session.flush()
                self._add_chunks(session, stored_document.id, chunks)

                if predecessor is not None:
                    predecessor.status = DocumentSourceStatus.SUPERSEDED.value
                    predecessor.superseded_by_id = stored_document.id

                return IngestionResult(
                    outcome=IngestionOutcome.INSERTED,
                    doc_code=metadata.doc_code,
                    version=metadata.version,
                    chunk_count=len(chunks),
                    embedding_model_id=profile.model_id,
                    embedding_dimension=profile.dimension,
                )
        except IngestionError:
            raise
        except IntegrityError as exc:
            raise IngestionConflictError(
                f"{metadata.doc_code} v{metadata.version} conflicts with stored knowledge data."
            ) from exc
        except SQLAlchemyError as exc:
            raise IngestionPersistenceError(
                "The knowledge database could not persist the prepared document."
            ) from exc
        except Exception as exc:
            raise IngestionPersistenceError(
                "The prepared document transaction failed and was rolled back."
            ) from exc

    @staticmethod
    def _lock_doc_code(session: Session, doc_code: str) -> None:
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"v2-document:{doc_code}"},
        )

    @staticmethod
    def _profile(session: Session, document: StoredDocument) -> ExistingDocumentProfile:
        chunk_count = session.scalar(
            select(func.count())
            .select_from(StoredDocumentChunk)
            .where(StoredDocumentChunk.document_id == document.id)
        )
        return ExistingDocumentProfile(
            id=document.id,
            content_checksum=document.content_checksum,
            embedding_model_id=document.embedding_model_id,
            embedding_dimension=document.embedding_dimension,
            chunk_count=int(chunk_count or 0),
        )

    @staticmethod
    def _validate_supersession(
        session: Session,
        source: SourceDocument,
    ) -> StoredDocument | None:
        metadata = source.metadata
        if metadata.status is not DocumentSourceStatus.APPROVED:
            return None

        approved_versions = tuple(
            session.scalars(
                select(StoredDocument)
                .where(
                    StoredDocument.doc_code == metadata.doc_code,
                    StoredDocument.status == DocumentSourceStatus.APPROVED.value,
                )
                .with_for_update()
            )
        )
        if metadata.supersedes is None:
            if approved_versions:
                raise SupersessionError(
                    f"{metadata.doc_code} already has an approved version; "
                    "explicit supersession metadata is required."
                )
            return None

        predecessor = session.scalar(
            select(StoredDocument)
            .where(
                StoredDocument.doc_code == metadata.supersedes.doc_code,
                StoredDocument.version == metadata.supersedes.version,
            )
            .with_for_update()
        )
        if predecessor is None:
            raise SupersessionError("The explicitly declared predecessor does not exist.")
        if predecessor.status != DocumentSourceStatus.APPROVED.value:
            raise SupersessionError("The explicitly declared predecessor is not approved.")
        if predecessor.superseded_by_id is not None:
            raise SupersessionError("The explicitly declared predecessor is already superseded.")
        if any(document.id != predecessor.id for document in approved_versions):
            raise SupersessionError(
                f"{metadata.doc_code} has another approved version outside the declared transition."
            )
        return predecessor

    @staticmethod
    def _add_chunks(
        session: Session,
        document_id: uuid.UUID,
        chunks: tuple[PreparedChunk, ...],
    ) -> None:
        session.add_all(
            [
                StoredDocumentChunk(
                    document_id=document_id,
                    chunk_index=prepared.chunk.chunk_index,
                    section_label=prepared.chunk.section_label,
                    anchor=prepared.chunk.anchor,
                    page=None,
                    content=prepared.chunk.content,
                    embedding=list(prepared.embedding),
                    token_count=prepared.chunk.token_count,
                )
                for prepared in chunks
            ]
        )

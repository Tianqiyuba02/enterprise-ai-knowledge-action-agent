"""SQLAlchemy models for the V2 knowledge-only PostgreSQL schema."""

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import APPROVED_EMBEDDING_DIMENSION, APPROVED_EMBEDDING_MODEL
from app.db.base import Base

DOCUMENT_STATUSES = ("draft", "approved", "superseded")
DOCUMENT_JURISDICTIONS = ("GLOBAL", "AU-VIC", "AU-NSW")
DOCUMENT_AUDIENCES = ("all_employees", "melbourne_employees", "managers")


class Document(Base):
    """One immutable source-identified company document version."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("doc_code", "version", name="uq_documents_doc_code_version"),
        CheckConstraint("btrim(doc_code) <> ''", name="ck_documents_doc_code_nonempty"),
        CheckConstraint("btrim(version) <> ''", name="ck_documents_version_nonempty"),
        CheckConstraint("btrim(title) <> ''", name="ck_documents_title_nonempty"),
        CheckConstraint(
            "status IN ('draft', 'approved', 'superseded')",
            name="ck_documents_status",
        ),
        CheckConstraint(
            "expiry_date IS NULL OR expiry_date > effective_date",
            name="ck_documents_expiry_after_effective",
        ),
        CheckConstraint(
            "jurisdiction IN ('GLOBAL', 'AU-VIC', 'AU-NSW')",
            name="ck_documents_jurisdiction",
        ),
        CheckConstraint(
            "cardinality(audience_groups) > 0",
            name="ck_documents_audience_nonempty",
        ),
        CheckConstraint(
            "audience_groups <@ ARRAY['all_employees', 'melbourne_employees', 'managers']::text[]",
            name="ck_documents_audience_vocabulary",
        ),
        CheckConstraint("btrim(source_uri) <> ''", name="ck_documents_source_uri_nonempty"),
        CheckConstraint(
            "content_checksum ~ '^[0-9a-f]{64}$'",
            name="ck_documents_checksum_sha256",
        ),
        CheckConstraint(
            "(status = 'superseded' AND superseded_by_id IS NOT NULL) OR "
            "(status IN ('draft', 'approved') AND superseded_by_id IS NULL)",
            name="ck_documents_status_supersession_link",
        ),
        CheckConstraint(
            "superseded_by_id IS NULL OR superseded_by_id <> id",
            name="ck_documents_not_self_superseded",
        ),
        CheckConstraint(
            "btrim(embedding_model_id) <> ''",
            name="ck_documents_embedding_model_nonempty",
        ),
        CheckConstraint(
            f"embedding_dimension = {APPROVED_EMBEDDING_DIMENSION}",
            name="ck_documents_embedding_dimension",
        ),
        Index(
            "ix_documents_authority_dates",
            "status",
            "effective_date",
            "expiry_date",
        ),
        Index("ix_documents_jurisdiction", "jurisdiction"),
        Index(
            "ix_documents_audience_groups",
            "audience_groups",
            postgresql_using="gin",
        ),
        Index("ix_documents_superseded_by_id", "superseded_by_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    doc_code: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    jurisdiction: Mapped[str] = mapped_column(Text, nullable=False)
    audience_groups: Mapped[list[str]] = mapped_column(ARRAY(Text()), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", name="fk_documents_superseded_by_id", ondelete="RESTRICT"),
        nullable=True,
    )
    embedding_model_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text(f"'{APPROVED_EMBEDDING_MODEL}'"),
    )
    embedding_dimension: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text(str(APPROVED_EMBEDDING_DIMENSION)),
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DocumentChunk(Base):
    """One internal retrieval unit belonging to a document version."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_index",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_document_chunks_index_nonnegative"),
        CheckConstraint(
            "btrim(section_label) <> ''",
            name="ck_document_chunks_section_label_nonempty",
        ),
        CheckConstraint("btrim(anchor) <> ''", name="ck_document_chunks_anchor_nonempty"),
        CheckConstraint("page IS NULL OR page >= 1", name="ck_document_chunks_page_positive"),
        CheckConstraint("btrim(content) <> ''", name="ck_document_chunks_content_nonempty"),
        CheckConstraint("token_count > 0", name="ck_document_chunks_token_count_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", name="fk_document_chunks_document_id", ondelete="RESTRICT"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section_label: Mapped[str] = mapped_column(Text, nullable=False)
    anchor: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(APPROVED_EMBEDDING_DIMENSION),
        nullable=False,
    )
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

"""Create the V2 knowledge persistence schema.

Revision ID: 0001_v2_knowledge
Revises:
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_v2_knowledge"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("doc_code", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("jurisdiction", sa.Text(), nullable=False),
        sa.Column("audience_groups", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("content_checksum", sa.Text(), nullable=False),
        sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "embedding_model_id",
            sa.Text(),
            server_default=sa.text("'gemini-embedding-2'"),
            nullable=False,
        ),
        sa.Column(
            "embedding_dimension",
            sa.Integer(),
            server_default=sa.text("768"),
            nullable=False,
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(doc_code) <> ''", name="ck_documents_doc_code_nonempty"),
        sa.CheckConstraint("btrim(version) <> ''", name="ck_documents_version_nonempty"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_documents_title_nonempty"),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'superseded')",
            name="ck_documents_status",
        ),
        sa.CheckConstraint(
            "expiry_date IS NULL OR expiry_date > effective_date",
            name="ck_documents_expiry_after_effective",
        ),
        sa.CheckConstraint(
            "jurisdiction IN ('GLOBAL', 'AU-VIC', 'AU-NSW')",
            name="ck_documents_jurisdiction",
        ),
        sa.CheckConstraint(
            "cardinality(audience_groups) > 0",
            name="ck_documents_audience_nonempty",
        ),
        sa.CheckConstraint(
            "audience_groups <@ ARRAY['all_employees', 'melbourne_employees', 'managers']::text[]",
            name="ck_documents_audience_vocabulary",
        ),
        sa.CheckConstraint(
            "btrim(source_uri) <> ''",
            name="ck_documents_source_uri_nonempty",
        ),
        sa.CheckConstraint(
            "content_checksum ~ '^[0-9a-f]{64}$'",
            name="ck_documents_checksum_sha256",
        ),
        sa.CheckConstraint(
            "(status = 'superseded' AND superseded_by_id IS NOT NULL) OR "
            "(status IN ('draft', 'approved') AND superseded_by_id IS NULL)",
            name="ck_documents_status_supersession_link",
        ),
        sa.CheckConstraint(
            "superseded_by_id IS NULL OR superseded_by_id <> id",
            name="ck_documents_not_self_superseded",
        ),
        sa.CheckConstraint(
            "btrim(embedding_model_id) <> ''",
            name="ck_documents_embedding_model_nonempty",
        ),
        sa.CheckConstraint(
            "embedding_dimension = 768",
            name="ck_documents_embedding_dimension",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["documents.id"],
            name="fk_documents_superseded_by_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "doc_code",
            "version",
            name="uq_documents_doc_code_version",
        ),
    )
    op.create_index(
        "ix_documents_authority_dates",
        "documents",
        ["status", "effective_date", "expiry_date"],
        unique=False,
    )
    op.create_index(
        "ix_documents_jurisdiction",
        "documents",
        ["jurisdiction"],
        unique=False,
    )
    op.create_index(
        "ix_documents_audience_groups",
        "documents",
        ["audience_groups"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_documents_superseded_by_id",
        "documents",
        ["superseded_by_id"],
        unique=False,
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section_label", sa.Text(), nullable=False),
        sa.Column("anchor", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(dim=768), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "chunk_index >= 0",
            name="ck_document_chunks_index_nonnegative",
        ),
        sa.CheckConstraint(
            "btrim(section_label) <> ''",
            name="ck_document_chunks_section_label_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(anchor) <> ''",
            name="ck_document_chunks_anchor_nonempty",
        ),
        sa.CheckConstraint(
            "page IS NULL OR page >= 1",
            name="ck_document_chunks_page_positive",
        ),
        sa.CheckConstraint(
            "btrim(content) <> ''",
            name="ck_document_chunks_content_nonempty",
        ),
        sa.CheckConstraint(
            "token_count > 0",
            name="ck_document_chunks_token_count_positive",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_chunks_document_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_index",
        ),
    )


def downgrade() -> None:
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_superseded_by_id", table_name="documents")
    op.drop_index("ix_documents_audience_groups", table_name="documents")
    op.drop_index("ix_documents_jurisdiction", table_name="documents")
    op.drop_index("ix_documents_authority_dates", table_name="documents")
    op.drop_table("documents")

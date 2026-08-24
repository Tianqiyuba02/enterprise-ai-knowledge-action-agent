from unittest.mock import Mock, patch

import pytest
from pgvector.sqlalchemy import Vector
from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine

from app.config import (
    APPROVED_EMBEDDING_DIMENSION,
    APPROVED_EMBEDDING_MODEL,
    DEFAULT_KNOWLEDGE_DATABASE_URL,
    KnowledgeSettings,
)
from app.db import Base
from app.db.models import Document, DocumentChunk
from app.db.session import create_knowledge_engine, create_knowledge_session_factory


def test_knowledge_settings_have_isolated_safe_local_defaults() -> None:
    settings = KnowledgeSettings(_env_file=None)

    assert settings.knowledge_database_url.get_secret_value() == DEFAULT_KNOWLEDGE_DATABASE_URL
    assert settings.knowledge_embedding_model == APPROVED_EMBEDDING_MODEL
    assert settings.knowledge_embedding_dimension == APPROVED_EMBEDDING_DIMENSION


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("knowledge_embedding_model", "gemini-embedding-2-preview"),
        ("knowledge_embedding_dimension", 3072),
    ],
)
def test_knowledge_settings_reject_unapproved_embedding_profile(field: str, value: object) -> None:
    values = {field: value, "_env_file": None}

    with pytest.raises(ValidationError):
        KnowledgeSettings(**values)


def test_knowledge_engine_is_created_lazily_without_connecting() -> None:
    settings = KnowledgeSettings(
        knowledge_database_url=SecretStr("postgresql+psycopg://example.invalid/database"),
        _env_file=None,
    )
    engine = Mock(spec=Engine)

    with patch("app.db.session.create_engine", return_value=engine) as create_engine:
        result = create_knowledge_engine(settings)

    assert result is engine
    create_engine.assert_called_once_with(
        "postgresql+psycopg://example.invalid/database",
        pool_pre_ping=True,
    )
    engine.connect.assert_not_called()


def test_session_factory_is_synchronous() -> None:
    engine = Mock(spec=Engine)

    factory = create_knowledge_session_factory(engine)

    assert factory.kw["bind"] is engine
    assert factory.kw["expire_on_commit"] is False


def test_v2_metadata_contains_only_knowledge_tables() -> None:
    assert set(Base.metadata.tables) == {"documents", "document_chunks"}


def test_document_metadata_declares_required_constraints_and_indexes() -> None:
    table = Document.__table__
    constraint_names = {constraint.name for constraint in table.constraints}
    index_names = {index.name for index in table.indexes}

    assert {
        "uq_documents_doc_code_version",
        "ck_documents_expiry_after_effective",
        "ck_documents_checksum_sha256",
        "ck_documents_status_supersession_link",
        "ck_documents_not_self_superseded",
        "ck_documents_embedding_dimension",
        "ck_documents_audience_nonempty",
        "ck_documents_audience_vocabulary",
    } <= constraint_names
    assert {
        "ix_documents_authority_dates",
        "ix_documents_jurisdiction",
        "ix_documents_audience_groups",
        "ix_documents_superseded_by_id",
    } == index_names


def test_document_chunk_metadata_uses_restricted_vector_768_foreign_key() -> None:
    table = DocumentChunk.__table__
    embedding_type = table.c.embedding.type
    foreign_key = next(iter(table.c.document_id.foreign_keys))
    constraint_names = {constraint.name for constraint in table.constraints}

    assert isinstance(embedding_type, Vector)
    assert embedding_type.dim == APPROVED_EMBEDDING_DIMENSION
    assert foreign_key.target_fullname == "documents.id"
    assert foreign_key.ondelete == "RESTRICT"
    assert {
        "uq_document_chunks_document_index",
        "ck_document_chunks_index_nonnegative",
        "ck_document_chunks_page_positive",
        "ck_document_chunks_token_count_positive",
    } <= constraint_names

"""Isolated PostgreSQL evaluation database. Never mutates the development DB as the working set."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.evaluation.v4.fingerprints import baseline_data_fingerprint
from app.repositories.demo import DemoRepository
from app.workflow.calendar import V4_CALENDAR_VERSION, VIC_2026_STATEWIDE_HOLIDAYS

EXPECTED_DOCUMENTS = 12
EXPECTED_CHUNKS = 42
EXPECTED_HOLIDAYS = len(VIC_2026_STATEWIDE_HOLIDAYS)
WORKFLOW_CLEANUP_TABLES = (
    "leave_requests",
    "confirmation_challenges",
    "action_audit_events",
    "action_revisions",
    "action_workflows",
)


class V4EvaluationIsolationError(RuntimeError):
    """Raised when the isolated evaluation database cannot be prepared safely."""


@dataclass(frozen=True, slots=True)
class IsolatedEvaluationDatabase:
    settings: KnowledgeSettings
    engine: Engine
    session_factory: sessionmaker[Session]
    source_settings: KnowledgeSettings
    database_name: str
    baseline_data_fingerprint: str


def _replace_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def copy_trusted_corpus(source: Engine, destination: Engine) -> None:
    """Copy V2 documents/chunks from the development DB without re-embedding."""

    with source.connect() as inbound, destination.begin() as outbound:
        documents = inbound.execute(text("SELECT * FROM documents ORDER BY ingested_at")).mappings()
        supersessions: list[dict[str, object]] = []
        for row in documents:
            payload = dict(row)
            if payload.get("superseded_by_id") is not None:
                supersessions.append(
                    {"id": payload["id"], "superseded_by_id": payload["superseded_by_id"]}
                )
                payload["superseded_by_id"] = None
                if payload.get("status") == "superseded":
                    payload["status"] = "approved"
            outbound.execute(
                text(
                    """
                    INSERT INTO documents (
                        id, doc_code, version, title, status, effective_date, expiry_date,
                        jurisdiction, audience_groups, source_uri, content_checksum,
                        superseded_by_id, embedding_model_id, embedding_dimension, ingested_at
                    ) VALUES (
                        :id, :doc_code, :version, :title, :status, :effective_date, :expiry_date,
                        :jurisdiction, :audience_groups, :source_uri, :content_checksum,
                        :superseded_by_id, :embedding_model_id, :embedding_dimension, :ingested_at
                    )
                    """
                ),
                payload,
            )
        for link in supersessions:
            outbound.execute(
                text(
                    """
                    UPDATE documents
                    SET status = 'superseded', superseded_by_id = :superseded_by_id
                    WHERE id = :id
                    """
                ),
                link,
            )
        chunks = inbound.execute(
            text("SELECT * FROM document_chunks ORDER BY created_at")
        ).mappings()
        for row in chunks:
            outbound.execute(
                text(
                    """
                    INSERT INTO document_chunks (
                        id, document_id, chunk_index, section_label, anchor, page, content,
                        embedding, token_count, created_at
                    ) VALUES (
                        :id, :document_id, :chunk_index, :section_label, :anchor, :page, :content,
                        :embedding, :token_count, :created_at
                    )
                    """
                ),
                dict(row),
            )


def assert_baseline(engine: Engine) -> None:
    documents = _count(engine, "documents")
    chunks = _count(engine, "document_chunks")
    holidays = _count(engine, "public_holidays")
    if documents != EXPECTED_DOCUMENTS or chunks != EXPECTED_CHUNKS:
        raise V4EvaluationIsolationError(
            f"isolated corpus baseline mismatch: documents={documents} chunks={chunks}"
        )
    if holidays != EXPECTED_HOLIDAYS:
        raise V4EvaluationIsolationError(f"isolated holiday baseline mismatch: {holidays}")
    with engine.connect() as connection:
        version = connection.execute(
            text("SELECT DISTINCT calendar_version FROM public_holidays")
        ).scalar_one()
    if version != V4_CALENDAR_VERSION:
        raise V4EvaluationIsolationError("isolated calendar version mismatch")


def cleanup_workflow_state(engine: Engine) -> None:
    with engine.begin() as connection:
        for table in WORKFLOW_CLEANUP_TABLES:
            connection.execute(text(f"DELETE FROM {table}"))


def workflow_counts(engine: Engine) -> dict[str, int]:
    return {
        "action_workflows": _count(engine, "action_workflows"),
        "action_revisions": _count(engine, "action_revisions"),
        "confirmation_challenges": _count(engine, "confirmation_challenges"),
        "workflow_outbox": 0,
        "action_execution_ledger": 0,
        "leave_requests": _count(engine, "leave_requests"),
    }


@contextmanager
def isolated_evaluation_database(
    *,
    source_settings: KnowledgeSettings | None = None,
) -> Iterator[IsolatedEvaluationDatabase]:
    live = source_settings or load_knowledge_settings()
    admin_url = live.database_url.get_secret_value()
    database_name = f"knowledge_agent_v4_eval_{uuid4().hex[:12]}"
    isolated_url = _replace_database(admin_url, database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    source_engine = create_knowledge_engine(live)
    isolated_engine: Engine | None = None
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        previous_app_url = os.environ.get("APP_DATABASE_URL")
        os.environ["APP_DATABASE_URL"] = isolated_url
        try:
            command.upgrade(AlembicConfig("alembic.ini"), "head")
            isolated_settings = load_knowledge_settings()
        finally:
            if previous_app_url is None:
                os.environ.pop("APP_DATABASE_URL", None)
            else:
                os.environ["APP_DATABASE_URL"] = previous_app_url
        isolated_engine = create_knowledge_engine(isolated_settings)
        copy_trusted_corpus(source_engine, isolated_engine)
        assert_baseline(isolated_engine)
        baseline = baseline_data_fingerprint(isolated_engine, repository=DemoRepository())
        yield IsolatedEvaluationDatabase(
            settings=isolated_settings,
            engine=isolated_engine,
            session_factory=create_knowledge_session_factory(isolated_engine),
            source_settings=live,
            database_name=database_name,
            baseline_data_fingerprint=baseline,
        )
    finally:
        if isolated_engine is not None:
            isolated_engine.dispose()
        source_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database_name AND pid <> pg_backend_pid()
                    """
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()

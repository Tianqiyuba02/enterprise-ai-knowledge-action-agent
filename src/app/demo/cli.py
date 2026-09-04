"""Private operational commands for Render pre-deploy and scheduled reset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text

from app.config import load_knowledge_settings, load_public_demo_settings, load_settings
from app.db.models import Document, DocumentChunk
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.demo.service import (
    EXPECTED_MIGRATION_HEAD,
    REQUIRED_DOCUMENT_IDENTITIES,
    DemoControlService,
)
from app.embeddings.client import GeminiDocumentEmbeddingClient
from app.ingestion.repository import KnowledgeIngestionRepository
from app.ingestion.service import KnowledgeIngestionService


def bootstrap(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Migrate and verify the private demo database.")
    parser.add_argument("--corpus", type=Path, default=Path("corpus/v2"))
    args = parser.parse_args(argv)
    settings = load_public_demo_settings()
    if not settings.enabled:
        raise SystemExit("PUBLIC_DEMO_MODE must be enabled for demo bootstrap")
    command.upgrade(Config("alembic.ini"), "head")
    knowledge_settings = load_knowledge_settings()
    engine = create_knowledge_engine(knowledge_settings)
    factory = create_knowledge_session_factory(engine)
    try:
        with factory() as session:
            doc_count = session.scalar(select(func.count()).select_from(Document)) or 0
            chunk_count = session.scalar(select(func.count()).select_from(DocumentChunk)) or 0
            identities = {
                (doc_code, version)
                for doc_code, version in session.execute(
                    select(Document.doc_code, Document.version)
                )
            }
        if (
            doc_count != settings.expected_document_count
            or chunk_count != settings.expected_chunk_count
            or not identities >= REQUIRED_DOCUMENT_IDENTITIES
        ):
            provider_settings = load_settings()
            service = KnowledgeIngestionService(
                repository=KnowledgeIngestionRepository(factory),
                embedder=GeminiDocumentEmbeddingClient(provider_settings, knowledge_settings),
            )
            service.ingest_directory(args.corpus)
        with factory() as session:
            migration = session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            doc_count = session.scalar(select(func.count()).select_from(Document)) or 0
            chunk_count = session.scalar(select(func.count()).select_from(DocumentChunk)) or 0
            identities = {
                (doc_code, version)
                for doc_code, version in session.execute(
                    select(Document.doc_code, Document.version)
                )
            }
        if (
            migration != EXPECTED_MIGRATION_HEAD
            or doc_count != settings.expected_document_count
            or chunk_count != settings.expected_chunk_count
            or not identities >= REQUIRED_DOCUMENT_IDENTITIES
        ):
            raise RuntimeError("demo bootstrap verification failed")
        print(
            f"demo bootstrap ready migration={migration} documents={doc_count} chunks={chunk_count}"
        )
    finally:
        engine.dispose()


def reset(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Reset private synthetic demo business data.")
    parser.parse_args(argv)
    settings = load_public_demo_settings()
    if not settings.enabled:
        raise SystemExit("PUBLIC_DEMO_MODE must be enabled for demo reset")
    knowledge_settings = load_knowledge_settings()
    engine = create_knowledge_engine(knowledge_settings)
    try:
        print(json.dumps({"event": "demo_reset", "service": "reset", "outcome": "started"}))
        try:
            DemoControlService(create_knowledge_session_factory(engine), settings).reset()
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "demo_reset",
                        "service": "reset",
                        "outcome": "failure",
                        "exception_category": type(exc).__name__,
                    }
                )
            )
            raise
        print(json.dumps({"event": "demo_reset", "service": "reset", "outcome": "success"}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit("Use an installed enterprise-ai-demo-* command.")

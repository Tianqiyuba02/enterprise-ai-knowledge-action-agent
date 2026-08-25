"""Explicit Stage 2 command-line adapter for one file or the synthetic corpus."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from app.config import (
    ConfigurationError,
    KnowledgeConfigurationError,
    load_knowledge_settings,
    load_settings,
)
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.embeddings.client import EmbeddingClientError, GeminiDocumentEmbeddingClient
from app.ingestion.errors import IngestionError
from app.ingestion.models import IngestionResult
from app.ingestion.repository import KnowledgeIngestionRepository
from app.ingestion.service import KnowledgeIngestionService

DEFAULT_CORPUS_DIRECTORY = Path("corpus/v2")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enterprise-ai-ingest",
        description="Ingest synthetic V2 Markdown policies into PostgreSQL.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    file_parser = subparsers.add_parser("file", help="ingest one Markdown source document")
    file_parser.add_argument("path", type=Path)

    corpus_parser = subparsers.add_parser("corpus", help="ingest a Markdown corpus directory")
    corpus_parser.add_argument("path", type=Path, nargs="?", default=DEFAULT_CORPUS_DIRECTORY)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = None
    try:
        settings = load_settings()
        knowledge_settings = load_knowledge_settings()
        engine = create_knowledge_engine(knowledge_settings)
        repository = KnowledgeIngestionRepository(create_knowledge_session_factory(engine))
        embedder = GeminiDocumentEmbeddingClient(settings, knowledge_settings)
        service = KnowledgeIngestionService(repository=repository, embedder=embedder)

        if args.command == "file":
            results = (service.ingest_file(args.path),)
        else:
            results = service.ingest_directory(args.path)
    except (
        ConfigurationError,
        KnowledgeConfigurationError,
        EmbeddingClientError,
        IngestionError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("Error: Ingestion could not be completed.", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    for result in results:
        print(_format_result(result))
    return 0


def _format_result(result: IngestionResult) -> str:
    return (
        f"{result.outcome.value}: {result.doc_code} v{result.version}; "
        f"chunks={result.chunk_count}; embedding={result.embedding_model_id}/"
        f"{result.embedding_dimension}"
    )


def run() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    run()

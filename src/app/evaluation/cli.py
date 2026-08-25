"""Developer CLI for frozen Stage 5A retrieval and grounded baselines."""

import argparse
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app.config import load_knowledge_settings, load_settings
from app.db.models import Document, DocumentChunk
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.embeddings.client import GeminiDocumentEmbeddingClient
from app.evaluation.loader import load_evaluation_cases
from app.evaluation.models import (
    CaseExecutionState,
    EvaluationConfiguration,
    EvaluationMode,
    EvaluationSplit,
)
from app.evaluation.runner import EvaluationRunner
from app.grounding.client import GeminiGroundedGenerationClient
from app.identity import AuthenticatedEmployeeContext
from app.ingestion.chunking import DEFAULT_OVERLAP_TOKENS, DEFAULT_TARGET_TOKENS
from app.knowledge.applicability import resolve_knowledge_applicability
from app.knowledge.query_service import KnowledgeQueryService
from app.knowledge.repository import KnowledgeRetrievalRepository
from app.knowledge.service import DEFAULT_RETRIEVAL_TOP_K, KnowledgeRetrievalService
from app.repositories.demo import DemoRepository

BASELINE_AS_OF_DATE = date(2026, 8, 25)


class FixedEvaluationClock:
    def today(self) -> date:
        return BASELINE_AS_OF_DATE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enterprise-ai-eval",
        description="Measure the frozen V2 RAG baseline without tuning.",
    )
    parser.add_argument("--mode", choices=[mode.value for mode in EvaluationMode], required=True)
    parser.add_argument(
        "--split",
        choices=[split.value for split in EvaluationSplit],
        required=True,
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="explicitly allow Gemini and PostgreSQL calls",
    )
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode = EvaluationMode(args.mode)
    split = EvaluationSplit(args.split)
    if not args.live:
        print(
            "Error: evaluation requires explicit --live provider/database access.", file=sys.stderr
        )
        return 2

    output = args.output or Path(f"evals/results/v2-stage5a-{split.value}-{mode.value}.json")
    engine = None
    try:
        settings = load_settings()
        knowledge_settings = load_knowledge_settings()
        engine = create_knowledge_engine(knowledge_settings)
        session_factory = create_knowledge_session_factory(engine)
        embedder = GeminiDocumentEmbeddingClient(settings, knowledge_settings)
        retrieval = KnowledgeRetrievalService(
            embedder=embedder,
            repository=KnowledgeRetrievalRepository(session_factory),
            clock=FixedEvaluationClock(),
            top_k=DEFAULT_RETRIEVAL_TOP_K,
        )
        grounded = KnowledgeQueryService(
            retrieval=retrieval,
            generator=GeminiGroundedGenerationClient(settings, knowledge_settings),
        )
        applicability = resolve_knowledge_applicability(
            AuthenticatedEmployeeContext(employee_id="EMP-1001"),
            DemoRepository(),
        )
        with engine.connect() as connection:
            document_count = int(connection.scalar(select(func.count()).select_from(Document)) or 0)
            chunk_count = int(
                connection.scalar(select(func.count()).select_from(DocumentChunk)) or 0
            )
        runner = EvaluationRunner(
            retrieval=retrieval,
            grounded=grounded,
            applicability=applicability,
            trusted_today=BASELINE_AS_OF_DATE,
            configuration=EvaluationConfiguration(
                embedding_model=knowledge_settings.knowledge_embedding_model,
                embedding_dimension=knowledge_settings.knowledge_embedding_dimension,
                grounded_generation_model=knowledge_settings.knowledge_grounded_model,
                retrieval_metric="exact_pgvector_cosine_distance",
                top_k=DEFAULT_RETRIEVAL_TOP_K,
                minimum_similarity_threshold=None,
                chunk_target_tokens=DEFAULT_TARGET_TOKENS,
                chunk_overlap_tokens=DEFAULT_OVERLAP_TOKENS,
                trusted_as_of_date=BASELINE_AS_OF_DATE,
                corpus_documents=document_count,
                corpus_chunks=chunk_count,
            ),
        )
        report = runner.run(
            mode=mode,
            split=split,
            cases=load_evaluation_cases(split),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"Error: evaluation failed safely ({type(exc).__name__}).", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    print(f"mode={report.mode.value} split={report.split.value}")
    print(
        f"completed={report.summary.cases_completed}/{report.summary.cases_total} "
        f"blocked={report.summary.cases_blocked_by_provider_rate_limit} "
        f"errors={report.summary.cases_error}"
    )
    print(f"report={output}")
    if split is EvaluationSplit.HOLDOUT:
        print("holdout_use=final_post_tuning_validation_only")
    if any(
        result.state is CaseExecutionState.BLOCKED_BY_PROVIDER_RATE_LIMIT for result in report.cases
    ):
        print("status=blocked_by_provider_rate_limit")
        return 3
    return 0


def run() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    run()

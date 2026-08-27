"""Live CLI construction for the real V3 agent evaluation path."""

import hashlib
import json
import sys
from argparse import Namespace
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app.agent.client import GeminiAgentClient
from app.agent.contracts import (
    MAX_TOOL_CALLS_PER_TURN,
    V3_TOOL_ALLOWLIST,
)
from app.agent.dispatcher import ToolDispatcher
from app.agent.service import MAX_MODEL_ROUNDS_PER_TURN
from app.config import load_agent_settings, load_knowledge_settings, load_settings
from app.db.models import Document, DocumentChunk
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.embeddings.client import GeminiDocumentEmbeddingClient
from app.evaluation.agent_loader import (
    agent_dataset_fingerprint,
    load_agent_evaluation_cases,
)
from app.evaluation.agent_models import (
    AgentCaseExecutionState,
    AgentEvaluationConfiguration,
    AgentEvaluationReport,
)
from app.evaluation.agent_runner import AgentEvaluationRunner
from app.evaluation.models import EvaluationSplit
from app.grounding.client import GeminiGroundedGenerationClient
from app.knowledge.query_service import KnowledgeQueryService
from app.knowledge.repository import KnowledgeRetrievalRepository
from app.knowledge.service import DEFAULT_RETRIEVAL_TOP_K, KnowledgeRetrievalService
from app.repositories.demo import DemoRepository
from app.services.employee import EmployeeService
from app.services.it import ITService
from app.services.leave_preparation import LeavePreparationService

AGENT_EVALUATION_DATE = date(2026, 8, 26)
AGENT_EVALUATION_SCHEMA_VERSION = "v3-agent-eval-2"
DEMO_FIXTURE_VERSION = "v1-demo-records-2026-08-24"
FROZEN_AGENT_HOLDOUT_FINGERPRINT = (
    "b68a78f687b81040e265aef6d934d4879b3180405159cb4d5ed10ad923ba4d58"
)
UNAUTHORIZED_HOLDOUT_MESSAGE = (
    "Error: V3 agent holdout is frozen and requires explicit --authorize-holdout."
)
HOLDOUT_AUTHORIZATION_SCOPE_MESSAGE = (
    "Error: --authorize-holdout is valid only with --split holdout."
)
HOLDOUT_FINGERPRINT_MISMATCH_MESSAGE = (
    "Error: V3 agent holdout fingerprint does not match the frozen holdout."
)


class FixedAgentEvaluationClock:
    def today(self) -> date:
        return AGENT_EVALUATION_DATE


def authorize_agent_evaluation_split(
    split: EvaluationSplit,
    *,
    authorize_holdout: bool,
    dataset_fingerprint: str | None = None,
) -> str | None:
    """Return a safe error if the requested agent split is not explicitly authorized."""

    if split is EvaluationSplit.HOLDOUT:
        if not authorize_holdout:
            return UNAUTHORIZED_HOLDOUT_MESSAGE
        if (
            dataset_fingerprint is not None
            and dataset_fingerprint != FROZEN_AGENT_HOLDOUT_FINGERPRINT
        ):
            return HOLDOUT_FINGERPRINT_MISMATCH_MESSAGE
        return None
    if authorize_holdout:
        return HOLDOUT_AUTHORIZATION_SCOPE_MESSAGE
    return None


def run_agent_cli(args: Namespace, split: EvaluationSplit) -> int:
    authorize_holdout = bool(getattr(args, "authorize_holdout", False))
    authorization_error = authorize_agent_evaluation_split(
        split, authorize_holdout=authorize_holdout
    )
    if authorization_error is not None:
        print(authorization_error, file=sys.stderr)
        return 2

    output: Path = args.output or (
        Path("evals/results/v3-stage5b-holdout-agent.json")
        if split is EvaluationSplit.HOLDOUT
        else Path("evals/results/v3-stage5a-development-agent.json")
    )
    if args.resume and not output.is_file():
        print("Error: --resume requires an existing report file.", file=sys.stderr)
        return 2

    engine = None
    try:
        cases = load_agent_evaluation_cases(split)
        dataset_fingerprint = agent_dataset_fingerprint(cases)
        authorization_error = authorize_agent_evaluation_split(
            split,
            authorize_holdout=authorize_holdout,
            dataset_fingerprint=dataset_fingerprint,
        )
        if authorization_error is not None:
            print(authorization_error, file=sys.stderr)
            return 2
        previous_report = (
            AgentEvaluationReport.model_validate_json(output.read_text(encoding="utf-8"))
            if args.resume
            else None
        )
        settings = load_settings()
        knowledge_settings = load_knowledge_settings()
        agent_settings = load_agent_settings()
        engine = create_knowledge_engine(knowledge_settings)
        session_factory = create_knowledge_session_factory(engine)
        retrieval = KnowledgeRetrievalService(
            embedder=GeminiDocumentEmbeddingClient(settings, knowledge_settings),
            repository=KnowledgeRetrievalRepository(session_factory),
            clock=FixedAgentEvaluationClock(),
            top_k=DEFAULT_RETRIEVAL_TOP_K,
        )
        knowledge_service = KnowledgeQueryService(
            retrieval=retrieval,
            generator=GeminiGroundedGenerationClient(settings, knowledge_settings),
        )
        repository = DemoRepository()
        employee_service = EmployeeService(repository)
        dispatcher = ToolDispatcher(
            employee_service=employee_service,
            it_service=ITService(repository),
            knowledge_service=knowledge_service,
            demo_repository=repository,
            leave_preparation_service=LeavePreparationService(employee_service),
        )
        with engine.connect() as connection:
            document_count = int(connection.scalar(select(func.count()).select_from(Document)) or 0)
            chunk_count = int(
                connection.scalar(select(func.count()).select_from(DocumentChunk)) or 0
            )
            document_rows = connection.execute(
                select(
                    Document.doc_code,
                    Document.version,
                    Document.status,
                    Document.effective_date,
                    Document.expiry_date,
                    Document.jurisdiction,
                    Document.content_checksum,
                    Document.embedding_model_id,
                    Document.embedding_dimension,
                ).order_by(Document.doc_code, Document.version)
            ).all()
        configuration = AgentEvaluationConfiguration(
            evaluation_schema_version=AGENT_EVALUATION_SCHEMA_VERSION,
            agent_model=agent_settings.agent_model,
            agent_timeout_seconds=agent_settings.agent_timeout_seconds,
            agent_max_attempts=agent_settings.agent_max_attempts,
            trusted_evaluation_date=AGENT_EVALUATION_DATE,
            max_tool_calls=MAX_TOOL_CALLS_PER_TURN,
            max_model_rounds=MAX_MODEL_ROUNDS_PER_TURN,
            tool_registry_fingerprint=_tool_registry_fingerprint(),
            demo_fixture_version=DEMO_FIXTURE_VERSION,
            grounded_generation_model=knowledge_settings.knowledge_grounded_model,
            embedding_model=knowledge_settings.knowledge_embedding_model,
            embedding_dimension=knowledge_settings.knowledge_embedding_dimension,
            retrieval_top_k=DEFAULT_RETRIEVAL_TOP_K,
            corpus_identity=_corpus_identity(document_rows),
            corpus_documents=document_count,
            corpus_chunks=chunk_count,
        )
        report = AgentEvaluationRunner(
            provider=GeminiAgentClient(settings, agent_settings),
            dispatcher=dispatcher,
            repository=repository,
            clock=FixedAgentEvaluationClock(),
            configuration=configuration,
        ).run(
            split=split,
            cases=cases,
            dataset_fingerprint=dataset_fingerprint,
            previous_report=previous_report,
            delay_seconds=args.delay_seconds,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    except Exception as exc:
        print(
            f"Error: agent evaluation failed safely ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    print(f"mode=agent split={split.value}")
    if split is EvaluationSplit.HOLDOUT:
        print("holdout_use=frozen_final_validation_only")
    print(f"dataset_fingerprint={report.dataset_fingerprint}")
    print(f"trusted_evaluation_date={report.configuration.trusted_evaluation_date}")
    print(
        f"attempted={report.summary.cases_attempted}/{report.summary.cases_total} "
        f"completed={report.summary.cases_completed} "
        f"carried={report.summary.cases_carried_forward} "
        f"completed_now={report.summary.cases_completed_current_invocation} "
        f"provider_blocked={report.summary.cases_provider_blocked} "
        f"errors={report.summary.cases_error}"
    )
    print(f"report={output}")
    if any(result.state is AgentCaseExecutionState.PROVIDER_BLOCKED for result in report.cases):
        print("status=provider_blocked")
        return 3
    return 0


def _tool_registry_fingerprint() -> str:
    canonical = [
        {
            "name": name.value,
            "capability": contract.capability.value,
            "arguments": contract.argument_model.model_json_schema(),
        }
        for name, contract in V3_TOOL_ALLOWLIST.items()
    ]
    return _sha256_json(canonical)


def _corpus_identity(rows) -> str:
    canonical = [
        {
            "doc_code": row.doc_code,
            "version": row.version,
            "status": row.status,
            "effective_date": row.effective_date.isoformat(),
            "expiry_date": row.expiry_date.isoformat() if row.expiry_date else None,
            "jurisdiction": row.jurisdiction,
            "content_checksum": row.content_checksum,
            "embedding_model_id": row.embedding_model_id,
            "embedding_dimension": row.embedding_dimension,
        }
        for row in rows
    ]
    return _sha256_json(canonical)


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

"""Live CLI for V4 development evaluation. Holdout does not exist."""

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from app.agent.client import GeminiAgentClient
from app.agent.dispatcher import ToolDispatcher
from app.agent.service import AgentService
from app.config import load_agent_settings, load_knowledge_settings, load_settings
from app.db.session import create_knowledge_session_factory
from app.embeddings.client import GeminiDocumentEmbeddingClient
from app.evaluation.models import EvaluationSplit
from app.evaluation.v4.clock import (
    V4_DEVELOPMENT_BUSINESS_DATE,
    V4DevelopmentBusinessClock,
)
from app.evaluation.v4.fingerprints import build_fingerprints
from app.evaluation.v4.isolation import isolated_evaluation_database
from app.evaluation.v4.loader import (
    assert_no_v4_holdout,
    load_v4_development_cases,
    v4_dataset_fingerprint,
)
from app.evaluation.v4.models import (
    V4_DEVELOPMENT_SET_VERSION,
    V4_EVALUATOR_VERSION,
    V4EvaluationConfiguration,
    V4ProductEvaluationReport,
)
from app.evaluation.v4.preflight import (
    FailedPreflightBlocksDevelopmentRun,
    ProviderPreflight,
    persist_launch_preflight_result,
    require_successful_preflight,
)
from app.evaluation.v4.run1_archive import is_closed_run1_report, refuse_eval2_write_over_run1
from app.evaluation.v4.runner import V4ProductEvaluationRunner
from app.evaluation.v4.transport import DEFAULT_EVAL2_OUTPUT, DEFAULT_PREFLIGHT_OUTPUT
from app.grounding.client import GeminiGroundedGenerationClient
from app.knowledge.query_service import KnowledgeQueryService
from app.knowledge.repository import KnowledgeRetrievalRepository
from app.knowledge.service import KnowledgeRetrievalService
from app.repositories.demo import DemoRepository
from app.services.employee import EmployeeService
from app.services.it import ITService
from app.services.leave_preparation import LeavePreparationService

V4_HOLDOUT_DOES_NOT_EXIST = "Error: V4 holdout does not exist. Development set only."


def run_v4_product_cli(args: Namespace, split: EvaluationSplit) -> int:
    if split is EvaluationSplit.HOLDOUT or bool(getattr(args, "authorize_holdout", False)):
        print(V4_HOLDOUT_DOES_NOT_EXIST, file=sys.stderr)
        return 2
    try:
        assert_no_v4_holdout()
        cases = load_v4_development_cases()
    except Exception as exc:
        print(f"Error: {type(exc).__name__}.", file=sys.stderr)
        return 2
    preflight_only = bool(getattr(args, "preflight", False))
    authorize_preflight = bool(getattr(args, "authorize_preflight", False))
    if not authorize_preflight:
        print(
            "Error: v4-product-eval-2 live provider use requires --authorize-preflight.",
            file=sys.stderr,
        )
        return 2
    output = Path(
        args.output or (DEFAULT_PREFLIGHT_OUTPUT if preflight_only else DEFAULT_EVAL2_OUTPUT)
    )
    if args.resume and preflight_only:
        print("Error: --resume cannot be combined with --preflight.", file=sys.stderr)
        return 2
    if args.resume and not output.is_file():
        print("Error: --resume requires an existing report file.", file=sys.stderr)
        return 2
    dataset_fingerprint = v4_dataset_fingerprint(cases)
    previous = None
    if args.resume:
        existing = output.read_text(encoding="utf-8")
        if is_closed_run1_report(json.loads(existing)):
            print("Error: closed Run 1 evidence cannot be resumed as eval-2.", file=sys.stderr)
            return 2
        previous = V4ProductEvaluationReport.model_validate_json(existing)
    if output.is_file() and not args.resume:
        try:
            refuse_eval2_write_over_run1(output, output.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"Error: {exc}.", file=sys.stderr)
            return 2
    settings = load_settings()
    knowledge_settings = load_knowledge_settings()
    agent_settings = load_agent_settings()
    clock = V4DevelopmentBusinessClock()
    branch, commit = _git_identity()
    launch_preflight_path = None
    try:
        preflight = ProviderPreflight(settings, agent_settings).run()
        if preflight_only:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(preflight.model_dump_json(indent=2), encoding="utf-8")
            print(f"mode=v4-product kind=provider_preflight scored={preflight.scored}")
            print(f"completed={preflight.completed} report={output}")
            return 0 if preflight.completed else 3
        launch_preflight_path = persist_launch_preflight_result(preflight)
        require_successful_preflight(preflight)
        with isolated_evaluation_database(source_settings=knowledge_settings) as isolated:
            fingerprints = build_fingerprints(
                dataset_fingerprint,
                baseline_data=isolated.baseline_data_fingerprint,
                agent_settings=agent_settings,
            )
            session_factory = create_knowledge_session_factory(isolated.engine)
            repository = DemoRepository()
            retrieval = KnowledgeRetrievalService(
                embedder=GeminiDocumentEmbeddingClient(settings, isolated.settings),
                repository=KnowledgeRetrievalRepository(session_factory),
                clock=clock,
            )
            dispatcher = ToolDispatcher(
                employee_service=EmployeeService(repository),
                it_service=ITService(repository),
                knowledge_service=KnowledgeQueryService(
                    retrieval=retrieval,
                    generator=GeminiGroundedGenerationClient(settings, isolated.settings),
                ),
                demo_repository=repository,
                leave_preparation_service=LeavePreparationService(EmployeeService(repository)),
            )
            agent = AgentService(
                provider=GeminiAgentClient(settings, agent_settings),
                dispatcher=dispatcher,
                clock=clock,
            )
            configuration = V4EvaluationConfiguration(
                agent_model=agent_settings.agent_model,
                agent_timeout_seconds=agent_settings.agent_timeout_seconds,
                agent_max_attempts=agent_settings.agent_max_attempts,
                trusted_evaluation_date=V4_DEVELOPMENT_BUSINESS_DATE,
                corpus_documents=12,
                corpus_chunks=42,
                holiday_rows=14,
                calendar_version="AU-VIC-2026-v1",
                fingerprints=fingerprints,
            )
            report = V4ProductEvaluationRunner(
                agent=agent,
                session_factory=isolated.session_factory,
                settings=isolated.settings,
                engine=isolated.engine,
                configuration=configuration,
                fingerprints=fingerprints,
                branch=branch,
                commit=commit,
                clock=clock,
            ).run(
                cases=cases,
                dataset_fingerprint=dataset_fingerprint,
                previous_report=previous,
                delay_seconds=args.delay_seconds,
            )
        try:
            refuse_eval2_write_over_run1(
                output,
                output.read_text(encoding="utf-8") if output.is_file() else None,
            )
        except ValueError as exc:
            print(f"Error: {exc}.", file=sys.stderr)
            return 2
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    except FailedPreflightBlocksDevelopmentRun:
        print(
            "Error: failed provider preflight prevents automatic development run start.",
            file=sys.stderr,
        )
        if launch_preflight_path is not None:
            print(f"preflight_report={launch_preflight_path}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(
            f"Error: V4 development evaluation failed safely ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 1
    print(f"mode=v4-product split=development evaluator={V4_EVALUATOR_VERSION}")
    print(f"development_set={V4_DEVELOPMENT_SET_VERSION}")
    print(f"dataset_fingerprint={report.dataset_fingerprint}")
    if launch_preflight_path is not None:
        print(f"preflight_report={launch_preflight_path}")
    print(
        f"completed={report.summary.provider_completed_count}/{report.summary.cases_total} "
        f"blocked={report.summary.provider_blocked_count} "
        f"unattempted={report.summary.cases_not_attempted_due_to_circuit_breaker} "
        f"errors={report.summary.cases_error}"
    )
    print(f"report={output}")
    if report.summary.safety_gate_failed:
        print("status=safety_gate_failed")
        return 4
    if report.summary.provider_blocked_count or report.run_stopped_early:
        print("status=provider_blocked")
        return 3
    return 0


def _git_identity() -> tuple[str, str]:
    def _run(args: list[str]) -> str:
        return subprocess.check_output(args, text=True).strip()

    try:
        return (
            _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
            _run(["git", "rev-parse", "HEAD"]),
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown", "0" * 40

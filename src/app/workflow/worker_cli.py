"""CLI entry point for the durable V4 workflow wake worker."""

import argparse

from app.config import load_knowledge_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.workflow.worker import WorkflowWorker


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Deliver durable V4 confirmation wakes.")
    parser.add_argument("--once", action="store_true", help="Claim and deliver at most one event.")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    settings = load_knowledge_settings()
    engine = create_knowledge_engine(settings)
    try:
        worker = WorkflowWorker(create_knowledge_session_factory(engine), settings)
        worker.run_loop(poll_seconds=args.poll_seconds, once=args.once)
    finally:
        engine.dispose()

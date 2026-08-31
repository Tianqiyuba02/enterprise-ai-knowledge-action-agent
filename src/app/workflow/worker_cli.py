"""CLI entry point for the internal CONFIRMED-action poller."""

import argparse

from app.config import load_knowledge_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.workflow.confirmed_poller import ConfirmedActionPoller


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Execute durable CONFIRMED V4 actions.")
    parser.add_argument("--once", action="store_true", help="Claim and execute at most one action.")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    settings = load_knowledge_settings()
    engine = create_knowledge_engine(settings)
    try:
        poller = ConfirmedActionPoller(create_knowledge_session_factory(engine), settings)
        poller.run_loop(poll_seconds=args.poll_seconds, once=args.once)
    finally:
        engine.dispose()

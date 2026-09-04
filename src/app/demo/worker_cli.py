"""M3 worker shell that adds heartbeat/maintenance without changing V4 execution."""

import argparse
import json
import logging
import time

from sqlalchemy import text

from app.config import load_knowledge_settings, load_public_demo_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.demo.leave_execution import M3AtomicConfirmedExecutor
from app.demo.service import MUTATION_LOCK_ID, DemoControlService
from app.workflow.confirmed_poller import ConfirmedActionPoller

logger = logging.getLogger(__name__)


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the guarded M3 confirmed-action worker.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--heartbeat-only",
        action="store_true",
        help="Write one readiness heartbeat without claiming an action.",
    )
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    demo_settings = load_public_demo_settings()
    if not demo_settings.enabled:
        raise SystemExit("PUBLIC_DEMO_MODE must be enabled for the demo worker")
    knowledge_settings = load_knowledge_settings()
    engine = create_knowledge_engine(knowledge_settings)
    factory = create_knowledge_session_factory(engine)
    poller = ConfirmedActionPoller(
        factory,
        knowledge_settings,
        executor=M3AtomicConfirmedExecutor(factory, knowledge_settings),
    )
    control = DemoControlService(factory, demo_settings)
    try:
        if args.heartbeat_only:
            control.heartbeat()
            logger.info('{"event":"worker_heartbeat","service":"worker","outcome":"success"}')
            return
        while True:
            outcome = "IDLE"
            try:
                control.heartbeat()
                with factory() as guard:
                    guard.execute(
                        text("SELECT pg_advisory_xact_lock_shared(:lock_id)"),
                        {"lock_id": MUTATION_LOCK_ID},
                    )
                    maintenance = guard.execute(
                        text(
                            "SELECT maintenance_mode FROM demo_runtime_state WHERE singleton_id = 1"
                        )
                    ).scalar_one_or_none()
                    if maintenance is None or maintenance:
                        outcome = "MAINTENANCE"
                    else:
                        result = poller.run_once()
                        outcome = result.outcome.value if result else "IDLE"
                    backlog_count, oldest_age_seconds = guard.execute(
                        text(
                            """
                            SELECT count(*),
                                   COALESCE(
                                       EXTRACT(EPOCH FROM (now() - min(confirmed_at))),
                                       0
                                   )::bigint
                            FROM action_revisions
                            WHERE state = 'CONFIRMED'
                            """
                        )
                    ).one()
                    guard.rollback()
            except Exception as exc:
                outcome = "FAILURE"
                logger.error(
                    json.dumps(
                        {
                            "event": "worker_poll",
                            "service": "worker",
                            "outcome": outcome,
                            "exception_type": type(exc).__name__,
                        },
                        separators=(",", ":"),
                    )
                )
            else:
                logger.info(
                    json.dumps(
                        {
                            "event": "worker_poll",
                            "service": "worker",
                            "outcome": outcome,
                            "confirmed_backlog": backlog_count,
                            "oldest_confirmed_age_seconds": oldest_age_seconds,
                        },
                        separators=(",", ":"),
                    )
                )
            if args.once:
                return
            time.sleep(args.poll_seconds)
    finally:
        engine.dispose()


if __name__ == "__main__":
    run()

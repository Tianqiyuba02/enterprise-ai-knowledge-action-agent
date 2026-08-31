"""Internal CONFIRMED-action poller. No outbox, lease, or LangGraph wake."""

from __future__ import annotations

import time

from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings
from app.workflow.atomic_execution import AtomicConfirmedExecutor, AtomicExecutionResult


class ConfirmedActionPoller:
    """Poll CONFIRMED actions and execute each in its own PostgreSQL transaction."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: KnowledgeSettings | None = None,
        *,
        executor: AtomicConfirmedExecutor | None = None,
        worker_id: str = "confirmed-poller",
    ) -> None:
        self._settings = settings or load_knowledge_settings()
        self._executor = executor or AtomicConfirmedExecutor(
            session_factory,
            self._settings,
            worker_id=worker_id,
        )

    def run_once(self) -> AtomicExecutionResult | None:
        result = self._executor.execute_one()
        if result.outcome.value == "IDLE":
            return None
        return result

    def run_loop(self, *, poll_seconds: float = 1.0, once: bool = False) -> None:
        while True:
            self.run_once()
            if once:
                return
            time.sleep(poll_seconds)

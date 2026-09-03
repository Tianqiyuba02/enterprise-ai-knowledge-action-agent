"""M3 public-demo control plane: quotas, readiness, heartbeat, and private reset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import PublicDemoSettings
from app.db.demo_models import DemoRuntimeState, DemoUsageBucket
from app.db.models import Document, DocumentChunk
from app.db.workflow_models import (
    ActionAuditEvent,
    ActionRevision,
    ActionWorkflow,
    ConfirmationChallenge,
    ITTicket,
    LeaveRequest,
)
from app.errors import DemoCapacityReachedError, DemoMaintenanceError

MUTATION_LOCK_ID: Final = 5_003_000_008
EXPECTED_MIGRATION_HEAD: Final = "0008_v5_m3_public_demo"
REQUIRED_DOCUMENT_IDENTITIES: Final = frozenset(
    {
        ("POL-HR-001", "2.0"),
        ("SOP-IT-003", "1.0"),
    }
)


@dataclass(frozen=True, slots=True)
class DemoReadiness:
    status: str
    database: bool
    migration: bool
    knowledge: bool
    maintenance: bool
    worker: bool
    worker_heartbeat_at: datetime | None
    last_successful_reset_at: datetime | None
    document_count: int
    chunk_count: int


class DemoControlService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: PublicDemoSettings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    def consume(self, *, visitor_id: str | None, metric: str, amount: int = 1) -> None:
        """Reserve visitor/global allowance atomically in one transaction."""

        limits: list[tuple[str, str, int]] = []
        if metric == "assistant":
            if not visitor_id:
                raise DemoCapacityReachedError
            limits.extend(
                [
                    ("visitor", visitor_id, self._settings.visitor_assistant_daily_limit),
                    ("global", "all", self._settings.global_assistant_daily_limit),
                ]
            )
        elif metric == "action_prepare":
            if not visitor_id:
                raise DemoCapacityReachedError
            limits.append(("visitor", visitor_id, self._settings.visitor_action_daily_limit))
        elif metric == "revision":
            if not visitor_id:
                raise DemoCapacityReachedError
            limits.append(("visitor", visitor_id, self._settings.visitor_revision_daily_limit))
        elif metric == "execution":
            limits.append(("global", "all", self._settings.global_execution_daily_limit))
        elif metric == "provider_operation":
            limits.append(("global", "all", self._settings.global_provider_daily_limit))
        else:
            raise ValueError("unsupported demo usage metric")

        today = datetime.now(UTC).date()
        with self._session_factory() as session:
            self._assert_available(session)
            for scope, scope_key, limit in limits:
                if amount > limit:
                    session.rollback()
                    raise DemoCapacityReachedError
                row = session.execute(
                    text(
                        """
                        INSERT INTO demo_usage_buckets
                            (bucket_date, scope, scope_key, metric, usage_count, token_count)
                        VALUES (:bucket_date, :scope, :scope_key, :metric, :amount, 0)
                        ON CONFLICT (bucket_date, scope, scope_key, metric)
                        DO UPDATE SET usage_count = demo_usage_buckets.usage_count + :amount,
                                      updated_at = now()
                        WHERE demo_usage_buckets.usage_count + :amount <= :limit
                        RETURNING usage_count
                        """
                    ),
                    {
                        "bucket_date": today,
                        "scope": scope,
                        "scope_key": scope_key,
                        "metric": metric,
                        "amount": amount,
                        "limit": limit,
                    },
                ).scalar_one_or_none()
                if row is None or row > limit:
                    session.rollback()
                    raise DemoCapacityReachedError
            session.commit()

    def add_provider_tokens(self, token_count: int | None) -> None:
        if token_count is None or token_count <= 0:
            return
        with self._session_factory() as session:
            session.execute(
                update(DemoUsageBucket)
                .where(
                    DemoUsageBucket.bucket_date == datetime.now(UTC).date(),
                    DemoUsageBucket.scope == "global",
                    DemoUsageBucket.scope_key == "all",
                    DemoUsageBucket.metric == "provider_operation",
                )
                .values(
                    token_count=DemoUsageBucket.token_count + token_count,
                    updated_at=func.now(),
                )
            )
            session.commit()

    def heartbeat(self) -> None:
        with self._session_factory() as session:
            session.execute(
                update(DemoRuntimeState)
                .where(DemoRuntimeState.singleton_id == 1)
                .values(worker_heartbeat_at=func.now(), updated_at=func.now())
            )
            session.commit()

    def readiness(self) -> DemoReadiness:
        try:
            with self._session_factory() as session:
                state = session.get(DemoRuntimeState, 1)
                migration = session.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
                document_count = session.scalar(select(func.count()).select_from(Document)) or 0
                chunk_count = session.scalar(select(func.count()).select_from(DocumentChunk)) or 0
                document_identities = {
                    (doc_code, version)
                    for doc_code, version in session.execute(
                        select(Document.doc_code, Document.version)
                    )
                }
                now = datetime.now(UTC)
                worker_ok = bool(
                    state
                    and state.worker_heartbeat_at
                    and now - state.worker_heartbeat_at
                    <= timedelta(seconds=self._settings.worker_stale_seconds)
                )
                maintenance = bool(state and state.maintenance_mode)
                knowledge_ok = (
                    document_count == self._settings.expected_document_count
                    and chunk_count == self._settings.expected_chunk_count
                    and document_identities >= REQUIRED_DOCUMENT_IDENTITIES
                )
                migration_ok = migration == EXPECTED_MIGRATION_HEAD
                ready = migration_ok and knowledge_ok and worker_ok and not maintenance
                return DemoReadiness(
                    status="ready" if ready else ("maintenance" if maintenance else "degraded"),
                    database=True,
                    migration=migration_ok,
                    knowledge=knowledge_ok,
                    maintenance=maintenance,
                    worker=worker_ok,
                    worker_heartbeat_at=state.worker_heartbeat_at if state else None,
                    last_successful_reset_at=state.last_successful_reset_at if state else None,
                    document_count=document_count,
                    chunk_count=chunk_count,
                )
        except Exception:
            return DemoReadiness("degraded", False, False, False, False, False, None, None, 0, 0)

    def reset(self) -> None:
        """Private deterministic reset. A failure deliberately leaves maintenance enabled."""

        with self._session_factory() as session:
            session.execute(
                update(DemoRuntimeState)
                .where(DemoRuntimeState.singleton_id == 1)
                .values(
                    maintenance_mode=True,
                    maintenance_started_at=func.now(),
                    updated_at=func.now(),
                )
            )
            session.commit()
        with self._session_factory() as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": MUTATION_LOCK_ID},
            )
            session.execute(delete(LeaveRequest))
            session.execute(delete(ITTicket).where(ITTicket.source_action_id.is_not(None)))
            session.execute(delete(ActionAuditEvent))
            session.execute(delete(ConfirmationChallenge))
            session.execute(delete(ActionRevision))
            session.execute(delete(ActionWorkflow))
            session.execute(
                delete(DemoUsageBucket).where(DemoUsageBucket.bucket_date < date.today())
            )
            session.execute(text("SELECT setval('it_ticket_number_seq', 3001, false)"))
            mutable_tickets = session.scalar(
                select(func.count())
                .select_from(ITTicket)
                .where(ITTicket.source_action_id.is_not(None))
            )
            actions = session.scalar(select(func.count()).select_from(ActionWorkflow))
            seed_tickets = session.scalar(
                select(func.count())
                .select_from(ITTicket)
                .where(ITTicket.source_action_id.is_(None))
            )
            if mutable_tickets != 0 or actions != 0 or seed_tickets != 3:
                raise RuntimeError("demo reset baseline verification failed")
            session.execute(
                update(DemoRuntimeState)
                .where(DemoRuntimeState.singleton_id == 1)
                .values(
                    maintenance_mode=False,
                    maintenance_started_at=None,
                    last_successful_reset_at=func.now(),
                    updated_at=func.now(),
                )
            )
            session.commit()

    @staticmethod
    def _assert_available(session: Session) -> None:
        state = session.get(DemoRuntimeState, 1)
        if state is None or state.maintenance_mode:
            raise DemoMaintenanceError

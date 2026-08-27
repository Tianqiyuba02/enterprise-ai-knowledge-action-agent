"""Outbox enqueue and SKIP LOCKED claim primitives. No running worker."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Select, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.workflow_models import WorkflowOutbox
from app.workflow.domain import V4_REVISION, OutboxEventType
from app.workflow.errors import DuplicateWorkflowEventError


@dataclass(frozen=True, slots=True)
class NewOutboxEvent:
    event_key: str
    action_id: UUID
    event_type: OutboxEventType
    available_at: datetime
    revision: int = V4_REVISION


class OutboxRepository:
    """Persist durable event identities for future competing consumers."""

    def enqueue(self, session: Session, spec: NewOutboxEvent) -> WorkflowOutbox:
        existing = self.get_by_event_key(session, spec.event_key)
        if existing is not None:
            raise DuplicateWorkflowEventError("outbox event identity already exists")
        row = WorkflowOutbox(
            event_id=uuid4(),
            event_key=spec.event_key,
            action_id=spec.action_id,
            revision=spec.revision,
            event_type=spec.event_type.value,
            available_at=spec.available_at,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise DuplicateWorkflowEventError("outbox event identity already exists") from exc
        return row

    def get_by_event_key(self, session: Session, event_key: str) -> WorkflowOutbox | None:
        return session.execute(
            select(WorkflowOutbox).where(WorkflowOutbox.event_key == event_key)
        ).scalar_one_or_none()

    def claimable_statement(
        self,
        *,
        now: datetime,
        limit: int = 1,
    ) -> Select[tuple[WorkflowOutbox]]:
        return (
            select(WorkflowOutbox)
            .where(
                WorkflowOutbox.delivered_at.is_(None),
                WorkflowOutbox.available_at <= now,
                or_(
                    WorkflowOutbox.locked_until.is_(None),
                    WorkflowOutbox.locked_until <= now,
                ),
            )
            .order_by(WorkflowOutbox.available_at, WorkflowOutbox.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

    def claim_ready(
        self,
        session: Session,
        *,
        now: datetime,
        locked_by: str,
        lock_for: timedelta,
        limit: int = 1,
    ) -> tuple[WorkflowOutbox, ...]:
        rows = session.execute(self.claimable_statement(now=now, limit=limit)).scalars().all()
        locked_until = now + lock_for
        for row in rows:
            row.locked_by = locked_by
            row.locked_until = locked_until
            row.attempt_count += 1
        session.flush()
        return tuple(rows)

    def mark_delivered(self, session: Session, event_id: UUID, *, delivered_at: datetime) -> None:
        row = session.get(WorkflowOutbox, event_id)
        if row is None:
            return
        row.delivered_at = delivered_at
        row.locked_by = None
        row.locked_until = None
        session.flush()

    def release(
        self,
        session: Session,
        event_id: UUID,
        *,
        failure_kind: str | None = None,
        available_at: datetime | None = None,
    ) -> None:
        row = session.get(WorkflowOutbox, event_id)
        if row is None:
            return
        row.locked_by = None
        row.locked_until = None
        row.last_failure_kind = failure_kind
        if available_at is not None:
            row.available_at = available_at
        session.flush()

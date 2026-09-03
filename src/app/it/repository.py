"""PostgreSQL persistence for owner-scoped M2 IT support tickets."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.workflow_models import IT_TICKET_NUMBER_SEQUENCE, ITTicket
from app.it.domain import ITTicketCategory, ITTicketStatus, ITTicketUrgency


@dataclass(frozen=True, slots=True)
class NewITTicket:
    employee_id: str
    owner_subject_id: str
    category: ITTicketCategory
    summary: str
    description: str
    urgency: ITTicketUrgency
    status: ITTicketStatus
    source_action_id: UUID
    source_action_revision: int
    created_at: datetime


class ITTicketRepository:
    """Keep ticket reads and writes on the caller's explicit transaction."""

    def list_owned(
        self,
        session: Session,
        *,
        employee_id: str,
        owner_subject_id: str,
    ) -> tuple[ITTicket, ...]:
        return tuple(
            session.scalars(
                select(ITTicket)
                .where(
                    ITTicket.employee_id == employee_id,
                    ITTicket.owner_subject_id == owner_subject_id,
                )
                .order_by(ITTicket.created_at.desc(), ITTicket.ticket_number.desc())
            )
        )

    def find_owned(
        self,
        session: Session,
        *,
        ticket_id: str,
        employee_id: str,
        owner_subject_id: str,
    ) -> ITTicket | None:
        return session.scalar(
            select(ITTicket).where(
                ITTicket.ticket_id == ticket_id,
                ITTicket.employee_id == employee_id,
                ITTicket.owner_subject_id == owner_subject_id,
            )
        )

    def find_by_source_action(self, session: Session, action_id: UUID) -> ITTicket | None:
        return session.scalar(select(ITTicket).where(ITTicket.source_action_id == action_id))

    def persist(self, session: Session, spec: NewITTicket) -> ITTicket:
        ticket_number = int(
            session.execute(select(IT_TICKET_NUMBER_SEQUENCE.next_value())).scalar_one()
        )
        now = spec.created_at
        row = ITTicket(
            ticket_number=ticket_number,
            ticket_id=f"TKT-{ticket_number}",
            employee_id=spec.employee_id,
            owner_subject_id=spec.owner_subject_id,
            category=spec.category.value,
            summary=spec.summary,
            description=spec.description,
            urgency=spec.urgency.value,
            status=spec.status.value,
            source_action_id=spec.source_action_id,
            source_action_revision=spec.source_action_revision,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row

"""Ownership-enforced runtime and deterministic-test IT ticket reads."""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.errors import PortalReadUnavailableError, TicketNotFoundError
from app.identity import AuthenticatedEmployeeContext
from app.it.domain import ITTicketRecord
from app.it.repository import ITTicketRepository
from app.repositories.demo import DemoRepository, TicketRecord


class ITService:
    def __init__(
        self,
        repository: DemoRepository | None = None,
        *,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        if (repository is None) == (session_factory is None):
            raise ValueError("configure exactly one IT ticket source")
        self._demo_repository = repository
        self._session_factory = session_factory
        self._ticket_repository = ITTicketRepository()

    def get_my_ticket(
        self,
        ticket_id: str,
        context: AuthenticatedEmployeeContext,
    ) -> TicketRecord | ITTicketRecord:
        if self._demo_repository is not None:
            ticket = self._demo_repository.find_ticket(ticket_id, context.employee_id)
            if ticket is None:
                raise TicketNotFoundError
            return ticket
        subject_id = _require_subject(context)
        assert self._session_factory is not None
        try:
            with self._session_factory() as session:
                row = self._ticket_repository.find_owned(
                    session,
                    ticket_id=ticket_id,
                    employee_id=context.employee_id,
                    owner_subject_id=subject_id,
                )
                ticket = None if row is None else _record(row)
        except SQLAlchemyError as exc:
            raise PortalReadUnavailableError from exc
        if ticket is None:
            raise TicketNotFoundError
        return ticket

    def list_my_tickets(
        self,
        context: AuthenticatedEmployeeContext,
    ) -> tuple[TicketRecord | ITTicketRecord, ...]:
        if self._demo_repository is not None:
            return self._demo_repository.list_tickets(context.employee_id)
        subject_id = _require_subject(context)
        assert self._session_factory is not None
        try:
            with self._session_factory() as session:
                return tuple(
                    _record(row)
                    for row in self._ticket_repository.list_owned(
                        session,
                        employee_id=context.employee_id,
                        owner_subject_id=subject_id,
                    )
                )
        except SQLAlchemyError as exc:
            raise PortalReadUnavailableError from exc


def _require_subject(context: AuthenticatedEmployeeContext) -> str:
    if not context.subject_id:
        raise TicketNotFoundError
    return context.subject_id


def _record(row) -> ITTicketRecord:
    return ITTicketRecord(
        ticket_id=row.ticket_id,
        employee_id=row.employee_id,
        owner_subject_id=row.owner_subject_id,
        category=row.category,
        summary=row.summary,
        description=row.description,
        urgency=row.urgency,
        status=row.status,
        source_action_id=row.source_action_id,
        source_action_revision=row.source_action_revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )

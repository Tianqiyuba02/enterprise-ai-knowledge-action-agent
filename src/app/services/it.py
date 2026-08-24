"""Ownership-enforced IT ticket read service."""

from app.errors import TicketNotFoundError
from app.identity import AuthenticatedEmployeeContext
from app.repositories.demo import DemoRepository, TicketRecord


class ITService:
    def __init__(self, repository: DemoRepository) -> None:
        self._repository = repository

    def get_my_ticket(self, ticket_id: str, context: AuthenticatedEmployeeContext) -> TicketRecord:
        ticket = self._repository.find_ticket(ticket_id, context.employee_id)
        if ticket is None:
            raise TicketNotFoundError
        return ticket

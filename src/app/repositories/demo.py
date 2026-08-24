"""Small deterministic in-memory repository containing synthetic V1 demo data."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class EmployeeRecord:
    employee_id: str
    full_name: str
    work_email: str
    location: str
    employment_type: str
    hours_per_day: float
    work_days: tuple[str, ...]
    timezone: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class LeaveBalanceRecord:
    employee_id: str
    leave_type: str
    balance_hours: float
    as_of_date: date


@dataclass(frozen=True, slots=True)
class TicketRecord:
    ticket_id: str
    employee_id: str
    category: str
    summary: str
    description: str
    urgency: str
    status: str
    created_at: datetime
    updated_at: datetime


class DemoRepository:
    """Read-only seeded data with ownership-scoped lookup methods."""

    def __init__(self) -> None:
        self._employees = {
            "EMP-1001": EmployeeRecord(
                employee_id="EMP-1001",
                full_name="Alex Morgan",
                work_email="alex.morgan@example.test",
                location="Melbourne",
                employment_type="permanent",
                hours_per_day=7.6,
                work_days=("monday", "tuesday", "wednesday", "thursday", "friday"),
                timezone="Australia/Melbourne",
                is_active=True,
            ),
            "EMP-1002": EmployeeRecord(
                employee_id="EMP-1002",
                full_name="Sam Lee",
                work_email="sam.lee@example.test",
                location="Melbourne",
                employment_type="part_time",
                hours_per_day=6.0,
                work_days=("monday", "tuesday", "wednesday", "thursday"),
                timezone="Australia/Melbourne",
                is_active=True,
            ),
        }
        self._leave_balances = (
            LeaveBalanceRecord("EMP-1001", "annual", 76.0, date(2026, 8, 24)),
            LeaveBalanceRecord("EMP-1001", "personal", 38.0, date(2026, 8, 24)),
            LeaveBalanceRecord("EMP-1002", "annual", 48.0, date(2026, 8, 24)),
            LeaveBalanceRecord("EMP-1002", "personal", 24.0, date(2026, 8, 24)),
        )
        self._tickets = {
            "TKT-1001": TicketRecord(
                ticket_id="TKT-1001",
                employee_id="EMP-1001",
                category="access",
                summary="Payroll portal access",
                description="Unable to sign in to the synthetic payroll portal.",
                urgency="medium",
                status="open",
                created_at=datetime.fromisoformat("2026-08-20T09:30:00+10:00"),
                updated_at=datetime.fromisoformat("2026-08-20T11:15:00+10:00"),
            ),
            "TKT-1002": TicketRecord(
                ticket_id="TKT-1002",
                employee_id="EMP-1001",
                category="hardware",
                summary="External monitor flicker",
                description="Synthetic workstation monitor flickers after waking.",
                urgency="low",
                status="resolved",
                created_at=datetime.fromisoformat("2026-08-10T14:00:00+10:00"),
                updated_at=datetime.fromisoformat("2026-08-12T16:40:00+10:00"),
            ),
            "TKT-2001": TicketRecord(
                ticket_id="TKT-2001",
                employee_id="EMP-1002",
                category="software",
                summary="Video meeting update",
                description="Synthetic meeting application requires an update.",
                urgency="low",
                status="in_progress",
                created_at=datetime.fromisoformat("2026-08-22T10:10:00+10:00"),
                updated_at=datetime.fromisoformat("2026-08-23T08:45:00+10:00"),
            ),
        }

    def get_employee(self, employee_id: str) -> EmployeeRecord | None:
        return self._employees.get(employee_id)

    def list_leave_balances(self, employee_id: str) -> tuple[LeaveBalanceRecord, ...]:
        return tuple(
            balance for balance in self._leave_balances if balance.employee_id == employee_id
        )

    def find_ticket(self, ticket_id: str, employee_id: str) -> TicketRecord | None:
        """Return a ticket only when both its ID and trusted owner match."""

        ticket = self._tickets.get(ticket_id)
        if ticket is None or ticket.employee_id != employee_id:
            return None
        return ticket

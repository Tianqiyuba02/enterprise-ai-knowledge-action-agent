"""Deterministic, read-only annual-leave draft calculation."""

from datetime import date, timedelta
from decimal import Decimal

from app.agent.leave_models import (
    LeavePreparationStatus,
    LeaveRequestDraft,
    PrepareLeaveRequestArguments,
)
from app.identity import AuthenticatedEmployeeContext
from app.services.employee import EmployeeService

_HOUR_QUANTUM = Decimal("0.01")


class LeavePreparationError(RuntimeError):
    """Base class for controlled deterministic preparation failures."""


class LeavePreparationUnavailableError(LeavePreparationError):
    """Raised when trusted employee schedule or balance data is unavailable."""


class LeavePreparationService:
    """Calculate one non-persistent annual-leave draft from trusted employee data."""

    def __init__(
        self,
        employee_service: EmployeeService,
    ) -> None:
        self._employee_service = employee_service

    def prepare(
        self,
        arguments: PrepareLeaveRequestArguments,
        context: AuthenticatedEmployeeContext,
    ) -> LeaveRequestDraft:
        profile = self._employee_service.get_my_profile(context)
        balances = self._employee_service.get_my_leave_balances(context)
        annual_balance = next(
            (balance for balance in balances if balance.leave_type == "annual"),
            None,
        )
        if annual_balance is None:
            raise LeavePreparationUnavailableError(
                "Annual leave balance is unavailable for preparation."
            )

        scheduled_days = _count_scheduled_work_days(
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            work_days=profile.work_days,
        )
        hours_per_day = Decimal(str(profile.hours_per_day))
        requested_hours = (Decimal(scheduled_days) * hours_per_day).quantize(_HOUR_QUANTUM)
        current_balance = Decimal(str(annual_balance.balance_hours)).quantize(_HOUR_QUANTUM)
        projected_balance = (current_balance - requested_hours).quantize(_HOUR_QUANTUM)

        if scheduled_days == 0:
            status = LeavePreparationStatus.NO_SCHEDULED_WORKDAYS
        elif requested_hours > current_balance:
            status = LeavePreparationStatus.INSUFFICIENT_BALANCE
        else:
            status = LeavePreparationStatus.READY

        return LeaveRequestDraft(
            leave_type="annual",
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            scheduled_work_days=scheduled_days,
            requested_hours=requested_hours,
            current_balance_hours=current_balance,
            projected_balance_hours=projected_balance,
            preparation_status=status,
            reason=arguments.reason,
            public_holiday_check_required=scheduled_days > 0,
            non_executing=True,
        )


def _count_scheduled_work_days(
    *,
    start_date: date,
    end_date: date,
    work_days: tuple[str, ...],
) -> int:
    scheduled_weekdays = {day.lower() for day in work_days}
    current = start_date
    count = 0
    while current <= end_date:
        if current.strftime("%A").lower() in scheduled_weekdays:
            count += 1
        current += timedelta(days=1)
    return count

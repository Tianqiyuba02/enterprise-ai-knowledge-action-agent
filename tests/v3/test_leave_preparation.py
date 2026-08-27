from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.agent.leave_models import (
    LeavePreparationStatus,
    PrepareLeaveRequestArguments,
)
from app.identity import AuthenticatedEmployeeContext
from app.repositories.demo import DemoRepository
from app.services.employee import EmployeeService
from app.services.leave_preparation import LeavePreparationService

ALEX = AuthenticatedEmployeeContext(employee_id="EMP-1001")
SAM = AuthenticatedEmployeeContext(employee_id="EMP-1002")


@pytest.fixture
def service() -> LeavePreparationService:
    repository = DemoRepository()
    return LeavePreparationService(EmployeeService(repository))


def _arguments(
    start_date: str,
    end_date: str,
    *,
    reason: str | None = None,
) -> PrepareLeaveRequestArguments:
    return PrepareLeaveRequestArguments.model_validate(
        {
            "leave_type": "annual",
            "start_date": start_date,
            "end_date": end_date,
            "reason": reason,
        }
    )


def test_one_normal_workday_uses_trusted_hours_and_balance(
    service: LeavePreparationService,
) -> None:
    draft = service.prepare(_arguments("2026-08-28", "2026-08-28"), ALEX)

    assert draft.scheduled_work_days == 1
    assert draft.requested_hours == Decimal("7.60")
    assert draft.current_balance_hours == Decimal("76.00")
    assert draft.projected_balance_hours == Decimal("68.40")
    assert draft.preparation_status is LeavePreparationStatus.READY
    assert draft.public_holiday_check_required is True
    assert draft.non_executing is True
    serialized = draft.model_dump(mode="json")
    assert serialized["requested_hours"] == 7.6
    assert serialized["current_balance_hours"] == 76.0
    assert serialized["projected_balance_hours"] == 68.4


def test_monday_to_friday_range_calculates_five_days(
    service: LeavePreparationService,
) -> None:
    draft = service.prepare(_arguments("2026-08-31", "2026-09-04"), ALEX)

    assert draft.scheduled_work_days == 5
    assert draft.requested_hours == Decimal("38.00")
    assert draft.projected_balance_hours == Decimal("38.00")


def test_weekend_is_excluded_from_trusted_schedule(
    service: LeavePreparationService,
) -> None:
    draft = service.prepare(_arguments("2026-08-28", "2026-08-31"), ALEX)

    assert draft.scheduled_work_days == 2
    assert draft.requested_hours == Decimal("15.20")


def test_employee_specific_schedule_and_zero_workdays(
    service: LeavePreparationService,
) -> None:
    friday = service.prepare(_arguments("2026-08-28", "2026-08-28"), SAM)
    monday = service.prepare(_arguments("2026-08-31", "2026-08-31"), SAM)

    assert friday.scheduled_work_days == 0
    assert friday.requested_hours == Decimal("0.00")
    assert friday.preparation_status is LeavePreparationStatus.NO_SCHEDULED_WORKDAYS
    assert friday.public_holiday_check_required is False
    assert monday.scheduled_work_days == 1
    assert monday.requested_hours == Decimal("6.00")
    assert monday.current_balance_hours == Decimal("48.00")
    assert monday.projected_balance_hours == Decimal("42.00")


def test_insufficient_balance_is_typed_without_approval_claim(
    service: LeavePreparationService,
) -> None:
    draft = service.prepare(_arguments("2026-08-26", "2026-09-25"), ALEX)

    assert draft.requested_hours > draft.current_balance_hours
    assert draft.projected_balance_hours < 0
    assert draft.preparation_status is LeavePreparationStatus.INSUFFICIENT_BALANCE


@pytest.mark.parametrize(
    "payload",
    [
        {
            "leave_type": "annual",
            "start_date": "2026-09-02",
            "end_date": "2026-09-01",
        },
        {
            "leave_type": "annual",
            "start_date": "2026-08-26",
            "end_date": "2026-09-26",
        },
        {
            "leave_type": "personal",
            "start_date": "2026-08-28",
            "end_date": "2026-08-28",
        },
        {
            "leave_type": "annual",
            "start_date": "next Friday",
            "end_date": "next Friday",
        },
        {
            "leave_type": "annual",
            "start_date": "2026-08-28",
            "end_date": "2026-08-28",
            "reason": "",
        },
        {
            "leave_type": "annual",
            "start_date": "2026-08-28",
            "end_date": "2026-08-28",
            "reason": "x" * 501,
        },
        {
            "leave_type": "annual",
            "start_date": "2026-08-28",
            "end_date": "2026-08-28",
            "employee_id": "EMP-1002",
        },
        {
            "leave_type": "annual",
            "start_date": "2026-08-28",
            "end_date": "2026-08-28",
            "hours_per_day": 1,
            "current_balance_hours": 999,
        },
    ],
)
def test_prepare_arguments_are_strict_and_reject_model_controlled_trust_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PrepareLeaveRequestArguments.model_validate(payload)


def test_prepare_arguments_accept_annual_and_reject_non_annual_leave_types() -> None:
    accepted = PrepareLeaveRequestArguments.model_validate(
        {
            "leave_type": "annual",
            "start_date": "2026-08-28",
            "end_date": "2026-08-28",
        }
    )

    assert accepted.leave_type == "annual"

    for leave_type in ("personal", "sick", "ANNUAL", "Annual"):
        with pytest.raises(ValidationError):
            PrepareLeaveRequestArguments.model_validate(
                {
                    "leave_type": leave_type,
                    "start_date": "2026-08-28",
                    "end_date": "2026-08-28",
                }
            )


def test_preparation_is_repeatable_and_does_not_mutate_employee_data() -> None:
    repository = DemoRepository()
    employee_service = EmployeeService(repository)
    service = LeavePreparationService(employee_service)
    arguments = _arguments("2026-08-28", "2026-08-28", reason="Synthetic holiday")
    before_profile = employee_service.get_my_profile(ALEX)
    before_balances = employee_service.get_my_leave_balances(ALEX)

    first = service.prepare(arguments, ALEX)
    second = service.prepare(arguments, ALEX)

    assert first == second
    assert employee_service.get_my_profile(ALEX) == before_profile
    assert employee_service.get_my_leave_balances(ALEX) == before_balances

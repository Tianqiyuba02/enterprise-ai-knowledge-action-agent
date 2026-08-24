"""Employee profile and leave-balance read services."""

from app.errors import EmployeeNotFoundError
from app.identity import AuthenticatedEmployeeContext
from app.repositories.demo import DemoRepository, EmployeeRecord, LeaveBalanceRecord


class EmployeeService:
    def __init__(self, repository: DemoRepository) -> None:
        self._repository = repository

    def get_my_profile(self, context: AuthenticatedEmployeeContext) -> EmployeeRecord:
        employee = self._repository.get_employee(context.employee_id)
        if employee is None:
            raise EmployeeNotFoundError
        return employee

    def get_my_leave_balances(
        self, context: AuthenticatedEmployeeContext
    ) -> tuple[LeaveBalanceRecord, ...]:
        self.get_my_profile(context)
        return self._repository.list_leave_balances(context.employee_id)

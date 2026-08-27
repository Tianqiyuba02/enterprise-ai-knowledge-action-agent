"""Evaluator-only dispatcher tracing and demo-state snapshots."""

from dataclasses import dataclass
from typing import Protocol

from app.agent.models import ToolResult, ToolResultStatus
from app.evaluation.agent_models import SafeArgumentValue, ToolTraceObservation
from app.identity import AuthenticatedEmployeeContext
from app.repositories.demo import (
    DemoRepository,
    EmployeeRecord,
    LeaveBalanceRecord,
    TicketRecord,
)

_FIXTURE_EMPLOYEE_IDS = ("EMP-1001", "EMP-1002")
_FIXTURE_TICKETS = (
    ("TKT-1001", "EMP-1001"),
    ("TKT-1002", "EMP-1001"),
    ("TKT-2001", "EMP-1002"),
)


class DispatcherLike(Protocol):
    def dispatch(
        self,
        *,
        name: object,
        arguments: object,
        context: AuthenticatedEmployeeContext,
    ) -> ToolResult: ...


class RecordingToolDispatcher:
    """Record safe observations while preserving the real dispatcher result unchanged."""

    def __init__(
        self,
        inner: DispatcherLike,
        expected_context: AuthenticatedEmployeeContext,
    ) -> None:
        self._inner = inner
        self._expected_context = expected_context
        self._observations: list[ToolTraceObservation] = []

    @property
    def observations(self) -> tuple[ToolTraceObservation, ...]:
        return tuple(self._observations)

    def dispatch(
        self,
        *,
        name: object,
        arguments: object,
        context: AuthenticatedEmployeeContext,
    ) -> ToolResult:
        safe_arguments = _safe_arguments(arguments)
        employee_id_argument_present = "employee_id" in safe_arguments
        try:
            result = self._inner.dispatch(
                name=name,
                arguments=arguments,
                context=context,
            )
        except Exception:
            self._observations.append(
                ToolTraceObservation(
                    tool_name=_safe_name(name),
                    arguments=safe_arguments,
                    result_status=ToolResultStatus.INTERNAL_ERROR,
                    trusted_context_valid=context == self._expected_context,
                    employee_id_argument_present=employee_id_argument_present,
                )
            )
            raise
        self._observations.append(
            ToolTraceObservation(
                tool_name=result.tool_name,
                arguments=safe_arguments,
                result_status=result.status,
                trusted_context_valid=context == self._expected_context,
                employee_id_argument_present=employee_id_argument_present,
                data_kind=getattr(result.data, "kind", None),
            )
        )
        return result


@dataclass(frozen=True, slots=True)
class DemoStateSnapshot:
    employees: tuple[EmployeeRecord | None, ...]
    leave_balances: tuple[LeaveBalanceRecord, ...]
    tickets: tuple[TicketRecord | None, ...]


def snapshot_demo_state(repository: DemoRepository) -> DemoStateSnapshot:
    return DemoStateSnapshot(
        employees=tuple(
            repository.get_employee(employee_id) for employee_id in _FIXTURE_EMPLOYEE_IDS
        ),
        leave_balances=tuple(
            balance
            for employee_id in _FIXTURE_EMPLOYEE_IDS
            for balance in repository.list_leave_balances(employee_id)
        ),
        tickets=tuple(
            repository.find_ticket(ticket_id, employee_id)
            for ticket_id, employee_id in _FIXTURE_TICKETS
        ),
    )


def _safe_arguments(arguments: object) -> dict[str, SafeArgumentValue]:
    if not isinstance(arguments, dict):
        return {}
    safe: dict[str, SafeArgumentValue] = {}
    for key, value in arguments.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            continue
        if isinstance(value, str):
            safe[key] = value[:4_000]
        elif isinstance(value, bool) or value is None or isinstance(value, int | float):
            safe[key] = value
        else:
            safe[key] = "[non-scalar]"
    return safe


def _safe_name(name: object) -> str:
    if isinstance(name, str) and 0 < len(name) <= 64 and name.isprintable():
        return name
    return "unknown_tool"

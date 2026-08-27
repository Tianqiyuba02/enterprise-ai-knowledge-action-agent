"""Frozen V4 workflow vocabulary. Storage uses TEXT plus CHECK, not native ENUMs."""

from collections.abc import Iterable
from enum import StrEnum
from typing import Final

V4_REVISION: Final = 1


class WorkflowState(StrEnum):
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    EXECUTING = "EXECUTING"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    RECONCILING = "RECONCILING"
    SUCCEEDED = "SUCCEEDED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    STALE = "STALE"


NON_TERMINAL_WORKFLOW_STATES: Final = frozenset(
    {
        WorkflowState.AWAITING_CONFIRMATION,
        WorkflowState.CONFIRMED,
        WorkflowState.EXECUTING,
        WorkflowState.UNKNOWN_OUTCOME,
        WorkflowState.RECONCILING,
    }
)
TERMINAL_WORKFLOW_STATES: Final = frozenset(
    {
        WorkflowState.SUCCEEDED,
        WorkflowState.EXECUTION_FAILED,
        WorkflowState.CANCELLED,
        WorkflowState.EXPIRED,
        WorkflowState.STALE,
    }
)
ALL_WORKFLOW_STATES: Final = NON_TERMINAL_WORKFLOW_STATES | TERMINAL_WORKFLOW_STATES


class ActionType(StrEnum):
    SUBMIT_ANNUAL_LEAVE = "submit_annual_leave"


class ChallengeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"


class OutboxEventType(StrEnum):
    CONFIRMATION_COMMITTED = "confirmation_committed"
    RECONCILE_REQUESTED = "reconcile_requested"


class ExecutionOperation(StrEnum):
    SUBMIT_ANNUAL_LEAVE = "submit_annual_leave"


class ExecutionLedgerStatus(StrEnum):
    RESERVED = "RESERVED"
    LEASED = "LEASED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"


class ActorType(StrEnum):
    EMPLOYEE = "employee"
    SYSTEM = "system"
    WORKER = "worker"


class LeaveRequestStatus(StrEnum):
    SUBMITTED = "submitted"


class LeaveType(StrEnum):
    ANNUAL = "annual"


def sql_in_clause(values: Iterable[StrEnum]) -> str:
    """Render a CHECK-friendly quoted IN list from a StrEnum collection."""

    return ", ".join(f"'{member.value}'" for member in values)

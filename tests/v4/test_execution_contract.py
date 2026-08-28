import inspect
from uuid import uuid4

import pytest

from app.workflow.errors import ExecutionFenceError, WorkflowIntegrityError
from app.workflow.execution import (
    ExecutionPermit,
    ExecutionReservationService,
    permit_for_caller,
)
from app.workflow.executor import LeaveSubmissionExecutor


def test_reservation_and_submit_accept_only_server_inputs() -> None:
    reserve = inspect.signature(ExecutionReservationService.reserve).parameters
    reload_permit = inspect.signature(ExecutionReservationService.reload_permit).parameters
    submit = inspect.signature(LeaveSubmissionExecutor.submit).parameters
    assert list(reserve) == ["self", "action_id", "revision", "worker_id"]
    assert list(reload_permit) == ["self", "action_id", "revision", "worker_id"]
    assert list(submit) == ["self", "permit"]
    assert ExecutionPermit.__slots__ == (
        "execution_key",
        "lease_owner_id",
        "lease_generation",
        "action_id",
        "revision",
    )


class _Ledger:
    execution_key = "aa" * 32
    lease_owner_id = "workflow-worker:a"
    lease_generation = 2
    action_id = uuid4()
    revision = 1


def test_permit_for_caller_rejects_other_owners() -> None:
    owned = permit_for_caller(_Ledger(), "workflow-worker:a")
    assert owned.lease_owner_id == "workflow-worker:a"
    assert owned.lease_generation == 2
    with pytest.raises(ExecutionFenceError, match="does not own"):
        permit_for_caller(_Ledger(), "workflow-worker:b")
    with pytest.raises(WorkflowIntegrityError, match="worker_id is required"):
        permit_for_caller(_Ledger(), "")

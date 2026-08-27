import inspect

from app.workflow.execution import ExecutionPermit, ExecutionReservationService
from app.workflow.executor import LeaveSubmissionExecutor


def test_reservation_and_submit_accept_only_server_inputs() -> None:
    reserve = inspect.signature(ExecutionReservationService.reserve).parameters
    submit = inspect.signature(LeaveSubmissionExecutor.submit).parameters
    assert list(reserve) == ["self", "action_id", "revision", "worker_id"]
    assert list(submit) == ["self", "permit"]
    assert ExecutionPermit.__slots__ == (
        "execution_key",
        "lease_owner_id",
        "lease_generation",
        "action_id",
        "revision",
    )

from uuid import UUID

from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from app.workflow.atomic_execution import (
    FailureScope,
    _IntegrityConflict,
    _TransientExecution,
    classify_execution_failure,
)
from app.workflow.errors import WorkflowIntegrityError
from app.workflow.occupancy import SOURCE_ACTION_UNIQUE_CONSTRAINT


class _Diag:
    def __init__(self, constraint_name: str) -> None:
        self.constraint_name = constraint_name


class _Orig(Exception):
    def __init__(self, constraint_name: str) -> None:
        self.diag = _Diag(constraint_name)
        super().__init__(constraint_name)


def test_operational_error_is_infrastructure() -> None:
    assert (
        classify_execution_failure(OperationalError("SELECT 1", {}, Exception("db-down")))
        is FailureScope.INFRASTRUCTURE
    )


def test_generic_sqlalchemy_error_is_infrastructure() -> None:
    assert (
        classify_execution_failure(SQLAlchemyError("session-broken")) is FailureScope.INFRASTRUCTURE
    )


def test_action_specific_runtime_error_is_action() -> None:
    assert classify_execution_failure(RuntimeError("poison-action")) is FailureScope.ACTION


def test_workflow_integrity_error_is_action() -> None:
    assert classify_execution_failure(WorkflowIntegrityError("draft")) is FailureScope.ACTION


def test_transient_execution_is_action() -> None:
    assert (
        classify_execution_failure(
            _TransientExecution(UUID("11111111-1111-1111-1111-111111111111"))
        )
        is FailureScope.ACTION
    )


def test_plain_integrity_error_is_action() -> None:
    assert (
        classify_execution_failure(IntegrityError("INSERT", {}, Exception("check")))
        is FailureScope.ACTION
    )


def test_unique_leave_integrity_error_is_conflict() -> None:
    assert (
        classify_execution_failure(
            IntegrityError("INSERT", {}, _Orig(SOURCE_ACTION_UNIQUE_CONSTRAINT))
        )
        is FailureScope.CONFLICT
    )


def test_integrity_conflict_is_conflict() -> None:
    assert (
        classify_execution_failure(_IntegrityConflict(UUID("11111111-1111-1111-1111-111111111111")))
        is FailureScope.CONFLICT
    )


def test_classification_does_not_accept_claimed_id() -> None:
    assert classify_execution_failure.__code__.co_argcount == 1
    assert "claimed_id" not in classify_execution_failure.__code__.co_varnames

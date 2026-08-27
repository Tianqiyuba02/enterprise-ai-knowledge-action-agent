"""Execution-ledger reservation and fencing primitives. No executor call."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.workflow_models import ActionExecutionLedger
from app.workflow.domain import V4_REVISION, ExecutionLedgerStatus, ExecutionOperation
from app.workflow.errors import DuplicateExecutionReservationError, WorkflowRowNotFoundError


@dataclass(frozen=True, slots=True)
class NewExecutionReservation:
    action_id: UUID
    execution_key: str
    operation: ExecutionOperation = ExecutionOperation.SUBMIT_ANNUAL_LEAVE
    status: ExecutionLedgerStatus = ExecutionLedgerStatus.RESERVED
    revision: int = V4_REVISION
    lease_owner_id: str | None = None
    lease_generation: int = 1
    lease_expires_at: datetime | None = None


class ExecutionLedgerRepository:
    """Create and lock reservation rows for later fencing verification."""

    def create_reservation(
        self,
        session: Session,
        spec: NewExecutionReservation,
    ) -> ActionExecutionLedger:
        row = ActionExecutionLedger(
            execution_id=uuid4(),
            action_id=spec.action_id,
            revision=spec.revision,
            operation=spec.operation.value,
            execution_key=spec.execution_key,
            lease_owner_id=spec.lease_owner_id,
            lease_generation=spec.lease_generation,
            lease_expires_at=spec.lease_expires_at,
            status=spec.status.value,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise DuplicateExecutionReservationError(
                "execution reservation already exists"
            ) from exc
        return row

    def get(self, session: Session, execution_id: UUID) -> ActionExecutionLedger | None:
        return session.get(ActionExecutionLedger, execution_id)

    def get_by_execution_key(
        self,
        session: Session,
        execution_key: str,
    ) -> ActionExecutionLedger | None:
        return session.execute(
            select(ActionExecutionLedger).where(
                ActionExecutionLedger.execution_key == execution_key
            )
        ).scalar_one_or_none()

    def get_by_action(
        self,
        session: Session,
        *,
        action_id: UUID,
        revision: int = V4_REVISION,
        operation: ExecutionOperation = ExecutionOperation.SUBMIT_ANNUAL_LEAVE,
    ) -> ActionExecutionLedger | None:
        return session.execute(
            select(ActionExecutionLedger).where(
                ActionExecutionLedger.action_id == action_id,
                ActionExecutionLedger.revision == revision,
                ActionExecutionLedger.operation == operation.value,
            )
        ).scalar_one_or_none()

    def lock_reservation_statement(
        self,
        *,
        action_id: UUID,
        revision: int = V4_REVISION,
        operation: ExecutionOperation = ExecutionOperation.SUBMIT_ANNUAL_LEAVE,
    ) -> Select[tuple[ActionExecutionLedger]]:
        return (
            select(ActionExecutionLedger)
            .where(
                ActionExecutionLedger.action_id == action_id,
                ActionExecutionLedger.revision == revision,
                ActionExecutionLedger.operation == operation.value,
            )
            .with_for_update()
        )

    def lock_reservation(
        self,
        session: Session,
        *,
        action_id: UUID,
        revision: int = V4_REVISION,
        operation: ExecutionOperation = ExecutionOperation.SUBMIT_ANNUAL_LEAVE,
    ) -> ActionExecutionLedger:
        row = session.execute(
            self.lock_reservation_statement(
                action_id=action_id,
                revision=revision,
                operation=operation,
            )
        ).scalar_one_or_none()
        if row is None:
            raise WorkflowRowNotFoundError("execution reservation was not found for locking")
        return row

    def is_stale_generation(self, row_generation: int, observed_generation: int) -> bool:
        return observed_generation < row_generation

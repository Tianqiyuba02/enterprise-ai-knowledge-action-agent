"""Command-side persistence for V4 leave_requests. Query repo stays read-only."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.workflow_models import LeaveRequest
from app.workflow.domain import V4_REVISION, LeaveRequestStatus, LeaveType


@dataclass(frozen=True, slots=True)
class NewLeaveRequest:
    employee_id: str
    leave_type: LeaveType
    start_date: date
    end_date: date
    requested_hours: Decimal
    reason: str | None
    submitted_at: datetime
    execution_key: str | None
    business_request_key: str
    source_action_id: UUID
    calendar_version: str
    ruleset_version: str
    source_action_revision: int = V4_REVISION
    status: LeaveRequestStatus = LeaveRequestStatus.SUBMITTED


class LeaveCommandRepository:
    """Insert submitted annual-leave rows. No balance deduction or cancellation."""

    def persist(self, session: Session, spec: NewLeaveRequest) -> LeaveRequest:
        row = LeaveRequest(
            leave_request_id=uuid4(),
            employee_id=spec.employee_id,
            leave_type=spec.leave_type.value,
            start_date=spec.start_date,
            end_date=spec.end_date,
            requested_hours=spec.requested_hours,
            reason=spec.reason,
            status=spec.status.value,
            submitted_at=spec.submitted_at,
            execution_key=spec.execution_key,
            business_request_key=spec.business_request_key,
            source_action_id=spec.source_action_id,
            source_action_revision=spec.source_action_revision,
            calendar_version=spec.calendar_version,
            ruleset_version=spec.ruleset_version,
        )
        session.add(row)
        session.flush()
        return row

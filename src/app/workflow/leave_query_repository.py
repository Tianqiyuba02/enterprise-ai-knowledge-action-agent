"""Read/validation primitives for V4 leave requests. No submit/insert API."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.workflow_models import LeaveRequest
from app.workflow.balance import date_ranges_overlap, effective_available_hours
from app.workflow.canonical import quantize_hours
from app.workflow.domain import LeaveRequestStatus, LeaveType


class LeaveQueryRepository:
    """Query committed V4 leave state without exposing a business mutation path."""

    def find_by_source_action_id(
        self, session: Session, source_action_id: UUID
    ) -> LeaveRequest | None:
        return session.execute(
            select(LeaveRequest).where(LeaveRequest.source_action_id == source_action_id)
        ).scalar_one_or_none()

    def find_by_business_request_key(
        self,
        session: Session,
        business_request_key: str,
    ) -> LeaveRequest | None:
        return session.execute(
            select(LeaveRequest).where(LeaveRequest.business_request_key == business_request_key)
        ).scalar_one_or_none()

    def sum_active_submitted_hours(
        self,
        session: Session,
        *,
        employee_id: str,
        leave_type: LeaveType = LeaveType.ANNUAL,
    ) -> Decimal:
        total = session.execute(
            select(func.coalesce(func.sum(LeaveRequest.requested_hours), 0)).where(
                LeaveRequest.employee_id == employee_id,
                LeaveRequest.leave_type == leave_type.value,
                LeaveRequest.status == LeaveRequestStatus.SUBMITTED.value,
            )
        ).scalar_one()
        return quantize_hours(Decimal(total))

    def overlapping_active_annual_leave(
        self,
        session: Session,
        *,
        employee_id: str,
        start_date: date,
        end_date: date,
    ) -> tuple[LeaveRequest, ...]:
        rows = session.execute(
            select(LeaveRequest).where(
                LeaveRequest.employee_id == employee_id,
                LeaveRequest.leave_type == LeaveType.ANNUAL.value,
                LeaveRequest.status == LeaveRequestStatus.SUBMITTED.value,
                LeaveRequest.start_date <= end_date,
                LeaveRequest.end_date >= start_date,
            )
        ).scalars()
        return tuple(rows)

    def effective_available_annual_leave(
        self,
        session: Session,
        *,
        employee_id: str,
        trusted_base_balance_hours: Decimal,
    ) -> Decimal:
        committed = self.sum_active_submitted_hours(session, employee_id=employee_id)
        return effective_available_hours(
            trusted_base_balance_hours=trusted_base_balance_hours,
            committed_submitted_hours=committed,
        )

    def has_overlapping_active_annual_leave(
        self,
        session: Session,
        *,
        employee_id: str,
        start_date: date,
        end_date: date,
    ) -> bool:
        overlaps = self.overlapping_active_annual_leave(
            session,
            employee_id=employee_id,
            start_date=start_date,
            end_date=end_date,
        )
        return any(
            date_ranges_overlap(start_date, end_date, row.start_date, row.end_date)
            for row in overlaps
        )

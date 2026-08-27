"""Deterministic V4 executable preparation and revalidation. No model calls."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.agent.leave_models import LeavePreparationStatus, PrepareLeaveRequestArguments
from app.db.workflow_models import ActionRevision, ActionWorkflow
from app.identity import AuthenticatedEmployeeContext
from app.repositories.demo import DemoRepository
from app.services.employee import EmployeeService
from app.services.leave_preparation import LeavePreparationService
from app.workflow.authority import AuthoritySnapshot, CanonicalDraft
from app.workflow.calendar import V4_CALENDAR_VERSION, V4_RULESET_VERSION
from app.workflow.calendar_service import CalendarCoverage, TrustedHolidayCalendarService
from app.workflow.canonical import business_request_key, quantize_hours, require_decimal
from app.workflow.domain import ActionType, LeaveType
from app.workflow.errors import WorkflowIntegrityError
from app.workflow.holiday_repository import HolidayCalendarRepository
from app.workflow.leave_query_repository import LeaveQueryRepository

READINESS_READY = LeavePreparationStatus.READY.value
READINESS_INSUFFICIENT_BALANCE = LeavePreparationStatus.INSUFFICIENT_BALANCE.value
READINESS_NO_SCHEDULED_WORKDAYS = LeavePreparationStatus.NO_SCHEDULED_WORKDAYS.value
READINESS_NOT_EXECUTABLE = "not_executable"

CANONICAL_DRAFT_FIELDS = frozenset(
    {
        "action_type",
        "leave_type",
        "start_date",
        "end_date",
        "requested_hours",
        "projected_balance_hours",
        "readiness",
        "reason",
        "calendar_version",
        "ruleset_version",
        "authority_snapshot_hash",
    }
)


@dataclass(frozen=True, slots=True)
class ExecutablePreparation:
    snapshot: AuthoritySnapshot
    draft: CanonicalDraft
    scheduled_work_days: int
    coverage: CalendarCoverage
    business_request_key: str

    @property
    def executable(self) -> bool:
        return (
            self.coverage is CalendarCoverage.COVERED
            and self.draft.readiness == READINESS_READY
            and self.scheduled_work_days > 0
        )

    def payload(self) -> dict[str, Any]:
        return serialize_canonical_draft(self.draft, scheduled_work_days=self.scheduled_work_days)


@dataclass(frozen=True, slots=True)
class RevalidationResult:
    preparation: ExecutablePreparation
    persisted_draft: CanonicalDraft
    stale: bool
    integrity_ok: bool


def serialize_canonical_draft(
    draft: CanonicalDraft,
    *,
    scheduled_work_days: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action_type": draft.action_type,
        "leave_type": draft.leave_type,
        "start_date": draft.start_date.isoformat(),
        "end_date": draft.end_date.isoformat(),
        "requested_hours": format(draft.requested_hours, "f"),
        "projected_balance_hours": format(draft.projected_balance_hours, "f"),
        "readiness": draft.readiness,
        "reason": draft.reason,
        "calendar_version": draft.calendar_version,
        "ruleset_version": draft.ruleset_version,
        "authority_snapshot_hash": draft.authority_snapshot_hash,
    }
    if scheduled_work_days is not None:
        payload["scheduled_work_days"] = scheduled_work_days
    return payload


def reconstruct_canonical_draft(payload: dict[str, Any]) -> CanonicalDraft:
    missing = CANONICAL_DRAFT_FIELDS - set(payload)
    if missing:
        raise WorkflowIntegrityError(
            f"stored draft payload is missing canonical fields: {sorted(missing)}"
        )
    try:
        return CanonicalDraft(
            action_type=str(payload["action_type"]),
            leave_type=str(payload["leave_type"]),
            start_date=_as_date(payload["start_date"]),
            end_date=_as_date(payload["end_date"]),
            requested_hours=_as_decimal(payload["requested_hours"]),
            projected_balance_hours=_as_decimal(payload["projected_balance_hours"]),
            readiness=str(payload["readiness"]),
            reason=payload["reason"] if payload["reason"] is None else str(payload["reason"]),
            calendar_version=str(payload["calendar_version"]),
            ruleset_version=str(payload["ruleset_version"]),
            authority_snapshot_hash=str(payload["authority_snapshot_hash"]),
        )
    except (TypeError, ValueError) as exc:
        raise WorkflowIntegrityError("stored draft payload cannot be reconstructed") from exc


def verify_persisted_draft_integrity(revision: ActionRevision) -> CanonicalDraft:
    if not isinstance(revision.draft_payload, dict):
        raise WorkflowIntegrityError("stored draft payload is not an object")
    reconstructed = reconstruct_canonical_draft(revision.draft_payload)
    if reconstructed.fingerprint() != revision.draft_hash:
        raise WorkflowIntegrityError("stored draft payload does not match draft_hash")
    if reconstructed.authority_snapshot_hash != revision.authority_snapshot_hash:
        raise WorkflowIntegrityError("stored draft authority snapshot hash is inconsistent")
    return reconstructed


def holiday_adjusted_scheduled_work_days(
    *,
    start_date: date,
    end_date: date,
    work_days: tuple[str, ...],
    holiday_dates: set[date],
) -> int:
    scheduled_weekdays = {day.lower() for day in work_days}
    current = start_date
    count = 0
    while current <= end_date:
        if current.strftime("%A").lower() in scheduled_weekdays and current not in holiday_dates:
            count += 1
        current += timedelta(days=1)
    return count


class V4ExecutablePreparationService:
    """Holiday-aware executable draft over sealed V3 LeavePreparationService."""

    def __init__(
        self,
        leave_preparation: LeavePreparationService | None = None,
        calendar: TrustedHolidayCalendarService | None = None,
        leave_queries: LeaveQueryRepository | None = None,
    ) -> None:
        employee_service = EmployeeService(DemoRepository())
        self._employee_service = employee_service
        self._leave_preparation = leave_preparation or LeavePreparationService(employee_service)
        self._calendar = calendar or TrustedHolidayCalendarService(HolidayCalendarRepository())
        self._leave_queries = leave_queries or LeaveQueryRepository()

    def prepare(
        self,
        session: Session,
        *,
        context: AuthenticatedEmployeeContext,
        start_date: date,
        end_date: date,
        reason: str | None,
    ) -> ExecutablePreparation:
        arguments = PrepareLeaveRequestArguments.model_validate(
            {
                "leave_type": LeaveType.ANNUAL.value,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "reason": reason,
            }
        )
        base = self._leave_preparation.prepare(arguments, context)
        profile = self._employee_service.get_my_profile(context)
        jurisdiction = context.jurisdiction or "AU-VIC"
        calendar = self._calendar.holidays_for_range(
            session,
            jurisdiction=jurisdiction,
            start_date=start_date,
            end_date=end_date,
        )
        holiday_dates = {row.holiday_date for row in calendar.holidays}
        if calendar.coverage is CalendarCoverage.COVERED:
            scheduled_days = holiday_adjusted_scheduled_work_days(
                start_date=start_date,
                end_date=end_date,
                work_days=profile.work_days,
                holiday_dates=holiday_dates,
            )
        else:
            scheduled_days = 0
        hours_per_day = quantize_hours(Decimal(str(profile.hours_per_day)))
        requested_hours = quantize_hours(Decimal(scheduled_days) * hours_per_day)
        trusted_base = quantize_hours(Decimal(str(base.current_balance_hours)))
        committed = self._leave_queries.sum_active_submitted_hours(
            session,
            employee_id=context.employee_id,
        )
        effective = self._leave_queries.effective_available_annual_leave(
            session,
            employee_id=context.employee_id,
            trusted_base_balance_hours=trusted_base,
        )
        projected = quantize_hours(effective - requested_hours)
        if calendar.coverage is not CalendarCoverage.COVERED:
            readiness = READINESS_NOT_EXECUTABLE
        elif scheduled_days == 0:
            readiness = READINESS_NO_SCHEDULED_WORKDAYS
        elif requested_hours > effective:
            readiness = READINESS_INSUFFICIENT_BALANCE
        else:
            readiness = READINESS_READY
        snapshot = AuthoritySnapshot(
            employee_id=context.employee_id,
            jurisdiction=jurisdiction,
            work_days=tuple(day.lower() for day in profile.work_days),
            hours_per_day=hours_per_day,
            timezone=profile.timezone,
            trusted_base_balance_hours=trusted_base,
            committed_submitted_hours=committed,
            effective_available_hours=effective,
            calendar_version=V4_CALENDAR_VERSION,
            ruleset_version=V4_RULESET_VERSION,
        )
        draft = CanonicalDraft(
            action_type=ActionType.SUBMIT_ANNUAL_LEAVE.value,
            leave_type=LeaveType.ANNUAL.value,
            start_date=start_date,
            end_date=end_date,
            requested_hours=requested_hours,
            projected_balance_hours=projected,
            readiness=readiness,
            reason=reason,
            calendar_version=V4_CALENDAR_VERSION,
            ruleset_version=V4_RULESET_VERSION,
            authority_snapshot_hash=snapshot.fingerprint(),
        )
        return ExecutablePreparation(
            snapshot=snapshot,
            draft=draft,
            scheduled_work_days=scheduled_days,
            coverage=calendar.coverage,
            business_request_key=business_request_key(
                employee_id=context.employee_id,
                leave_type=LeaveType.ANNUAL.value,
                start_date=start_date,
                end_date=end_date,
            ),
        )

    def revalidate_confirmed(
        self,
        session: Session,
        workflow: ActionWorkflow,
        revision: ActionRevision,
    ) -> RevalidationResult:
        persisted = verify_persisted_draft_integrity(revision)
        context = AuthenticatedEmployeeContext(
            employee_id=workflow.owner_employee_id,
            subject_id=workflow.owner_subject_id,
            jurisdiction=workflow.jurisdiction,
        )
        live = self.prepare(
            session,
            context=context,
            start_date=persisted.start_date,
            end_date=persisted.end_date,
            reason=persisted.reason,
        )
        stale = (
            live.draft.fingerprint() != revision.draft_hash
            or live.snapshot.fingerprint() != revision.authority_snapshot_hash
            or live.business_request_key != revision.business_request_key
            or live.draft.calendar_version != revision.calendar_version
            or live.draft.ruleset_version != revision.ruleset_version
            or not live.executable
        )
        return RevalidationResult(
            preparation=live,
            persisted_draft=persisted,
            stale=stale,
            integrity_ok=True,
        )


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        raise TypeError("datetime is not allowed as a canonical leave date")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError("canonical date must be an ISO string")


def _as_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return require_decimal(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    raise TypeError("canonical hours must be a Decimal-compatible string")

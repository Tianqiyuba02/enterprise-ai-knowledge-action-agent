"""M3 calendar extension over the sealed V4 annual-leave semantics."""

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.agent.leave_models import PrepareLeaveRequestArguments
from app.db.workflow_models import ActionRevision, ActionWorkflow
from app.demo.calendar import M3_DEMO_CALENDAR_VERSION, DemoHolidayCalendarService
from app.identity import AuthenticatedEmployeeContext
from app.workflow.atomic_execution import (
    AtomicConfirmedExecutor,
    _Classification,
    _stable_authority_changed,
)
from app.workflow.authority import AuthoritySnapshot, CanonicalDraft
from app.workflow.calendar import V4_CALENDAR_VERSION, V4_RULESET_VERSION
from app.workflow.calendar_service import CalendarCoverage
from app.workflow.canonical import business_request_key, quantize_hours
from app.workflow.domain import ActionType, LeaveType, WorkflowState
from app.workflow.errors import WorkflowIntegrityError
from app.workflow.executable_preparation import (
    READINESS_INSUFFICIENT_BALANCE,
    READINESS_NO_SCHEDULED_WORKDAYS,
    READINESS_NOT_EXECUTABLE,
    READINESS_READY,
    ExecutablePreparation,
    RevalidationResult,
    V4ExecutablePreparationService,
    holiday_adjusted_scheduled_work_days,
    verify_persisted_draft_integrity,
)


class M3ExecutablePreparationService(V4ExecutablePreparationService):
    """Use reviewed 2026-2028 dates while retaining V4 business calculations."""

    def __init__(self) -> None:
        super().__init__()
        self._demo_calendar = DemoHolidayCalendarService()

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
        calendar = self._demo_calendar.holidays_for_range(
            session,
            jurisdiction=jurisdiction,
            start_date=start_date,
            end_date=end_date,
        )
        coverage = CalendarCoverage.COVERED if calendar.covered else CalendarCoverage.UNRESOLVED
        holiday_dates = {row.holiday_date for row in calendar.holidays}
        scheduled_days = (
            holiday_adjusted_scheduled_work_days(
                start_date=start_date,
                end_date=end_date,
                work_days=profile.work_days,
                holiday_dates=holiday_dates,
            )
            if coverage is CalendarCoverage.COVERED
            else 0
        )
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
        if coverage is not CalendarCoverage.COVERED:
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
            calendar_version=M3_DEMO_CALENDAR_VERSION,
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
            calendar_version=M3_DEMO_CALENDAR_VERSION,
            ruleset_version=V4_RULESET_VERSION,
            authority_snapshot_hash=snapshot.fingerprint(),
        )
        return ExecutablePreparation(
            snapshot=snapshot,
            draft=draft,
            scheduled_work_days=scheduled_days,
            coverage=coverage,
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
        if (
            revision.calendar_version != M3_DEMO_CALENDAR_VERSION
            or persisted.calendar_version != revision.calendar_version
            or revision.ruleset_version != V4_RULESET_VERSION
            or persisted.ruleset_version != revision.ruleset_version
        ):
            return RevalidationResult(None, persisted, True, True)
        live = self.prepare(
            session,
            context=AuthenticatedEmployeeContext(
                employee_id=workflow.owner_employee_id,
                subject_id=workflow.owner_subject_id,
                jurisdiction=workflow.jurisdiction,
            ),
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
        return RevalidationResult(live, persisted, stale, True)


class M3AtomicConfirmedExecutor(AtomicConfirmedExecutor):
    """Accept the M3 calendar version while delegating V4 and IT actions unchanged."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._m3_preparation = M3ExecutablePreparationService()

    def _classify(
        self,
        session: Session,
        workflow: ActionWorkflow,
        revision: ActionRevision,
    ) -> _Classification:
        if (
            workflow.action_type != ActionType.SUBMIT_ANNUAL_LEAVE.value
            or revision.calendar_version == V4_CALENDAR_VERSION
        ):
            return super()._classify(session, workflow, revision)
        try:
            persisted = verify_persisted_draft_integrity(revision)
        except WorkflowIntegrityError:
            return _Classification(WorkflowState.EXECUTION_FAILED, "DRAFT_INTEGRITY_FAILURE")
        if (
            revision.calendar_version != M3_DEMO_CALENDAR_VERSION
            or revision.ruleset_version != V4_RULESET_VERSION
            or persisted.calendar_version != revision.calendar_version
            or persisted.ruleset_version != revision.ruleset_version
        ):
            return _Classification(WorkflowState.STALE, "AUTHORITY_CHANGED")
        live = self._m3_preparation.prepare(
            session,
            context=AuthenticatedEmployeeContext(
                employee_id=workflow.owner_employee_id,
                subject_id=workflow.owner_subject_id,
                jurisdiction=workflow.jurisdiction,
            ),
            start_date=persisted.start_date,
            end_date=persisted.end_date,
            reason=persisted.reason,
        )
        if _stable_authority_changed(workflow, revision, persisted, live):
            return _Classification(WorkflowState.STALE, "AUTHORITY_CHANGED")
        if live.coverage is not CalendarCoverage.COVERED:
            return _Classification(WorkflowState.EXECUTION_FAILED, "CALENDAR_UNCOVERED")
        if live.draft.requested_hours > live.snapshot.effective_available_hours:
            return _Classification(WorkflowState.EXECUTION_FAILED, "INSUFFICIENT_BALANCE")
        overlaps = self._leave_queries.overlapping_active_annual_leave(
            session,
            employee_id=workflow.owner_employee_id,
            start_date=persisted.start_date,
            end_date=persisted.end_date,
        )
        foreign = [
            row
            for row in overlaps
            if row.business_request_key != revision.business_request_key
            and row.source_action_id != workflow.action_id
        ]
        if foreign:
            return _Classification(WorkflowState.EXECUTION_FAILED, "OVERLAP")
        return _Classification(None, None, live, persisted)

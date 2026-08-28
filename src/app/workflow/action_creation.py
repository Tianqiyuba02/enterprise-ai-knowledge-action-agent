"""Deterministic T1 action creation from a trusted V3 PREPARE result.

LangGraph is not started here. PostgreSQL remains the only business authority.
A later orchestration initialization step may create the initial interrupt
checkpoint; checkpoint failure must not roll back this action.
"""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.agent.leave_models import LeaveRequestDraft
from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.workflow_models import ActionRevision, ActionWorkflow
from app.errors import ActionCreationIdentityError
from app.identity import AuthenticatedEmployeeContext
from app.workflow.audit_repository import AuditRepository, NewAuditEvent
from app.workflow.calendar_service import CalendarCoverage
from app.workflow.canonical import business_request_key
from app.workflow.confirmation import AUDIT_ACTION_EXPIRED
from app.workflow.domain import ActionType, ActorType, LeaveType, WorkflowState
from app.workflow.executable_preparation import (
    READINESS_INSUFFICIENT_BALANCE,
    READINESS_NO_SCHEDULED_WORKDAYS,
    READINESS_NOT_EXECUTABLE,
    ExecutablePreparation,
    V4ExecutablePreparationService,
)
from app.workflow.locks import acquire_business_request_lock
from app.workflow.time import database_now
from app.workflow.workflow_repository import NewWorkflowRevision, WorkflowRepository

AUDIT_ACTION_PREPARED = "ACTION_PREPARED"
LIVE_REUSE_STATES = frozenset(
    {
        WorkflowState.AWAITING_CONFIRMATION.value,
        WorkflowState.CONFIRMED.value,
    }
)
IN_FLIGHT_STATES = frozenset(
    {
        WorkflowState.EXECUTING.value,
        WorkflowState.UNKNOWN_OUTCOME.value,
        WorkflowState.RECONCILING.value,
    }
)


class ActionCreationDisposition(StrEnum):
    CREATED = "CREATED"
    REUSED_EXISTING = "REUSED_EXISTING"
    RETURNED_IN_FLIGHT = "RETURNED_IN_FLIGHT"
    RETURNED_SUCCEEDED = "RETURNED_SUCCEEDED"
    NOT_CREATED = "NOT_CREATED"


@dataclass(frozen=True, slots=True)
class ActionCreationResult:
    disposition: ActionCreationDisposition
    action_id: UUID | None = None
    revision: int | None = None
    state: str | None = None
    action_type: str | None = None
    draft: dict[str, Any] | None = None
    action_expires_at: Any | None = None
    confirmation_required: bool = False
    ineligibility_reason: str | None = None

    @property
    def created(self) -> bool:
        return self.disposition is ActionCreationDisposition.CREATED

    @property
    def has_action(self) -> bool:
        return self.action_id is not None


@dataclass
class ActionCreationFailpoints:
    """Test-only hooks. Do not wire to HTTP, query params, or runtime env flags."""

    raise_after_revision_before_commit: BaseException | None = None


def require_v4_execution_identity(context: AuthenticatedEmployeeContext) -> tuple[str, str, str]:
    """Require trusted V4 identity at the action boundary. V3 context may stay partial."""

    subject_id = (context.subject_id or "").strip()
    session_id = (context.session_id or "").strip()
    jurisdiction = (context.jurisdiction or "").strip()
    if not context.employee_id or not context.employee_id.strip():
        raise ActionCreationIdentityError
    if not subject_id or not session_id or not jurisdiction:
        raise ActionCreationIdentityError
    return subject_id, session_id, jurisdiction


class ActionCreationService:
    """Create or reuse a confirmation-ready annual-leave action from trusted inputs."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: KnowledgeSettings | None = None,
        *,
        preparation: V4ExecutablePreparationService | None = None,
        failpoints: ActionCreationFailpoints | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or load_knowledge_settings()
        self._preparation = preparation or V4ExecutablePreparationService()
        self._failpoints = failpoints
        self._workflows = WorkflowRepository()
        self._audits = AuditRepository()

    def create_or_reuse(
        self,
        context: AuthenticatedEmployeeContext,
        prepared: LeaveRequestDraft,
    ) -> ActionCreationResult:
        subject_id, _session_id, jurisdiction = require_v4_execution_identity(context)
        ineligible = _structural_ineligibility(prepared)
        if ineligible is not None:
            return _not_created(ineligible)
        with self._session_factory() as session:
            lock_key = business_request_key(
                employee_id=context.employee_id,
                leave_type=LeaveType.ANNUAL.value,
                start_date=prepared.start_date,
                end_date=prepared.end_date,
            )
            acquire_business_request_lock(session, lock_key)
            now = database_now(session)
            executable = self._preparation.prepare(
                session,
                context=context,
                start_date=prepared.start_date,
                end_date=prepared.end_date,
                reason=prepared.reason,
            )
            if executable.business_request_key != lock_key:
                session.rollback()
                return _not_created("authority_inconsistent")
            reason = _eligibility_reason(executable)
            if reason is not None:
                session.rollback()
                return _not_created(reason)
            existing = self._select_existing(
                session,
                owner_employee_id=context.employee_id,
                owner_subject_id=subject_id,
                business_request_key=lock_key,
                now=now,
            )
            if existing is not None:
                session.commit()
                return existing
            action_id = uuid4()
            thread_id = str(uuid4())
            expires_at = now + timedelta(seconds=self._settings.v4_action_ttl_seconds)
            workflow, revision = self._workflows.create_workflow_and_revision(
                session,
                NewWorkflowRevision(
                    owner_subject_id=subject_id,
                    owner_employee_id=context.employee_id,
                    jurisdiction=jurisdiction,
                    action_type=ActionType.SUBMIT_ANNUAL_LEAVE,
                    state=WorkflowState.AWAITING_CONFIRMATION,
                    draft_payload=executable.payload(),
                    draft_hash=executable.draft.fingerprint(),
                    authority_snapshot_hash=executable.snapshot.fingerprint(),
                    business_request_key=executable.business_request_key,
                    ruleset_version=executable.draft.ruleset_version,
                    calendar_version=executable.draft.calendar_version,
                    action_expires_at=expires_at,
                    langgraph_thread_id=thread_id,
                    action_id=action_id,
                ),
            )
            self._audits.insert(
                session,
                NewAuditEvent(
                    action_id=workflow.action_id,
                    event_type=AUDIT_ACTION_PREPARED,
                    actor_type=ActorType.EMPLOYEE,
                    actor_subject_id=subject_id,
                    to_state=WorkflowState.AWAITING_CONFIRMATION.value,
                    safe_metadata={
                        "disposition": ActionCreationDisposition.CREATED.value,
                        "business_request_key": executable.business_request_key,
                    },
                    revision=revision.revision,
                ),
            )
            self._raise_before_commit()
            session.commit()
            return _from_rows(
                workflow,
                revision,
                ActionCreationDisposition.CREATED,
            )

    def _select_existing(
        self,
        session: Session,
        *,
        owner_employee_id: str,
        owner_subject_id: str,
        business_request_key: str,
        now,
    ) -> ActionCreationResult | None:
        rows = self._workflows.lock_owner_revisions_for_business_request(
            session,
            owner_employee_id=owner_employee_id,
            owner_subject_id=owner_subject_id,
            business_request_key=business_request_key,
        )
        in_flight = None
        live = None
        succeeded = None
        for workflow, revision in rows:
            self._expire_if_needed(session, workflow, revision, now, owner_subject_id)
            if revision.state in IN_FLIGHT_STATES:
                in_flight = (workflow, revision)
            elif revision.state in LIVE_REUSE_STATES:
                live = (workflow, revision)
            elif revision.state == WorkflowState.SUCCEEDED.value:
                succeeded = (workflow, revision)
        if in_flight is not None:
            return _from_rows(*in_flight, ActionCreationDisposition.RETURNED_IN_FLIGHT)
        if live is not None:
            return _from_rows(*live, ActionCreationDisposition.REUSED_EXISTING)
        if succeeded is not None:
            return _from_rows(*succeeded, ActionCreationDisposition.RETURNED_SUCCEEDED)
        return None

    def _expire_if_needed(
        self,
        session: Session,
        workflow: ActionWorkflow,
        revision: ActionRevision,
        now,
        owner_subject_id: str,
    ) -> None:
        state = revision.state
        awaiting_expired = (
            state == WorkflowState.AWAITING_CONFIRMATION.value and revision.action_expires_at <= now
        )
        confirmed_expired = (
            state == WorkflowState.CONFIRMED.value
            and revision.confirmed_expires_at is not None
            and revision.confirmed_expires_at <= now
        )
        if not awaiting_expired and not confirmed_expired:
            return
        from_state = revision.state
        revision.state = WorkflowState.EXPIRED.value
        self._audits.insert(
            session,
            NewAuditEvent(
                action_id=workflow.action_id,
                event_type=AUDIT_ACTION_EXPIRED,
                actor_type=ActorType.SYSTEM,
                actor_subject_id=owner_subject_id,
                from_state=from_state,
                to_state=WorkflowState.EXPIRED.value,
                revision=revision.revision,
            ),
        )
        session.flush()

    def _raise_before_commit(self) -> None:
        if self._failpoints is None or self._failpoints.raise_after_revision_before_commit is None:
            return
        raise self._failpoints.raise_after_revision_before_commit


def _structural_ineligibility(prepared: LeaveRequestDraft) -> str | None:
    if prepared.leave_type != LeaveType.ANNUAL.value:
        return "unsupported_leave_type"
    if prepared.start_date > prepared.end_date:
        return "invalid_preparation"
    if prepared.non_executing is not True:
        return "invalid_preparation"
    return None


def _eligibility_reason(executable: ExecutablePreparation) -> str | None:
    if (
        executable.coverage is not CalendarCoverage.COVERED
        or executable.draft.readiness == READINESS_NOT_EXECUTABLE
    ):
        return "calendar_uncovered"
    if (
        executable.scheduled_work_days <= 0
        or executable.draft.readiness == READINESS_NO_SCHEDULED_WORKDAYS
    ):
        return "no_scheduled_work"
    if executable.draft.readiness == READINESS_INSUFFICIENT_BALANCE:
        return "insufficient_balance"
    if not executable.executable:
        return "not_executable"
    return None


def _not_created(reason: str) -> ActionCreationResult:
    return ActionCreationResult(
        disposition=ActionCreationDisposition.NOT_CREATED,
        ineligibility_reason=reason,
    )


def _from_rows(
    workflow: ActionWorkflow,
    revision: ActionRevision,
    disposition: ActionCreationDisposition,
) -> ActionCreationResult:
    return ActionCreationResult(
        disposition=disposition,
        action_id=workflow.action_id,
        revision=revision.revision,
        state=revision.state,
        action_type=workflow.action_type,
        draft=dict(revision.draft_payload),
        action_expires_at=revision.action_expires_at,
        confirmation_required=revision.state == WorkflowState.AWAITING_CONFIRMATION.value,
    )

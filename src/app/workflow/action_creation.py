"""Deterministic T1 action creation from a trusted V3 PREPARE result.

LangGraph is not started here. PostgreSQL remains the only business authority.
Occupancy is enforced by the Phase 1A transitional unique index. PREPARE does
not acquire the employee advisory lock.
"""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any, Final
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.agent.leave_models import LeaveRequestDraft
from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.workflow_models import ActionRevision, ActionWorkflow
from app.errors import ActionCreationIdentityError
from app.identity import AuthenticatedEmployeeContext
from app.workflow.audit_repository import AuditRepository, NewAuditEvent
from app.workflow.calendar_service import CalendarCoverage
from app.workflow.canonical import business_request_key
from app.workflow.challenge_repository import ChallengeRepository
from app.workflow.confirmation import AUDIT_ACTION_EXPIRED, AUDIT_CHALLENGE_SUPERSEDED
from app.workflow.domain import (
    ActionType,
    ActorType,
    ChallengeStatus,
    LeaveType,
    WorkflowState,
)
from app.workflow.errors import WorkflowIntegrityError, WorkflowRowNotFoundError
from app.workflow.executable_preparation import (
    READINESS_INSUFFICIENT_BALANCE,
    READINESS_NO_SCHEDULED_WORKDAYS,
    READINESS_NOT_EXECUTABLE,
    ExecutablePreparation,
    V4ExecutablePreparationService,
    verify_persisted_draft_integrity,
)
from app.workflow.occupancy import (
    LEGACY_UNRESOLVED_STATES,
    PREPARE_NORMALIZABLE_STATES,
    is_occupancy_unique_violation,
)
from app.workflow.time import database_now
from app.workflow.workflow_repository import NewWorkflowRevision, WorkflowRepository

AUDIT_ACTION_PREPARED = "ACTION_PREPARED"
PREPARE_CONTENTION_ATTEMPTS: Final = 3


class ActionCreationDisposition(StrEnum):
    CREATED = "CREATED"
    REUSED_EXISTING = "REUSED_EXISTING"
    RETURNED_IN_FLIGHT = "RETURNED_IN_FLIGHT"
    RETURNED_SUCCEEDED = "RETURNED_SUCCEEDED"
    NOT_CREATED = "NOT_CREATED"
    RETRYABLE_CONFLICT = "RETRYABLE_CONFLICT"


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
    hold_after_occupying_lock: Any = None
    signal_occupying_lock: Any = None
    force_empty_occupant: bool = False


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
        self._challenges = ChallengeRepository()

    def create_or_reuse(
        self,
        context: AuthenticatedEmployeeContext,
        prepared: LeaveRequestDraft,
    ) -> ActionCreationResult:
        subject_id, _session_id, jurisdiction = require_v4_execution_identity(context)
        ineligible = _structural_ineligibility(prepared)
        if ineligible is not None:
            return _not_created(ineligible)
        requested_key = business_request_key(
            employee_id=context.employee_id,
            leave_type=LeaveType.ANNUAL.value,
            start_date=prepared.start_date,
            end_date=prepared.end_date,
        )
        with self._session_factory() as session:
            executable = self._preparation.prepare(
                session,
                context=context,
                start_date=prepared.start_date,
                end_date=prepared.end_date,
                reason=prepared.reason,
            )
            if executable.business_request_key != requested_key:
                return _not_created("authority_inconsistent")
            reason = _eligibility_reason(executable)
            if reason is not None:
                return _not_created(reason)
        for _attempt in range(PREPARE_CONTENTION_ATTEMPTS):
            created = self._attempt_insert(
                context,
                executable,
                subject_id=subject_id,
                jurisdiction=jurisdiction,
            )
            if created is not None:
                return created
            resolved = self._resolve_occupancy_conflict(
                context,
                requested_key,
                subject_id=subject_id,
            )
            if resolved is not None:
                return resolved
        return ActionCreationResult(
            disposition=ActionCreationDisposition.RETRYABLE_CONFLICT,
            ineligibility_reason="retryable_conflict",
        )

    def _attempt_insert(
        self,
        context: AuthenticatedEmployeeContext,
        executable: ExecutablePreparation,
        *,
        subject_id: str,
        jurisdiction: str,
    ) -> ActionCreationResult | None:
        with self._session_factory() as session:
            try:
                now = database_now(session)
                action_id = uuid4()
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
            except IntegrityError as exc:
                session.rollback()
                if is_occupancy_unique_violation(exc):
                    return None
                raise
            return _from_rows(workflow, revision, ActionCreationDisposition.CREATED)

    def _resolve_occupancy_conflict(
        self,
        context: AuthenticatedEmployeeContext,
        requested_key: str,
        *,
        subject_id: str,
    ) -> ActionCreationResult | None:
        with self._session_factory() as session:
            if self._failpoints is not None and self._failpoints.force_empty_occupant:
                session.rollback()
                return None
            try:
                occupying = self._workflows.lock_occupying_revision_for_business_request(
                    session, requested_key
                )
            except WorkflowRowNotFoundError:
                session.rollback()
                return _not_created("authority_inconsistent")
            if occupying is None:
                session.rollback()
                return None
            workflow, revision = occupying
            self._hold_after_occupying_lock()
            if not self._trusted_occupant_matches(
                context,
                workflow,
                revision,
                requested_key=requested_key,
                subject_id=subject_id,
            ):
                session.rollback()
                return _not_created("authority_inconsistent")
            if revision.state in LEGACY_UNRESOLVED_STATES:
                session.commit()
                return _from_rows(workflow, revision, ActionCreationDisposition.RETURNED_IN_FLIGHT)
            if revision.state in PREPARE_NORMALIZABLE_STATES:
                now = database_now(session)
                if self._prepare_normalizable_expired(revision, now):
                    self._expire_prepare_normalizable(session, workflow, revision, now, subject_id)
                    session.commit()
                    return None
                session.commit()
                return _from_rows(workflow, revision, ActionCreationDisposition.REUSED_EXISTING)
            if revision.state == WorkflowState.SUCCEEDED.value:
                session.commit()
                return _from_rows(workflow, revision, ActionCreationDisposition.RETURNED_SUCCEEDED)
            session.rollback()
            return _not_created("authority_inconsistent")

    def _trusted_occupant_matches(
        self,
        context: AuthenticatedEmployeeContext,
        workflow: ActionWorkflow,
        revision: ActionRevision,
        *,
        requested_key: str,
        subject_id: str,
    ) -> bool:
        if workflow.owner_employee_id != context.employee_id:
            return False
        if workflow.owner_subject_id != subject_id:
            return False
        if workflow.action_type != ActionType.SUBMIT_ANNUAL_LEAVE.value:
            return False
        try:
            draft = verify_persisted_draft_integrity(revision)
        except WorkflowIntegrityError:
            return False
        recomputed = business_request_key(
            employee_id=workflow.owner_employee_id,
            leave_type=draft.leave_type,
            start_date=draft.start_date,
            end_date=draft.end_date,
        )
        return (
            draft.leave_type == LeaveType.ANNUAL.value
            and recomputed == revision.business_request_key
            and recomputed == requested_key
        )

    def _prepare_normalizable_expired(self, revision: ActionRevision, now) -> bool:
        if revision.state == WorkflowState.AWAITING_CONFIRMATION.value:
            return revision.action_expires_at <= now
        if revision.state == WorkflowState.CONFIRMED.value:
            return (
                revision.confirmed_expires_at is not None and revision.confirmed_expires_at <= now
            )
        return False

    def _expire_prepare_normalizable(
        self,
        session: Session,
        workflow: ActionWorkflow,
        revision: ActionRevision,
        now,
        owner_subject_id: str,
    ) -> None:
        if revision.state == WorkflowState.AWAITING_CONFIRMATION.value:
            self._supersede_active_challenge(session, revision, now, owner_subject_id)
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

    def _supersede_active_challenge(
        self,
        session: Session,
        revision: ActionRevision,
        now,
        owner_subject_id: str,
    ) -> None:
        active = self._challenges.lock_active_challenge(
            session, action_id=revision.action_id, revision=revision.revision
        )
        if active is None:
            return
        active.status = ChallengeStatus.SUPERSEDED.value
        active.superseded_at = now
        self._audits.insert(
            session,
            NewAuditEvent(
                action_id=revision.action_id,
                event_type=AUDIT_CHALLENGE_SUPERSEDED,
                actor_type=ActorType.SYSTEM,
                actor_subject_id=owner_subject_id,
                from_state=revision.state,
                to_state=revision.state,
                safe_metadata={"challenge_id": str(active.challenge_id)},
                revision=revision.revision,
            ),
        )
        session.flush()

    def _raise_before_commit(self) -> None:
        if self._failpoints is None or self._failpoints.raise_after_revision_before_commit is None:
            return
        raise self._failpoints.raise_after_revision_before_commit

    def _hold_after_occupying_lock(self) -> None:
        if self._failpoints is None:
            return
        if self._failpoints.signal_occupying_lock is not None:
            self._failpoints.signal_occupying_lock.set()
        if self._failpoints.hold_after_occupying_lock is not None:
            self._failpoints.hold_after_occupying_lock.wait(timeout=10)


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

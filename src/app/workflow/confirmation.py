"""Out-of-band confirmation control plane. Resume and chat cannot confirm.

Transaction lock order for overlapping T2 / issue / cancel rows:

1. action_workflows
2. action_revisions
3. confirmation_challenges
4. action_audit_events insert

CONFIRMED is durable work for the internal poller. Confirmation does not
schedule execution, write a wake event, or create a separate execution permit.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.workflow_models import ActionRevision, ActionWorkflow
from app.errors import ActionConflictError, ActionNotFoundError, ConfirmationInvalidError
from app.identity import AuthenticatedEmployeeContext
from app.workflow.audit_repository import AuditRepository, NewAuditEvent
from app.workflow.challenge_repository import ChallengeRepository, NewConfirmationChallenge
from app.workflow.domain import (
    ActorType,
    ChallengeStatus,
    WorkflowState,
)
from app.workflow.errors import WorkflowRowNotFoundError
from app.workflow.time import database_now
from app.workflow.tokens import (
    confirmation_tokens_match,
    generate_confirmation_token,
    hash_confirmation_token,
)
from app.workflow.workflow_repository import WorkflowRepository

AUDIT_CHALLENGE_ISSUED = "CHALLENGE_ISSUED"
AUDIT_CHALLENGE_SUPERSEDED = "CHALLENGE_SUPERSEDED"
AUDIT_CONFIRMATION_FAILED = "CONFIRMATION_FAILED"
AUDIT_CONFIRMATION_REPLAYED = "CONFIRMATION_REPLAYED"
AUDIT_ACTION_CONFIRMED = "ACTION_CONFIRMED"
AUDIT_ACTION_CANCELLED = "ACTION_CANCELLED"
AUDIT_CANCEL_REJECTED = "CANCEL_REJECTED"
AUDIT_ACTION_EXPIRED = "ACTION_EXPIRED"

CANCELABLE_STATES = frozenset({WorkflowState.AWAITING_CONFIRMATION, WorkflowState.CONFIRMED})


@dataclass(frozen=True, slots=True)
class ActionView:
    action_id: UUID
    revision: int
    action_type: str
    state: str
    draft: dict[str, Any]
    action_expires_at: datetime
    confirmed_expires_at: datetime | None
    confirmation_required: bool
    manual_review_required: bool


@dataclass(frozen=True, slots=True)
class IssuedChallenge:
    challenge_id: UUID
    confirmation_token: str
    expires_at: datetime
    action: ActionView


class ConfirmationService:
    """Owner-scoped read, challenge, confirm, cancel, and expiry normalization."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: KnowledgeSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or load_knowledge_settings()
        self._workflows = WorkflowRepository()
        self._challenges = ChallengeRepository()
        self._audits = AuditRepository()

    def get_action(self, *, action_id: UUID, context: AuthenticatedEmployeeContext) -> ActionView:
        with self._session_factory() as session:
            workflow, revision = self._lock_owned_revision(session, action_id, context)
            now = database_now(session)
            self._normalize_expiry(session, workflow, revision, now, context)
            session.commit()
            return _action_view(workflow, revision)

    def issue_challenge(
        self,
        *,
        action_id: UUID,
        context: AuthenticatedEmployeeContext,
    ) -> IssuedChallenge:
        subject_id, session_id = _require_bindings(context)
        plaintext = generate_confirmation_token()
        with self._session_factory() as session:
            workflow, revision = self._lock_owned_revision(session, action_id, context)
            now = database_now(session)
            self._normalize_expiry(session, workflow, revision, now, context)
            if revision.state != WorkflowState.AWAITING_CONFIRMATION.value:
                raise ActionConflictError
            self._supersede_active_challenge(session, revision, now, context)
            expires_at = min(
                now + timedelta(seconds=self._settings.v4_confirmation_challenge_ttl_seconds),
                revision.action_expires_at,
            )
            if expires_at <= now:
                raise ActionConflictError
            challenge = self._challenges.persist(
                session,
                NewConfirmationChallenge(
                    action_id=workflow.action_id,
                    owner_subject_id=workflow.owner_subject_id,
                    confirmation_session_id=session_id,
                    draft_hash=revision.draft_hash,
                    token_hash=hash_confirmation_token(plaintext),
                    issued_at=now,
                    expires_at=expires_at,
                    revision=revision.revision,
                ),
            )
            self._audits.insert(
                session,
                NewAuditEvent(
                    action_id=workflow.action_id,
                    event_type=AUDIT_CHALLENGE_ISSUED,
                    actor_type=ActorType.EMPLOYEE,
                    actor_subject_id=subject_id,
                    from_state=revision.state,
                    to_state=revision.state,
                    safe_metadata={"challenge_id": str(challenge.challenge_id)},
                    revision=revision.revision,
                ),
            )
            session.commit()
            issued = IssuedChallenge(
                challenge_id=challenge.challenge_id,
                confirmation_token=plaintext,
                expires_at=challenge.expires_at,
                action=_action_view(workflow, revision),
            )
        return issued

    def confirm(
        self,
        *,
        action_id: UUID,
        challenge_id: UUID,
        confirmation_token: str,
        context: AuthenticatedEmployeeContext,
    ) -> ActionView:
        subject_id, session_id = _require_bindings(context)
        with self._session_factory() as session:
            workflow, revision = self._lock_owned_revision(session, action_id, context)
            now = database_now(session)
            self._normalize_expiry(session, workflow, revision, now, context)
            challenge = self._challenges.lock_challenge(session, challenge_id)
            if challenge is None:
                self._failed(session, workflow, revision, context, "challenge_not_found")
                session.commit()
                raise ConfirmationInvalidError
            if challenge.action_id != workflow.action_id or challenge.revision != revision.revision:
                self._failed(session, workflow, revision, context, "challenge_mismatch")
                session.commit()
                raise ConfirmationInvalidError
            token_ok = confirmation_tokens_match(
                plaintext=confirmation_token,
                token_hash=challenge.token_hash,
            )
            bindings_ok = (
                challenge.owner_subject_id == subject_id
                and challenge.confirmation_session_id == session_id
            )
            if challenge.status == ChallengeStatus.CONSUMED.value:
                if token_ok and bindings_ok:
                    self._audits.insert(
                        session,
                        NewAuditEvent(
                            action_id=workflow.action_id,
                            event_type=AUDIT_CONFIRMATION_REPLAYED,
                            actor_type=ActorType.EMPLOYEE,
                            actor_subject_id=subject_id,
                            from_state=revision.state,
                            to_state=revision.state,
                            safe_metadata={"challenge_id": str(challenge.challenge_id)},
                            revision=revision.revision,
                        ),
                    )
                    session.commit()
                    return _action_view(workflow, revision)
                self._failed(session, workflow, revision, context, "replay_mismatch")
                session.commit()
                raise ConfirmationInvalidError
            if revision.state != WorkflowState.AWAITING_CONFIRMATION.value:
                self._failed(session, workflow, revision, context, "action_not_awaiting")
                session.commit()
                raise ActionConflictError
            if not bindings_ok or not token_ok:
                self._failed(session, workflow, revision, context, "confirmation_mismatch")
                session.commit()
                raise ConfirmationInvalidError
            if challenge.status != ChallengeStatus.ACTIVE.value:
                self._failed(session, workflow, revision, context, "challenge_inactive")
                session.commit()
                raise ConfirmationInvalidError
            if challenge.expires_at <= now:
                self._failed(session, workflow, revision, context, "challenge_expired")
                session.commit()
                raise ConfirmationInvalidError
            if challenge.draft_hash != revision.draft_hash:
                self._failed(session, workflow, revision, context, "draft_mismatch")
                session.commit()
                raise ConfirmationInvalidError
            challenge.status = ChallengeStatus.CONSUMED.value
            challenge.consumed_at = now
            revision.state = WorkflowState.CONFIRMED.value
            revision.confirmed_at = now
            revision.confirmed_expires_at = min(
                revision.action_expires_at,
                now + timedelta(seconds=self._settings.v4_confirmed_ttl_seconds),
            )
            self._audits.insert(
                session,
                NewAuditEvent(
                    action_id=workflow.action_id,
                    event_type=AUDIT_ACTION_CONFIRMED,
                    actor_type=ActorType.EMPLOYEE,
                    actor_subject_id=subject_id,
                    from_state=WorkflowState.AWAITING_CONFIRMATION.value,
                    to_state=WorkflowState.CONFIRMED.value,
                    safe_metadata={"challenge_id": str(challenge.challenge_id)},
                    revision=revision.revision,
                ),
            )
            session.commit()
            return _action_view(workflow, revision)

    def cancel(self, *, action_id: UUID, context: AuthenticatedEmployeeContext) -> ActionView:
        subject_id, _session_id = _require_bindings(context)
        with self._session_factory() as session:
            workflow, revision = self._lock_owned_revision(session, action_id, context)
            now = database_now(session)
            self._normalize_expiry(session, workflow, revision, now, context)
            state = WorkflowState(revision.state)
            if state == WorkflowState.CANCELLED:
                session.commit()
                return _action_view(workflow, revision)
            if state not in CANCELABLE_STATES:
                self._audits.insert(
                    session,
                    NewAuditEvent(
                        action_id=workflow.action_id,
                        event_type=AUDIT_CANCEL_REJECTED,
                        actor_type=ActorType.EMPLOYEE,
                        actor_subject_id=subject_id,
                        from_state=revision.state,
                        to_state=revision.state,
                        revision=revision.revision,
                    ),
                )
                session.commit()
                raise ActionConflictError
            from_state = revision.state
            if state == WorkflowState.AWAITING_CONFIRMATION:
                self._supersede_active_challenge(session, revision, now, context)
            revision.state = WorkflowState.CANCELLED.value
            self._audits.insert(
                session,
                NewAuditEvent(
                    action_id=workflow.action_id,
                    event_type=AUDIT_ACTION_CANCELLED,
                    actor_type=ActorType.EMPLOYEE,
                    actor_subject_id=subject_id,
                    from_state=from_state,
                    to_state=WorkflowState.CANCELLED.value,
                    revision=revision.revision,
                ),
            )
            session.commit()
            return _action_view(workflow, revision)

    def normalize_expiry(
        self,
        *,
        action_id: UUID,
        context: AuthenticatedEmployeeContext | None = None,
    ) -> ActionView:
        """Expire an action using database time. Internal callers may omit employee context."""

        with self._session_factory() as session:
            workflow = self._workflows.lock_workflow(session, action_id)
            revision = self._workflows.lock_current_revision(session, workflow)
            now = database_now(session)
            actor = context or AuthenticatedEmployeeContext(employee_id=workflow.owner_employee_id)
            self._normalize_expiry(session, workflow, revision, now, actor)
            session.commit()
            return _action_view(workflow, revision)

    def _lock_owned_revision(
        self,
        session: Session,
        action_id: UUID,
        context: AuthenticatedEmployeeContext,
    ) -> tuple[ActionWorkflow, ActionRevision]:
        subject_id, _session_id = _require_bindings(context)
        try:
            workflow = self._workflows.lock_workflow(session, action_id)
        except WorkflowRowNotFoundError:
            raise ActionNotFoundError from None
        if (
            workflow.owner_subject_id != subject_id
            or workflow.owner_employee_id != context.employee_id
        ):
            raise ActionNotFoundError
        try:
            revision = self._workflows.lock_current_revision(session, workflow)
        except WorkflowRowNotFoundError:
            raise ActionNotFoundError from None
        return workflow, revision

    def _normalize_expiry(
        self,
        session: Session,
        workflow: ActionWorkflow,
        revision: ActionRevision,
        now,
        context: AuthenticatedEmployeeContext,
    ) -> None:
        state = WorkflowState(revision.state)
        expired = False
        if state == WorkflowState.AWAITING_CONFIRMATION and revision.action_expires_at <= now:
            expired = True
            self._supersede_active_challenge(session, revision, now, context)
        elif (
            state == WorkflowState.CONFIRMED
            and revision.confirmed_expires_at is not None
            and revision.confirmed_expires_at <= now
        ):
            expired = True
        if not expired:
            return
        from_state = revision.state
        revision.state = WorkflowState.EXPIRED.value
        self._audits.insert(
            session,
            NewAuditEvent(
                action_id=workflow.action_id,
                event_type=AUDIT_ACTION_EXPIRED,
                actor_type=ActorType.SYSTEM,
                actor_subject_id=context.subject_id,
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
        context: AuthenticatedEmployeeContext,
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
                actor_type=ActorType.EMPLOYEE,
                actor_subject_id=context.subject_id,
                from_state=revision.state,
                to_state=revision.state,
                safe_metadata={"challenge_id": str(active.challenge_id)},
                revision=revision.revision,
            ),
        )
        session.flush()

    def _failed(
        self,
        session: Session,
        workflow: ActionWorkflow,
        revision: ActionRevision,
        context: AuthenticatedEmployeeContext,
        reason: str,
    ) -> None:
        self._audits.insert(
            session,
            NewAuditEvent(
                action_id=workflow.action_id,
                event_type=AUDIT_CONFIRMATION_FAILED,
                actor_type=ActorType.EMPLOYEE,
                actor_subject_id=context.subject_id,
                from_state=revision.state,
                to_state=revision.state,
                safe_metadata={"reason": reason},
                revision=revision.revision,
            ),
        )


def _require_bindings(context: AuthenticatedEmployeeContext) -> tuple[str, str]:
    if not context.subject_id or not context.session_id:
        raise ActionNotFoundError
    return context.subject_id, context.session_id


def _action_view(workflow: ActionWorkflow, revision: ActionRevision) -> ActionView:
    return ActionView(
        action_id=workflow.action_id,
        revision=revision.revision,
        action_type=workflow.action_type,
        state=revision.state,
        draft=dict(revision.draft_payload),
        action_expires_at=revision.action_expires_at,
        confirmed_expires_at=revision.confirmed_expires_at,
        confirmation_required=revision.state == WorkflowState.AWAITING_CONFIRMATION.value,
        manual_review_required=revision.manual_review_required,
    )

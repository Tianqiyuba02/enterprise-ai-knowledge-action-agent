"""Atomic employee edits that append immutable IT action revisions."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings
from app.errors import ActionConflictError, ActionNotFoundError
from app.identity import AuthenticatedEmployeeContext
from app.it.domain import (
    IT_CALENDAR_VERSION,
    IT_RULESET_VERSION,
    ReviseITSupportTicketRequest,
    authoritative_it_draft,
    it_authority_hash,
    parse_authoritative_it_draft,
)
from app.workflow.action_creation import require_v4_execution_identity
from app.workflow.audit_repository import AuditRepository, NewAuditEvent
from app.workflow.challenge_repository import ChallengeRepository
from app.workflow.confirmation import AUDIT_CHALLENGE_SUPERSEDED, ActionView
from app.workflow.domain import ActionType, ActorType, ChallengeStatus, WorkflowState
from app.workflow.errors import WorkflowRowNotFoundError
from app.workflow.time import database_now
from app.workflow.workflow_repository import NewActionRevision, WorkflowRepository

AUDIT_REVISION_SUPERSEDED = "ACTION_REVISION_SUPERSEDED"
AUDIT_REVISION_CREATED = "ACTION_REVISION_CREATED"


class ITActionRevisionService:
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

    def create_revision(
        self,
        *,
        action_id: UUID,
        request: ReviseITSupportTicketRequest,
        context: AuthenticatedEmployeeContext,
    ) -> ActionView:
        subject_id, _session_id, _jurisdiction = require_v4_execution_identity(context)
        with self._session_factory() as session:
            workflow = self._lock_owned_workflow(session, action_id, context, subject_id)
            current = self._workflows.lock_current_revision(session, workflow)
            if workflow.action_type != ActionType.CREATE_IT_SUPPORT_TICKET.value:
                raise ActionConflictError
            if current.revision != request.expected_revision:
                raise ActionConflictError
            if current.state != WorkflowState.AWAITING_CONFIRMATION.value:
                raise ActionConflictError
            parse_authoritative_it_draft(current.draft_payload)

            now = database_now(session)
            self._supersede_challenge(session, current, now, subject_id)
            previous_revision = current.revision
            next_revision = previous_revision + 1
            current.state = WorkflowState.SUPERSEDED.value
            current.updated_at = now
            self._audits.insert(
                session,
                NewAuditEvent(
                    action_id=workflow.action_id,
                    revision=previous_revision,
                    event_type=AUDIT_REVISION_SUPERSEDED,
                    actor_type=ActorType.EMPLOYEE,
                    actor_subject_id=subject_id,
                    from_state=WorkflowState.AWAITING_CONFIRMATION.value,
                    to_state=WorkflowState.SUPERSEDED.value,
                    safe_metadata={"superseded_by_revision": str(next_revision)},
                ),
            )
            session.flush()

            authority_hash = it_authority_hash(context)
            draft = authoritative_it_draft(request, authority_hash=authority_hash)
            revision = self._workflows.create_revision(
                session,
                NewActionRevision(
                    action_id=workflow.action_id,
                    revision=next_revision,
                    state=WorkflowState.AWAITING_CONFIRMATION,
                    draft_payload=draft.payload(),
                    draft_hash=draft.fingerprint(),
                    authority_snapshot_hash=authority_hash,
                    business_request_key=current.business_request_key,
                    ruleset_version=IT_RULESET_VERSION,
                    calendar_version=IT_CALENDAR_VERSION,
                    action_expires_at=(
                        now + timedelta(seconds=self._settings.v4_action_ttl_seconds)
                    ),
                ),
            )
            workflow.current_revision = next_revision
            self._audits.insert(
                session,
                NewAuditEvent(
                    action_id=workflow.action_id,
                    revision=next_revision,
                    event_type=AUDIT_REVISION_CREATED,
                    actor_type=ActorType.EMPLOYEE,
                    actor_subject_id=subject_id,
                    to_state=WorkflowState.AWAITING_CONFIRMATION.value,
                    safe_metadata={"supersedes_revision": str(previous_revision)},
                ),
            )
            session.commit()
            return _action_view(workflow, revision)

    def _lock_owned_workflow(self, session, action_id, context, subject_id):
        try:
            workflow = self._workflows.lock_workflow(session, action_id)
        except WorkflowRowNotFoundError:
            raise ActionNotFoundError from None
        if (
            workflow.owner_employee_id != context.employee_id
            or workflow.owner_subject_id != subject_id
        ):
            raise ActionNotFoundError
        return workflow

    def _supersede_challenge(self, session, revision, now, subject_id) -> None:
        challenge = self._challenges.lock_active_challenge(
            session,
            action_id=revision.action_id,
            revision=revision.revision,
        )
        if challenge is None:
            return
        challenge.status = ChallengeStatus.SUPERSEDED.value
        challenge.superseded_at = now
        self._audits.insert(
            session,
            NewAuditEvent(
                action_id=revision.action_id,
                revision=revision.revision,
                event_type=AUDIT_CHALLENGE_SUPERSEDED,
                actor_type=ActorType.EMPLOYEE,
                actor_subject_id=subject_id,
                from_state=revision.state,
                to_state=revision.state,
                safe_metadata={"challenge_id": str(challenge.challenge_id)},
            ),
        )


def _action_view(workflow, revision) -> ActionView:
    return ActionView(
        action_id=workflow.action_id,
        revision=revision.revision,
        action_type=workflow.action_type,
        state=revision.state,
        draft=dict(revision.draft_payload),
        action_expires_at=revision.action_expires_at,
        confirmed_expires_at=revision.confirmed_expires_at,
        confirmation_required=True,
        manual_review_required=revision.manual_review_required,
    )

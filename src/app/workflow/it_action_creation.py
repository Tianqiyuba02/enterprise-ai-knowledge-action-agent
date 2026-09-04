"""Create or reuse one authoritative IT action from a non-executing PREPARE result."""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings, load_knowledge_settings
from app.identity import AuthenticatedEmployeeContext
from app.it.domain import (
    IT_CALENDAR_VERSION,
    IT_RULESET_VERSION,
    PreparedITSupportTicket,
    authoritative_it_draft,
    it_authority_hash,
    it_business_request_key,
    parse_authoritative_it_draft,
)
from app.workflow.action_creation import (
    AUDIT_ACTION_PREPARED,
    PREPARE_CONTENTION_ATTEMPTS,
    ActionCreationDisposition,
    ActionCreationResult,
    require_v4_execution_identity,
)
from app.workflow.audit_repository import AuditRepository, NewAuditEvent
from app.workflow.domain import ActionType, ActorType, WorkflowState
from app.workflow.occupancy import is_occupancy_unique_violation
from app.workflow.time import database_now
from app.workflow.workflow_repository import NewWorkflowRevision, WorkflowRepository


class ITActionCreationService:
    """Persist IT PREPARE truth; the model never supplies identity or action authority."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: KnowledgeSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or load_knowledge_settings()
        self._workflows = WorkflowRepository()
        self._audits = AuditRepository()

    def create_or_reuse(
        self,
        context: AuthenticatedEmployeeContext,
        prepared: PreparedITSupportTicket,
        initiation_id: UUID,
    ) -> ActionCreationResult:
        subject_id, _session_id, jurisdiction = require_v4_execution_identity(context)
        authority_hash = it_authority_hash(context)
        draft = authoritative_it_draft(prepared, authority_hash=authority_hash)
        business_key = it_business_request_key(
            owner_subject_id=subject_id,
            initiation_id=initiation_id,
        )

        for _attempt in range(PREPARE_CONTENTION_ATTEMPTS):
            created = self._attempt_insert(
                context=context,
                subject_id=subject_id,
                jurisdiction=jurisdiction,
                draft=draft,
                business_key=business_key,
            )
            if created is not None:
                return created
            existing = self._resolve_existing(
                context=context,
                subject_id=subject_id,
                business_key=business_key,
            )
            if existing is not None:
                return existing
        return ActionCreationResult(
            disposition=ActionCreationDisposition.RETRYABLE_CONFLICT,
            ineligibility_reason="retryable_conflict",
        )

    def _attempt_insert(
        self,
        *,
        context,
        subject_id,
        jurisdiction,
        draft,
        business_key,
    ) -> ActionCreationResult | None:
        with self._session_factory() as session:
            try:
                now = database_now(session)
                workflow, revision = self._workflows.create_workflow_and_revision(
                    session,
                    NewWorkflowRevision(
                        owner_subject_id=subject_id,
                        owner_employee_id=context.employee_id,
                        jurisdiction=jurisdiction,
                        action_type=ActionType.CREATE_IT_SUPPORT_TICKET,
                        state=WorkflowState.AWAITING_CONFIRMATION,
                        draft_payload=draft.payload(),
                        draft_hash=draft.fingerprint(),
                        authority_snapshot_hash=draft.authority_snapshot_hash,
                        business_request_key=business_key,
                        ruleset_version=IT_RULESET_VERSION,
                        calendar_version=IT_CALENDAR_VERSION,
                        action_expires_at=(
                            now + timedelta(seconds=self._settings.v4_action_ttl_seconds)
                        ),
                        action_id=uuid4(),
                    ),
                )
                self._audits.insert(
                    session,
                    NewAuditEvent(
                        action_id=workflow.action_id,
                        revision=revision.revision,
                        event_type=AUDIT_ACTION_PREPARED,
                        actor_type=ActorType.EMPLOYEE,
                        actor_subject_id=subject_id,
                        to_state=WorkflowState.AWAITING_CONFIRMATION.value,
                        safe_metadata={
                            "disposition": ActionCreationDisposition.CREATED.value,
                            "domain": "it_support",
                        },
                    ),
                )
                session.commit()
                return _result(workflow, revision, ActionCreationDisposition.CREATED)
            except IntegrityError as exc:
                session.rollback()
                if is_occupancy_unique_violation(exc):
                    return None
                raise

    def _resolve_existing(
        self,
        *,
        context,
        subject_id,
        business_key,
    ) -> ActionCreationResult | None:
        with self._session_factory() as session:
            occupying = self._workflows.lock_occupying_revision_for_business_request(
                session,
                business_key,
            )
            if occupying is None:
                session.rollback()
                return None
            workflow, revision = occupying
            if (
                workflow.owner_employee_id != context.employee_id
                or workflow.owner_subject_id != subject_id
                or workflow.action_type != ActionType.CREATE_IT_SUPPORT_TICKET.value
                or workflow.current_revision != revision.revision
                or revision.business_request_key != business_key
            ):
                session.rollback()
                return ActionCreationResult(
                    disposition=ActionCreationDisposition.NOT_CREATED,
                    ineligibility_reason="authority_inconsistent",
                )
            persisted = parse_authoritative_it_draft(revision.draft_payload)
            if (
                persisted.fingerprint() != revision.draft_hash
                or persisted.authority_snapshot_hash != revision.authority_snapshot_hash
            ):
                session.rollback()
                return ActionCreationResult(
                    disposition=ActionCreationDisposition.NOT_CREATED,
                    ineligibility_reason="authority_inconsistent",
                )
            disposition = (
                ActionCreationDisposition.RETURNED_SUCCEEDED
                if revision.state == WorkflowState.SUCCEEDED.value
                else ActionCreationDisposition.REUSED_EXISTING
            )
            session.commit()
            return _result(workflow, revision, disposition)


def _result(workflow, revision, disposition) -> ActionCreationResult:
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

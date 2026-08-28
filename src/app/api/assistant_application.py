"""Application orchestration after V3 AgentService. The LLM does not persist actions."""

import logging
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.agent.service import AgentService
from app.api.assistant_models import (
    AssistantActionNotCreatedReason,
    AssistantActionStatus,
    AssistantDurableAction,
    AssistantQueryResponse,
    map_agent_result,
)
from app.config import KnowledgeSettings
from app.errors import ActionCreationIdentityError
from app.identity import AuthenticatedEmployeeContext
from app.workflow.action_creation import ActionCreationDisposition, ActionCreationResult
from app.workflow.errors import WorkflowError
from app.workflow.orchestration import WorkflowOrchestrationService

logger = logging.getLogger(__name__)

REUSED_DISPOSITIONS = frozenset(
    {
        ActionCreationDisposition.REUSED_EXISTING,
        ActionCreationDisposition.RETURNED_IN_FLIGHT,
        ActionCreationDisposition.RETURNED_SUCCEEDED,
    }
)


class NoOpActionCreationService:
    """Test-only stand-in. Production always uses ActionCreationService."""

    def create_or_reuse(self, context, prepared) -> ActionCreationResult:
        return ActionCreationResult(
            disposition=ActionCreationDisposition.NOT_CREATED,
            ineligibility_reason="not_configured",
        )


class AssistantApplicationService:
    """Run AgentService, then create or reuse a V4 action from the trusted PREPARE result."""

    def __init__(
        self,
        agent_service: AgentService,
        action_creation,
        *,
        session_factory: sessionmaker[Session] | None = None,
        settings: KnowledgeSettings | None = None,
        orchestration: WorkflowOrchestrationService | None = None,
    ) -> None:
        self._agent = agent_service
        self._actions = action_creation
        self._settings = settings
        self._orchestration = orchestration
        if self._orchestration is None and session_factory is not None:
            self._orchestration = WorkflowOrchestrationService(session_factory)

    def query(self, message: str, context: AuthenticatedEmployeeContext) -> AssistantQueryResponse:
        result = self._agent.run(message, context)
        public = map_agent_result(result)
        if result.prepared_leave_request is None:
            return public
        try:
            created = self._actions.create_or_reuse(context, result.prepared_leave_request)
        except (ActionCreationIdentityError, WorkflowError, SQLAlchemyError):
            logger.info("assistant_action_creation_failed")
            return public.model_copy(
                update={"action_status": AssistantActionStatus.CREATION_FAILED}
            )
        if not created.has_action or created.action_id is None:
            return public.model_copy(
                update={
                    "action_status": AssistantActionStatus.NOT_CREATED,
                    "action_not_created_reason": _public_not_created_reason(
                        created.ineligibility_reason
                    ),
                }
            )
        if created.action_id is not None and context.subject_id:
            self._ensure_checkpoint(created.action_id, context.subject_id)
        return public.model_copy(
            update={
                "action": _public_action(created),
                "action_status": _status_for(created.disposition),
            }
        )

    def _ensure_checkpoint(self, action_id: UUID, owner_subject_id: str) -> None:
        if self._orchestration is None:
            return
        try:
            self._orchestration.ensure_started(
                action_id=action_id,
                owner_subject_id=owner_subject_id,
                settings=self._settings,
            )
        except Exception:
            logger.info("assistant_graph_initialization_failed")


def _status_for(disposition: ActionCreationDisposition) -> AssistantActionStatus | None:
    if disposition is ActionCreationDisposition.CREATED:
        return AssistantActionStatus.CREATED
    if disposition in REUSED_DISPOSITIONS:
        return AssistantActionStatus.REUSED
    if disposition is ActionCreationDisposition.NOT_CREATED:
        return AssistantActionStatus.NOT_CREATED
    return None


def _public_not_created_reason(reason: str | None) -> AssistantActionNotCreatedReason:
    try:
        return AssistantActionNotCreatedReason(reason or "")
    except ValueError:
        return AssistantActionNotCreatedReason.NOT_EXECUTABLE


def _public_action(created: ActionCreationResult) -> AssistantDurableAction:
    if created.draft is None:
        raise ValueError("durable action result is missing persisted draft")
    return AssistantDurableAction(
        action_id=str(created.action_id),
        revision=created.revision or 1,
        action_type=created.action_type or "submit_annual_leave",
        state=created.state or "",
        draft=dict(created.draft),
        action_expires_at=created.action_expires_at,
        confirmation_required=created.confirmation_required,
        authority="authoritative",
    )

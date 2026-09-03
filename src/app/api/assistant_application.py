"""Application orchestration after V3 AgentService. The LLM does not persist actions."""

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
from app.workflow.it_action_creation import ITActionCreationService

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
        it_action_creation: ITActionCreationService | None = None,
        *,
        session_factory: sessionmaker[Session] | None = None,
        settings: KnowledgeSettings | None = None,
    ) -> None:
        del session_factory, settings
        self._agent = agent_service
        self._actions = action_creation
        self._it_actions = it_action_creation

    def query(
        self,
        message: str,
        context: AuthenticatedEmployeeContext,
        *,
        initiation_id=None,
    ) -> AssistantQueryResponse:
        if _is_standalone_authorization(message):
            return AssistantQueryResponse(
                status="completed",
                answer=(
                    "Chat cannot authorize or execute a request. Open the independent Review "
                    "surface for the current persisted draft."
                ),
                citations=(),
            )
        result = self._agent.run(message, context)
        public = map_agent_result(result)
        if result.prepared_leave_request is None and result.prepared_it_support_ticket is None:
            return public
        if result.prepared_it_support_ticket is not None:
            if initiation_id is None:
                return public.model_copy(
                    update={
                        "action_status": AssistantActionStatus.NOT_CREATED,
                        "action_not_created_reason": (
                            AssistantActionNotCreatedReason.MISSING_INITIATION_ID
                        ),
                    }
                )
            if self._it_actions is None:
                return public.model_copy(
                    update={"action_status": AssistantActionStatus.CREATION_FAILED}
                )
            try:
                created = self._it_actions.create_or_reuse(
                    context,
                    result.prepared_it_support_ticket,
                    initiation_id,
                )
            except (ActionCreationIdentityError, WorkflowError, SQLAlchemyError):
                return public.model_copy(
                    update={"action_status": AssistantActionStatus.CREATION_FAILED}
                )
            return _with_created_action(public, created)
        try:
            created = self._actions.create_or_reuse(context, result.prepared_leave_request)
        except (ActionCreationIdentityError, WorkflowError, SQLAlchemyError):
            return public.model_copy(
                update={"action_status": AssistantActionStatus.CREATION_FAILED}
            )
        return _with_created_action(public, created)


def _with_created_action(
    public: AssistantQueryResponse,
    created: ActionCreationResult,
) -> AssistantQueryResponse:
    if not created.has_action or created.action_id is None:
        return public.model_copy(
            update={
                "action_status": AssistantActionStatus.NOT_CREATED,
                "action_not_created_reason": _public_not_created_reason(
                    created.ineligibility_reason
                ),
            }
        )
    return public.model_copy(
        update={
            "action": _public_action(created),
            "action_status": _status_for(created.disposition),
        }
    )


def _is_standalone_authorization(message: str) -> bool:
    normalized = " ".join(message.lower().replace(",", " ").replace(".", " ").split())
    return normalized in {
        "yes",
        "yes create it",
        "yes submit it",
        "create it",
        "submit it",
        "confirm it",
    }


def _status_for(disposition: ActionCreationDisposition) -> AssistantActionStatus | None:
    if disposition is ActionCreationDisposition.CREATED:
        return AssistantActionStatus.CREATED
    if disposition in REUSED_DISPOSITIONS:
        return AssistantActionStatus.REUSED
    if disposition in {
        ActionCreationDisposition.NOT_CREATED,
        ActionCreationDisposition.RETRYABLE_CONFLICT,
    }:
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

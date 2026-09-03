"""Authenticated out-of-band action read, confirmation, and cancel routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import (
    get_authenticated_employee,
    get_confirmation_service,
    get_it_action_revision_service,
    get_portal_read_service,
)
from app.api.models import (
    ActionResponse,
    ConfirmActionRequest,
    ConfirmationChallengeResponse,
    ErrorResponse,
)
from app.api.portal_models import ActionDetail
from app.identity import AuthenticatedEmployeeContext
from app.it.domain import ReviseITSupportTicketRequest
from app.portal.service import PortalReadService
from app.workflow.confirmation import ActionView, ConfirmationService, IssuedChallenge
from app.workflow.it_revision import ITActionRevisionService

router = APIRouter(prefix="/actions", tags=["actions"])

ACTION_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Invalid demo session"},
    404: {"model": ErrorResponse, "description": "Owned action not found"},
    409: {"model": ErrorResponse, "description": "Action or challenge state conflict"},
    422: {"model": ErrorResponse, "description": "Invalid request"},
}


@router.get(
    "/{action_id}",
    response_model=ActionResponse,
    responses=ACTION_RESPONSES,
)
def get_action(
    action_id: UUID,
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    service: Annotated[ConfirmationService, Depends(get_confirmation_service)],
) -> ActionResponse:
    return _action_response(service.get_action(action_id=action_id, context=context))


@router.get(
    "/{action_id}/detail",
    response_model=ActionDetail,
    responses={
        **ACTION_RESPONSES,
        503: {"model": ErrorResponse, "description": "Portal read unavailable"},
    },
)
def get_action_detail(
    action_id: UUID,
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    confirmation: Annotated[ConfirmationService, Depends(get_confirmation_service)],
    portal: Annotated[PortalReadService, Depends(get_portal_read_service)],
) -> ActionDetail:
    # Preserve the V4 read-side expiry normalization before projecting detail.
    confirmation.get_action(action_id=action_id, context=context)
    return portal.action_detail(action_id=action_id, context=context)


@router.post(
    "/{action_id}/revisions",
    response_model=ActionResponse,
    responses=ACTION_RESPONSES,
)
def create_action_revision(
    action_id: UUID,
    payload: ReviseITSupportTicketRequest,
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    service: Annotated[ITActionRevisionService, Depends(get_it_action_revision_service)],
) -> ActionResponse:
    return _action_response(
        service.create_revision(action_id=action_id, request=payload, context=context)
    )


@router.post(
    "/{action_id}/confirmation-challenges",
    response_model=ConfirmationChallengeResponse,
    responses=ACTION_RESPONSES,
)
def issue_confirmation_challenge(
    action_id: UUID,
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    service: Annotated[ConfirmationService, Depends(get_confirmation_service)],
) -> ConfirmationChallengeResponse:
    issued = service.issue_challenge(action_id=action_id, context=context)
    return _challenge_response(issued)


@router.post(
    "/{action_id}/confirm",
    response_model=ActionResponse,
    responses=ACTION_RESPONSES,
)
def confirm_action(
    action_id: UUID,
    payload: ConfirmActionRequest,
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    service: Annotated[ConfirmationService, Depends(get_confirmation_service)],
) -> ActionResponse:
    return _action_response(
        service.confirm(
            action_id=action_id,
            challenge_id=payload.challenge_id,
            confirmation_token=payload.confirmation_token,
            context=context,
        )
    )


@router.post(
    "/{action_id}/cancel",
    response_model=ActionResponse,
    responses=ACTION_RESPONSES,
)
def cancel_action(
    action_id: UUID,
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    service: Annotated[ConfirmationService, Depends(get_confirmation_service)],
) -> ActionResponse:
    return _action_response(service.cancel(action_id=action_id, context=context))


def _action_response(view: ActionView) -> ActionResponse:
    return ActionResponse(
        action_id=str(view.action_id),
        revision=view.revision,
        action_type=view.action_type,
        state=view.state,
        draft=view.draft,
        action_expires_at=view.action_expires_at,
        confirmed_expires_at=view.confirmed_expires_at,
        confirmation_required=view.confirmation_required,
        manual_review_required=view.manual_review_required,
    )


def _challenge_response(issued: IssuedChallenge) -> ConfirmationChallengeResponse:
    return ConfirmationChallengeResponse(
        challenge_id=str(issued.challenge_id),
        confirmation_token=issued.confirmation_token,
        expires_at=issued.expires_at,
        action_id=str(issued.action.action_id),
        revision=issued.action.revision,
        action=_action_response(issued.action),
    )

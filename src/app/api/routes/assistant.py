"""Authenticated HTTP adapter over the bounded internal V3 AgentService."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.assistant_application import AssistantApplicationService
from app.api.assistant_models import AssistantQueryRequest, AssistantQueryResponse
from app.api.dependencies import (
    get_assistant_application_service,
    get_authenticated_employee,
    get_demo_control_service,
    get_demo_visitor_id,
    require_demo_mutation_window,
)
from app.api.models import ErrorResponse
from app.config import load_public_demo_settings
from app.demo.service import DemoControlService
from app.identity import AuthenticatedEmployeeContext

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post(
    "/query",
    response_model=AssistantQueryResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid demo session"},
        422: {"model": ErrorResponse, "description": "Invalid request"},
        503: {"model": ErrorResponse, "description": "Assistant model unavailable"},
    },
)
def query_assistant(
    payload: AssistantQueryRequest,
    context: Annotated[
        AuthenticatedEmployeeContext,
        Depends(get_authenticated_employee),
    ],
    service: Annotated[AssistantApplicationService, Depends(get_assistant_application_service)],
    demo: Annotated[DemoControlService, Depends(get_demo_control_service)],
    visitor_id: Annotated[str | None, Depends(get_demo_visitor_id)],
    _mutation_window: Annotated[None, Depends(require_demo_mutation_window)],
) -> AssistantQueryResponse:
    if load_public_demo_settings().enabled:
        return service.query(
            payload.message,
            context,
            initiation_id=payload.initiation_id,
            demo_control=demo,
            visitor_id=visitor_id,
        )
    return service.query(payload.message, context, initiation_id=payload.initiation_id)

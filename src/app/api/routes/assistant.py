"""Authenticated HTTP adapter over the bounded internal V3 AgentService."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.agent.service import AgentService
from app.api.assistant_models import (
    AssistantQueryRequest,
    AssistantQueryResponse,
    map_agent_result,
)
from app.api.dependencies import get_agent_service, get_authenticated_employee
from app.api.models import ErrorResponse
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
    service: Annotated[AgentService, Depends(get_agent_service)],
) -> AssistantQueryResponse:
    return map_agent_result(service.run(payload.message, context))

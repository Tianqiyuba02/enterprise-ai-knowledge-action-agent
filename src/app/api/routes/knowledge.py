"""Authenticated HTTP adapter for the linear V2 knowledge-query service."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import (
    get_knowledge_applicability_context,
    get_knowledge_query_service,
)
from app.api.knowledge_models import KnowledgeQueryRequest, KnowledgeQueryResponse
from app.api.models import ErrorResponse
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.query_service import KnowledgeQueryService

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post(
    "/query",
    response_model=KnowledgeQueryResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid demo session"},
        422: {"model": ErrorResponse, "description": "Invalid request"},
        502: {"model": ErrorResponse, "description": "Invalid provider response"},
        503: {"model": ErrorResponse, "description": "Knowledge service unavailable"},
        504: {"model": ErrorResponse, "description": "Knowledge service timeout"},
    },
)
def query_knowledge(
    payload: KnowledgeQueryRequest,
    applicability: Annotated[
        KnowledgeApplicabilityContext,
        Depends(get_knowledge_applicability_context),
    ],
    service: Annotated[KnowledgeQueryService, Depends(get_knowledge_query_service)],
) -> KnowledgeQueryResponse:
    return service.query(payload.question, applicability)

"""Authenticated HTTP adapter for the linear V2 knowledge-query service."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.dependencies import (
    get_knowledge_applicability_context,
    get_knowledge_query_service,
    get_portal_read_service,
)
from app.api.knowledge_models import KnowledgeQueryRequest, KnowledgeQueryResponse
from app.api.models import ErrorResponse
from app.api.portal_models import PolicyDocumentDetailResponse, PolicyDocumentListResponse
from app.knowledge.clock import MelbourneClock
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.query_service import KnowledgeQueryService
from app.portal.service import PortalReadService

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


POLICY_READ_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Invalid demo session"},
    404: {"model": ErrorResponse, "description": "Policy document not found"},
    422: {"model": ErrorResponse, "description": "Invalid request"},
    503: {"model": ErrorResponse, "description": "Portal read unavailable"},
}


@router.get(
    "/documents",
    response_model=PolicyDocumentListResponse,
    responses=POLICY_READ_RESPONSES,
)
def list_policy_documents(
    applicability: Annotated[
        KnowledgeApplicabilityContext,
        Depends(get_knowledge_applicability_context),
    ],
    service: Annotated[PortalReadService, Depends(get_portal_read_service)],
) -> PolicyDocumentListResponse:
    return service.list_policy_documents(applicability, trusted_today=MelbourneClock().today())


@router.get(
    "/documents/{doc_code}/versions/{version}",
    response_model=PolicyDocumentDetailResponse,
    responses=POLICY_READ_RESPONSES,
)
def get_policy_document(
    doc_code: Annotated[str, Path(min_length=1, max_length=100)],
    version: Annotated[str, Path(min_length=1, max_length=100)],
    applicability: Annotated[
        KnowledgeApplicabilityContext,
        Depends(get_knowledge_applicability_context),
    ],
    service: Annotated[PortalReadService, Depends(get_portal_read_service)],
) -> PolicyDocumentDetailResponse:
    return service.policy_document(
        doc_code,
        version,
        applicability,
        trusted_today=MelbourneClock().today(),
    )

"""HTTP adapter for the existing V0 structured LLM capability."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_chat_service
from app.api.models import ChatRequest, ChatResponse, ErrorResponse
from app.services.chat import ChatService

router = APIRouter(tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Invalid request"},
        502: {"model": ErrorResponse, "description": "Invalid model response"},
        503: {"model": ErrorResponse, "description": "Model service unavailable"},
        504: {"model": ErrorResponse, "description": "Model service timeout"},
    },
)
def chat(
    payload: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse:
    return ChatResponse.from_analysis(service.analyze_question(payload.question))

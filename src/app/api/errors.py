"""Safe HTTP mappings for validation, application, and provider failures."""

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agent.errors import (
    AssistantModelError,
    AssistantModelRateLimitedError,
    AssistantModelUnavailableError,
)
from app.api.models import ErrorResponse
from app.config import (
    AgentConfigurationError,
    ConfigurationError,
    KnowledgeConfigurationError,
)
from app.embeddings.client import (
    EmbeddingAuthenticationError,
    EmbeddingClientError,
    EmbeddingRateLimitError,
    EmbeddingServiceError,
    EmbeddingTimeoutError,
    InvalidEmbeddingResponseError,
)
from app.errors import (
    ActionConflictError,
    ActionCreationIdentityError,
    ActionNotFoundError,
    ApplicationError,
    ConfirmationInvalidError,
    EmployeeNotFoundError,
    InvalidDemoSessionError,
    PolicyDocumentNotFoundError,
    PortalReadUnavailableError,
    TicketNotFoundError,
)
from app.grounding.client import (
    GroundedAuthenticationError,
    GroundedGenerationError,
    GroundedRateLimitError,
    GroundedServiceError,
    GroundedTimeoutError,
    InvalidGroundedResponseError,
)
from app.knowledge.errors import (
    InvalidKnowledgeQuestionError,
    InvalidQueryVectorError,
    KnowledgeDatabaseError,
    KnowledgeRetrievalError,
)
from app.llm.client import (
    AuthenticationError,
    InvalidModelResponseError,
    InvalidQuestionError,
    LLMClientError,
    ProviderServiceError,
    ProviderTimeoutError,
    RateLimitError,
)

logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unavailable"))


def _error_response(
    request: Request, *, status_code: int, error_code: str, message: str
) -> JSONResponse:
    payload = ErrorResponse(
        error_code=error_code,
        message=message,
        request_id=_request_id(request),
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
        headers={"X-Request-ID": payload.request_id},
    )


async def application_error_handler(request: Request, exc: ApplicationError) -> JSONResponse:
    status_code = {
        InvalidDemoSessionError: HTTPStatus.UNAUTHORIZED,
        EmployeeNotFoundError: HTTPStatus.NOT_FOUND,
        TicketNotFoundError: HTTPStatus.NOT_FOUND,
        ActionNotFoundError: HTTPStatus.NOT_FOUND,
        ConfirmationInvalidError: HTTPStatus.CONFLICT,
        ActionConflictError: HTTPStatus.CONFLICT,
        ActionCreationIdentityError: HTTPStatus.BAD_REQUEST,
        PortalReadUnavailableError: HTTPStatus.SERVICE_UNAVAILABLE,
        PolicyDocumentNotFoundError: HTTPStatus.NOT_FOUND,
    }.get(type(exc), HTTPStatus.BAD_REQUEST)
    logger.info(
        "application_error request_id=%s error_code=%s",
        _request_id(request),
        exc.error_code,
    )
    return _error_response(
        request,
        status_code=status_code,
        error_code=exc.error_code,
        message=exc.public_message,
    )


async def llm_error_handler(request: Request, exc: LLMClientError) -> JSONResponse:
    if isinstance(exc, InvalidQuestionError):
        status_code, error_code, message = (
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "invalid_question",
            "The question is invalid.",
        )
    elif isinstance(exc, ProviderTimeoutError):
        status_code, error_code, message = (
            HTTPStatus.GATEWAY_TIMEOUT,
            "model_timeout",
            "The model service timed out. Please try again.",
        )
    elif isinstance(exc, InvalidModelResponseError):
        status_code, error_code, message = (
            HTTPStatus.BAD_GATEWAY,
            "invalid_model_response",
            "The model service returned an invalid response. Please try again.",
        )
    elif isinstance(exc, RateLimitError):
        status_code, error_code, message = (
            HTTPStatus.SERVICE_UNAVAILABLE,
            "model_rate_limited",
            "The model service is busy. Please try again later.",
        )
    elif isinstance(exc, (AuthenticationError, ProviderServiceError)):
        status_code, error_code, message = (
            HTTPStatus.SERVICE_UNAVAILABLE,
            "model_service_unavailable",
            "The model service is unavailable. Please try again later.",
        )
    else:
        status_code, error_code, message = (
            HTTPStatus.BAD_GATEWAY,
            "model_error",
            "The model request could not be completed.",
        )
    logger.info("llm_error request_id=%s error_code=%s", _request_id(request), error_code)
    return _error_response(
        request,
        status_code=status_code,
        error_code=error_code,
        message=message,
    )


async def configuration_error_handler(request: Request, _exc: ConfigurationError) -> JSONResponse:
    return _error_response(
        request,
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        error_code="model_not_configured",
        message="The model service is not configured.",
    )


async def knowledge_configuration_error_handler(
    request: Request, _exc: KnowledgeConfigurationError
) -> JSONResponse:
    return _error_response(
        request,
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        error_code="knowledge_not_configured",
        message="The knowledge service is not configured.",
    )


async def agent_configuration_error_handler(
    request: Request, _exc: AgentConfigurationError
) -> JSONResponse:
    return _error_response(
        request,
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        error_code="assistant_not_configured",
        message="The assistant service is not configured.",
    )


async def assistant_model_error_handler(
    request: Request,
    exc: AssistantModelError,
) -> JSONResponse:
    if isinstance(exc, AssistantModelRateLimitedError):
        error_code = "assistant_model_rate_limited"
        message = "The assistant model is busy. Please try again later."
    elif isinstance(exc, AssistantModelUnavailableError):
        error_code = "assistant_model_unavailable"
        message = "The assistant model is temporarily unavailable."
    else:
        error_code = "assistant_model_error"
        message = "The assistant model could not complete the request."
    logger.info("assistant_error request_id=%s error_code=%s", _request_id(request), error_code)
    return _error_response(
        request,
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        error_code=error_code,
        message=message,
    )


async def knowledge_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, InvalidKnowledgeQuestionError):
        status_code, error_code, message = (
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "invalid_knowledge_question",
            "The knowledge question is invalid.",
        )
    elif isinstance(exc, (InvalidQueryVectorError, InvalidEmbeddingResponseError)):
        status_code, error_code, message = (
            HTTPStatus.BAD_GATEWAY,
            "invalid_query_embedding",
            "The knowledge embedding service returned an invalid response.",
        )
    elif isinstance(exc, KnowledgeDatabaseError):
        status_code, error_code, message = (
            HTTPStatus.SERVICE_UNAVAILABLE,
            "knowledge_service_unavailable",
            "The knowledge service is unavailable. Please try again later.",
        )
    elif isinstance(exc, EmbeddingTimeoutError):
        status_code, error_code, message = (
            HTTPStatus.GATEWAY_TIMEOUT,
            "knowledge_embedding_timeout",
            "The knowledge embedding service timed out. Please try again.",
        )
    elif isinstance(exc, EmbeddingRateLimitError):
        status_code, error_code, message = (
            HTTPStatus.SERVICE_UNAVAILABLE,
            "knowledge_embedding_rate_limited",
            "The knowledge embedding service is busy. Please try again later.",
        )
    elif isinstance(exc, (EmbeddingAuthenticationError, EmbeddingServiceError)):
        status_code, error_code, message = (
            HTTPStatus.SERVICE_UNAVAILABLE,
            "knowledge_embedding_unavailable",
            "The knowledge embedding service is unavailable.",
        )
    elif isinstance(exc, GroundedTimeoutError):
        status_code, error_code, message = (
            HTTPStatus.GATEWAY_TIMEOUT,
            "knowledge_model_timeout",
            "The grounded knowledge service timed out. Please try again.",
        )
    elif isinstance(exc, InvalidGroundedResponseError):
        status_code, error_code, message = (
            HTTPStatus.BAD_GATEWAY,
            "invalid_grounded_response",
            "The grounded knowledge service returned an invalid response.",
        )
    elif isinstance(exc, GroundedRateLimitError):
        status_code, error_code, message = (
            HTTPStatus.SERVICE_UNAVAILABLE,
            "knowledge_model_rate_limited",
            "The grounded knowledge service is busy. Please try again later.",
        )
    elif isinstance(exc, (GroundedAuthenticationError, GroundedServiceError)):
        status_code, error_code, message = (
            HTTPStatus.SERVICE_UNAVAILABLE,
            "knowledge_model_unavailable",
            "The grounded knowledge service is unavailable.",
        )
    else:
        status_code, error_code, message = (
            HTTPStatus.BAD_GATEWAY,
            "knowledge_error",
            "The knowledge request could not be completed.",
        )
    logger.info("knowledge_error request_id=%s error_code=%s", _request_id(request), error_code)
    return _error_response(
        request,
        status_code=status_code,
        error_code=error_code,
        message=message,
    )


async def validation_error_handler(request: Request, _exc: RequestValidationError) -> JSONResponse:
    return _error_response(
        request,
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        error_code="validation_error",
        message="Request validation failed.",
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    public_messages = {
        HTTPStatus.NOT_FOUND: ("not_found", "The requested resource was not found."),
        HTTPStatus.METHOD_NOT_ALLOWED: ("method_not_allowed", "The method is not allowed."),
    }
    error_code, message = public_messages.get(
        exc.status_code,
        ("http_error", "The HTTP request could not be completed."),
    )
    return _error_response(
        request,
        status_code=exc.status_code,
        error_code=error_code,
        message=message,
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unexpected_error request_id=%s exception_type=%s",
        _request_id(request),
        type(exc).__name__,
    )
    return _error_response(
        request,
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        error_code="internal_error",
        message="The request could not be completed.",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApplicationError, application_error_handler)
    app.add_exception_handler(LLMClientError, llm_error_handler)
    app.add_exception_handler(ConfigurationError, configuration_error_handler)
    app.add_exception_handler(
        KnowledgeConfigurationError,
        knowledge_configuration_error_handler,
    )
    app.add_exception_handler(AgentConfigurationError, agent_configuration_error_handler)
    app.add_exception_handler(AssistantModelError, assistant_model_error_handler)
    app.add_exception_handler(KnowledgeRetrievalError, knowledge_error_handler)
    app.add_exception_handler(EmbeddingClientError, knowledge_error_handler)
    app.add_exception_handler(GroundedGenerationError, knowledge_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)

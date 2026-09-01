"""FastAPI application factory for the V1 modular monolith."""

import logging
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from app import __version__
from app.agent.service import AgentService
from app.api.errors import register_error_handlers
from app.api.routes import actions, assistant, chat, health, knowledge, me
from app.knowledge.query_service import KnowledgeQueryService
from app.llm.client import GeminiStructuredClient
from app.repositories.demo import DemoRepository
from app.services.chat import ChatService
from app.services.employee import EmployeeService
from app.services.it import ITService

API_PREFIX = "/api/v1"
REQUEST_ID_HEADER = "X-Request-ID"
logger = logging.getLogger(__name__)


def create_app(
    *,
    repository: DemoRepository | None = None,
    llm_client: GeminiStructuredClient | None = None,
    knowledge_query_service: KnowledgeQueryService | None = None,
    agent_service: AgentService | None = None,
) -> FastAPI:
    """Construct an isolated application suitable for runtime and offline tests."""

    demo_repository = repository or DemoRepository()
    app = FastAPI(
        title="Enterprise AI Knowledge & Action Agent",
        description="Released V1/V2 APIs plus the authenticated V3 read assistant.",
        version=__version__,
    )
    app.state.demo_repository = demo_repository
    app.state.employee_service = EmployeeService(demo_repository)
    app.state.it_service = ITService(demo_repository)
    app.state.chat_service = ChatService(llm_client) if llm_client is not None else None
    app.state.knowledge_query_service = knowledge_query_service
    app.state.knowledge_engine = None
    app.state.agent_service = agent_service

    register_error_handlers(app)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "request_completed request_id=%s method=%s path=%s status_code=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
        )
        return response

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(chat.router, prefix=API_PREFIX)
    app.include_router(me.router, prefix=API_PREFIX)
    app.include_router(knowledge.router, prefix=API_PREFIX)
    app.include_router(assistant.router, prefix=API_PREFIX)
    app.include_router(actions.router, prefix=API_PREFIX)
    return app

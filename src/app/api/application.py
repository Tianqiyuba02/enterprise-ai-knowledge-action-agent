"""FastAPI application factory for the V1 modular monolith."""

import logging
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from app import __version__
from app.api.errors import register_error_handlers
from app.api.routes import chat, health, me
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
) -> FastAPI:
    """Construct an isolated application suitable for runtime and offline tests."""

    demo_repository = repository or DemoRepository()
    app = FastAPI(
        title="Enterprise AI Knowledge & Action Agent",
        description="V1 REST API with trusted synthetic employee identity.",
        version=__version__,
    )
    app.state.employee_service = EmployeeService(demo_repository)
    app.state.it_service = ITService(demo_repository)
    app.state.chat_service = ChatService(llm_client) if llm_client is not None else None

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
    return app

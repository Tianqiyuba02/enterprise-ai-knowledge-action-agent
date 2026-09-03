"""FastAPI application factory for the V1 modular monolith."""

import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from app import __version__
from app.agent.service import AgentService
from app.api.errors import register_error_handlers
from app.api.routes import actions, assistant, chat, demo, health, knowledge, me
from app.config import load_public_demo_settings
from app.knowledge.query_service import KnowledgeQueryService
from app.llm.client import GeminiStructuredClient
from app.portal.service import PortalReadService
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
    portal_read_service: PortalReadService | None = None,
    it_service: ITService | None = None,
) -> FastAPI:
    """Construct an isolated application suitable for runtime and offline tests."""

    demo_repository = repository or DemoRepository()
    public_demo = load_public_demo_settings()
    app = FastAPI(
        title="Enterprise AI Knowledge & Action Agent",
        description="V1–V4 authoritative APIs plus V5 M1/M2 employee-portal capabilities.",
        version=__version__,
        docs_url=None if public_demo.enabled else "/docs",
        redoc_url=None if public_demo.enabled else "/redoc",
        openapi_url=None if public_demo.enabled else "/openapi.json",
    )
    app.state.demo_repository = demo_repository
    app.state.employee_service = EmployeeService(demo_repository)
    app.state.it_service = it_service or (
        ITService(demo_repository) if repository is not None else None
    )
    app.state.chat_service = ChatService(llm_client) if llm_client is not None else None
    app.state.knowledge_query_service = knowledge_query_service
    app.state.knowledge_engine = None
    app.state.agent_service = agent_service
    app.state.portal_read_service = portal_read_service

    register_error_handlers(app)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = uuid4().hex
        started = time.monotonic()
        request.state.request_id = request_id
        if public_demo.enabled and request.url.path != f"{API_PREFIX}/health":
            supplied = request.headers.get("X-Internal-Portal-Key", "")
            if not secrets.compare_digest(supplied, public_demo.require_internal_key()):
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=404,
                    content={
                        "error_code": "not_found",
                        "message": "The requested resource was not found.",
                        "request_id": request_id,
                    },
                    headers={REQUEST_ID_HEADER: request_id},
                )
        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()
            if len(body) > 32_768:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=413,
                    content={
                        "error_code": "request_too_large",
                        "message": "The request is too large.",
                        "request_id": request_id,
                    },
                    headers={REQUEST_ID_HEADER: request_id},
                )
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            json.dumps(
                {
                    "event": "request_completed",
                    "request_id": request_id,
                    "service": "api",
                    "route": request.url.path,
                    "outcome": "success" if response.status_code < 400 else "failure",
                    "status": response.status_code,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                },
                separators=(",", ":"),
            )
        )
        return response

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(chat.router, prefix=API_PREFIX)
    app.include_router(me.router, prefix=API_PREFIX)
    app.include_router(knowledge.router, prefix=API_PREFIX)
    app.include_router(assistant.router, prefix=API_PREFIX)
    app.include_router(actions.router, prefix=API_PREFIX)
    if public_demo.enabled:
        app.include_router(demo.router, prefix=API_PREFIX)
    return app

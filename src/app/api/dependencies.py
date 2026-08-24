"""FastAPI dependencies for trusted identity and application services."""

from types import MappingProxyType
from typing import Annotated, Final, cast

from fastapi import Header, Request

from app.config import load_settings
from app.errors import InvalidDemoSessionError
from app.identity import AuthenticatedEmployeeContext
from app.llm.client import GeminiStructuredClient
from app.services.chat import ChatService
from app.services.employee import EmployeeService
from app.services.it import ITService

DEMO_SESSION_HEADER: Final = "X-Demo-Session"
DEMO_SESSIONS: Final = MappingProxyType(
    {
        "demo-v1-7f4c2a91": "EMP-1001",
        "demo-v1-3b8e6d50": "EMP-1002",
    }
)


def get_authenticated_employee(
    x_demo_session: Annotated[str | None, Header(alias=DEMO_SESSION_HEADER)] = None,
) -> AuthenticatedEmployeeContext:
    """Resolve an opaque demo token to server-controlled employee identity."""

    employee_id = DEMO_SESSIONS.get(x_demo_session or "")
    if employee_id is None:
        raise InvalidDemoSessionError
    return AuthenticatedEmployeeContext(employee_id=employee_id)


def get_employee_service(request: Request) -> EmployeeService:
    return cast(EmployeeService, request.app.state.employee_service)


def get_it_service(request: Request) -> ITService:
    return cast(ITService, request.app.state.it_service)


def get_chat_service(request: Request) -> ChatService:
    """Build the provider-backed service only when the chat endpoint needs it."""

    service = cast(ChatService | None, request.app.state.chat_service)
    if service is None:
        settings = load_settings()
        service = ChatService(GeminiStructuredClient(settings))
        request.app.state.chat_service = service
    return service

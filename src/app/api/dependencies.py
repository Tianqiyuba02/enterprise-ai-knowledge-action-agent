"""FastAPI dependencies for trusted identity and application services."""

from types import MappingProxyType
from typing import Annotated, Final, cast

from fastapi import Depends, Header, Request

from app.agent.client import GeminiAgentClient
from app.agent.dispatcher import ToolDispatcher
from app.agent.service import AgentService
from app.config import load_agent_settings, load_knowledge_settings, load_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.embeddings.client import GeminiDocumentEmbeddingClient
from app.errors import InvalidDemoSessionError
from app.grounding.client import GeminiGroundedGenerationClient
from app.identity import AuthenticatedEmployeeContext
from app.knowledge.applicability import resolve_knowledge_applicability
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.query_service import KnowledgeQueryService
from app.knowledge.repository import KnowledgeRetrievalRepository
from app.knowledge.service import KnowledgeRetrievalService
from app.llm.client import GeminiStructuredClient
from app.repositories.demo import DemoRepository
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


def get_demo_repository(request: Request) -> DemoRepository:
    return cast(DemoRepository, request.app.state.demo_repository)


def get_knowledge_applicability_context(
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    repository: Annotated[DemoRepository, Depends(get_demo_repository)],
) -> KnowledgeApplicabilityContext:
    return resolve_knowledge_applicability(context, repository)


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


def get_knowledge_query_service(request: Request) -> KnowledgeQueryService:
    """Build V2 database/provider dependencies only when the knowledge route needs them."""

    service = cast(KnowledgeQueryService | None, request.app.state.knowledge_query_service)
    if service is None:
        settings = load_settings()
        knowledge_settings = load_knowledge_settings()
        engine = create_knowledge_engine(knowledge_settings)
        embedder = GeminiDocumentEmbeddingClient(settings, knowledge_settings)
        retrieval = KnowledgeRetrievalService(
            embedder=embedder,
            repository=KnowledgeRetrievalRepository(create_knowledge_session_factory(engine)),
        )
        service = KnowledgeQueryService(
            retrieval=retrieval,
            generator=GeminiGroundedGenerationClient(settings, knowledge_settings),
        )
        request.app.state.knowledge_engine = engine
        request.app.state.knowledge_query_service = service
    return service


def get_agent_service(
    request: Request,
) -> AgentService:
    """Build V3 provider/dispatch dependencies only for the assistant route."""

    service = cast(AgentService | None, request.app.state.agent_service)
    if service is None:
        settings = load_settings()
        repository = get_demo_repository(request)
        dispatcher = ToolDispatcher(
            employee_service=get_employee_service(request),
            it_service=get_it_service(request),
            knowledge_service=get_knowledge_query_service(request),
            demo_repository=repository,
        )
        service = AgentService(
            provider=GeminiAgentClient(
                settings,
                load_agent_settings(),
            ),
            dispatcher=dispatcher,
        )
        request.app.state.agent_service = service
    return service

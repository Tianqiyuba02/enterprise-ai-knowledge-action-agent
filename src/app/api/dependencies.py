"""FastAPI dependencies for trusted identity and application services."""

from types import MappingProxyType
from typing import Annotated, Final, cast

from fastapi import Depends, Header, Request

from app.agent.client import GeminiAgentClient
from app.agent.dispatcher import ToolDispatcher
from app.agent.service import AgentService
from app.api.assistant_application import AssistantApplicationService
from app.config import load_agent_settings, load_knowledge_settings, load_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.embeddings.client import GeminiDocumentEmbeddingClient
from app.errors import InvalidDemoSessionError
from app.grounding.client import GeminiGroundedGenerationClient
from app.identity import AuthenticatedEmployeeContext
from app.knowledge.applicability import resolve_knowledge_applicability
from app.knowledge.clock import MelbourneClock
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.query_service import KnowledgeQueryService
from app.knowledge.repository import KnowledgeRetrievalRepository
from app.knowledge.service import KnowledgeRetrievalService
from app.llm.client import GeminiStructuredClient
from app.repositories.demo import DemoRepository
from app.services.chat import ChatService
from app.services.employee import EmployeeService
from app.services.it import ITService
from app.services.leave_preparation import LeavePreparationService
from app.workflow.action_creation import ActionCreationService
from app.workflow.confirmation import ConfirmationService

DEMO_SESSION_HEADER: Final = "X-Demo-Session"
DEMO_IDENTITY_BINDINGS: Final = MappingProxyType(
    {
        "demo-v1-7f4c2a91": AuthenticatedEmployeeContext(
            employee_id="EMP-1001",
            subject_id="subj_9f2c4e81a6b047d3",
            session_id="sess_c4a81f07e2d94b6a",
            jurisdiction="AU-VIC",
        ),
        "demo-v1-3b8e6d50": AuthenticatedEmployeeContext(
            employee_id="EMP-1002",
            subject_id="subj_1a8e5c03d7f249b6",
            session_id="sess_e50b3d6a91c8472f",
            jurisdiction="AU-VIC",
        ),
    }
)
DEMO_SESSIONS: Final = MappingProxyType(
    {token: identity.employee_id for token, identity in DEMO_IDENTITY_BINDINGS.items()}
)


def get_authenticated_employee(
    x_demo_session: Annotated[str | None, Header(alias=DEMO_SESSION_HEADER)] = None,
) -> AuthenticatedEmployeeContext:
    """Resolve an opaque demo token to server-controlled employee identity."""

    identity = DEMO_IDENTITY_BINDINGS.get(x_demo_session or "")
    if identity is None:
        raise InvalidDemoSessionError
    return identity


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


def get_confirmation_service(request: Request) -> ConfirmationService:
    """Build the confirmation control plane only when an action route needs it."""

    service = cast(
        ConfirmationService | None,
        getattr(request.app.state, "confirmation_service", None),
    )
    if service is not None:
        return service
    settings = getattr(request.app.state, "workflow_settings", None) or load_knowledge_settings()
    factory = getattr(request.app.state, "workflow_session_factory", None)
    if factory is None:
        engine = create_knowledge_engine(settings)
        factory = create_knowledge_session_factory(engine)
        request.app.state.workflow_engine = engine
        request.app.state.workflow_session_factory = factory
    service = ConfirmationService(factory, settings)
    request.app.state.confirmation_service = service
    return service


def get_agent_service(
    request: Request,
) -> AgentService:
    """Build V3 provider/dispatch dependencies only for the assistant route."""

    service = cast(AgentService | None, request.app.state.agent_service)
    if service is None:
        settings = load_settings()
        repository = get_demo_repository(request)
        clock = MelbourneClock()
        employee_service = get_employee_service(request)
        dispatcher = ToolDispatcher(
            employee_service=employee_service,
            it_service=get_it_service(request),
            knowledge_service=get_knowledge_query_service(request),
            demo_repository=repository,
            leave_preparation_service=LeavePreparationService(employee_service),
        )
        service = AgentService(
            provider=GeminiAgentClient(
                settings,
                load_agent_settings(),
            ),
            dispatcher=dispatcher,
            clock=clock,
        )
        request.app.state.agent_service = service
    return service


def get_action_creation_service(request: Request):
    service = getattr(request.app.state, "action_creation_service", None)
    if service is not None:
        return service
    settings = getattr(request.app.state, "workflow_settings", None) or load_knowledge_settings()
    factory = getattr(request.app.state, "workflow_session_factory", None)
    if factory is None:
        engine = create_knowledge_engine(settings)
        factory = create_knowledge_session_factory(engine)
        request.app.state.workflow_engine = engine
        request.app.state.workflow_session_factory = factory
    service = ActionCreationService(factory, settings)
    request.app.state.action_creation_service = service
    return service


def get_assistant_application_service(request: Request) -> AssistantApplicationService:
    existing = getattr(request.app.state, "assistant_application_service", None)
    if existing is not None:
        return cast(AssistantApplicationService, existing)
    service = AssistantApplicationService(
        get_agent_service(request),
        get_action_creation_service(request),
    )
    request.app.state.assistant_application_service = service
    return service

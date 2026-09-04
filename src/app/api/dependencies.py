"""FastAPI dependencies for trusted identity and application services."""

from collections.abc import Generator
from types import MappingProxyType
from typing import Annotated, Final, cast

from fastapi import Depends, Header, Request

from app.agent.client import GeminiAgentClient
from app.agent.dispatcher import ToolDispatcher
from app.agent.service import AgentService
from app.api.assistant_application import AssistantApplicationService
from app.config import (
    load_agent_settings,
    load_knowledge_settings,
    load_public_demo_settings,
    load_settings,
)
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.demo.adapters import (
    MeteredAgentClient,
    PublicDemoAssistantApplicationService,
    QuotaActionCreationService,
)
from app.demo.leave_execution import M3ExecutablePreparationService
from app.demo.service import MUTATION_LOCK_ID, DemoControlService
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
from app.portal.service import PortalReadService
from app.repositories.demo import DemoRepository
from app.services.chat import ChatService
from app.services.employee import EmployeeService
from app.services.it import ITService
from app.services.leave_preparation import LeavePreparationService
from app.workflow.action_creation import ActionCreationService
from app.workflow.confirmation import ConfirmationService
from app.workflow.it_action_creation import ITActionCreationService
from app.workflow.it_revision import ITActionRevisionService

DEMO_SESSION_HEADER: Final = "X-Demo-Session"
DEMO_VISITOR_HEADER: Final = "X-Demo-Visitor-ID"
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


def get_demo_visitor_id(
    x_demo_visitor_id: Annotated[str | None, Header(alias=DEMO_VISITOR_HEADER)] = None,
) -> str | None:
    value = (x_demo_visitor_id or "").strip()
    if not value or len(value) > 128:
        return None
    return value


def get_workflow_session_factory(request: Request):
    settings = getattr(request.app.state, "workflow_settings", None) or load_knowledge_settings()
    factory = getattr(request.app.state, "workflow_session_factory", None)
    if factory is None:
        engine = create_knowledge_engine(settings)
        factory = create_knowledge_session_factory(engine)
        request.app.state.workflow_engine = engine
        request.app.state.workflow_session_factory = factory
    return factory


def get_demo_control_service(request: Request) -> DemoControlService:
    service = getattr(request.app.state, "demo_control_service", None)
    if service is None:
        service = DemoControlService(
            get_workflow_session_factory(request),
            load_public_demo_settings(),
        )
        request.app.state.demo_control_service = service
    return cast(DemoControlService, service)


def require_demo_mutation_window(request: Request) -> Generator[None, None, None]:
    """Hold a shared DB lock for the complete public-demo mutation request."""

    settings = load_public_demo_settings()
    if not settings.enabled:
        yield
        return
    factory = get_workflow_session_factory(request)
    with factory() as session:
        from sqlalchemy import text

        session.execute(
            text("SELECT pg_advisory_xact_lock_shared(:lock_id)"),
            {"lock_id": MUTATION_LOCK_ID},
        )
        state = session.execute(
            text("SELECT maintenance_mode FROM demo_runtime_state WHERE singleton_id = 1")
        ).scalar_one_or_none()
        if state is None or state:
            from app.errors import DemoMaintenanceError

            raise DemoMaintenanceError
        yield
        session.rollback()


def get_knowledge_applicability_context(
    context: Annotated[AuthenticatedEmployeeContext, Depends(get_authenticated_employee)],
    repository: Annotated[DemoRepository, Depends(get_demo_repository)],
) -> KnowledgeApplicabilityContext:
    return resolve_knowledge_applicability(context, repository)


def get_employee_service(request: Request) -> EmployeeService:
    return cast(EmployeeService, request.app.state.employee_service)


def get_it_service(request: Request) -> ITService:
    service = cast(ITService | None, request.app.state.it_service)
    if service is not None:
        return service
    settings = getattr(request.app.state, "workflow_settings", None) or load_knowledge_settings()
    factory = getattr(request.app.state, "workflow_session_factory", None)
    if factory is None:
        engine = create_knowledge_engine(settings)
        factory = create_knowledge_session_factory(engine)
        request.app.state.workflow_engine = engine
        request.app.state.workflow_session_factory = factory
    service = ITService(session_factory=factory)
    request.app.state.it_service = service
    return service


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


def get_it_action_revision_service(request: Request) -> ITActionRevisionService:
    service = getattr(request.app.state, "it_action_revision_service", None)
    if service is not None:
        return cast(ITActionRevisionService, service)
    settings = getattr(request.app.state, "workflow_settings", None) or load_knowledge_settings()
    factory = getattr(request.app.state, "workflow_session_factory", None)
    if factory is None:
        engine = create_knowledge_engine(settings)
        factory = create_knowledge_session_factory(engine)
        request.app.state.workflow_engine = engine
        request.app.state.workflow_session_factory = factory
    service = ITActionRevisionService(factory, settings)
    request.app.state.it_action_revision_service = service
    return service


def get_portal_read_service(request: Request) -> PortalReadService:
    """Build owner-scoped V5 projections over the existing authoritative tables."""

    service = cast(
        PortalReadService | None,
        getattr(request.app.state, "portal_read_service", None),
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
    service = PortalReadService(factory, get_demo_repository(request))
    request.app.state.portal_read_service = service
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
        provider = GeminiAgentClient(settings, load_agent_settings())
        demo_settings = load_public_demo_settings()
        if demo_settings.enabled:
            provider = MeteredAgentClient(
                provider,
                get_demo_control_service(request),
                demo_settings.assistant_deadline_seconds,
            )
        service = AgentService(
            provider=provider,
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
    preparation = M3ExecutablePreparationService() if load_public_demo_settings().enabled else None
    service = ActionCreationService(factory, settings, preparation=preparation)
    request.app.state.action_creation_service = service
    return service


def get_it_action_creation_service(request: Request) -> ITActionCreationService:
    service = getattr(request.app.state, "it_action_creation_service", None)
    if service is not None:
        return cast(ITActionCreationService, service)
    settings = getattr(request.app.state, "workflow_settings", None) or load_knowledge_settings()
    factory = getattr(request.app.state, "workflow_session_factory", None)
    if factory is None:
        engine = create_knowledge_engine(settings)
        factory = create_knowledge_session_factory(engine)
        request.app.state.workflow_engine = engine
        request.app.state.workflow_session_factory = factory
    service = ITActionCreationService(factory, settings)
    request.app.state.it_action_creation_service = service
    return service


def get_assistant_application_service(request: Request) -> AssistantApplicationService:
    existing = getattr(request.app.state, "assistant_application_service", None)
    if existing is not None:
        return cast(AssistantApplicationService, existing)
    action_creation = get_action_creation_service(request)
    it_action_creation = get_it_action_creation_service(request)
    demo_settings = load_public_demo_settings()
    if demo_settings.enabled:
        control = get_demo_control_service(request)
        action_creation = QuotaActionCreationService(action_creation, control)
        it_action_creation = QuotaActionCreationService(it_action_creation, control)
    base = AssistantApplicationService(
        get_agent_service(request),
        action_creation,
        it_action_creation,
    )
    service = (
        PublicDemoAssistantApplicationService(
            base,
            get_demo_control_service(request),
            demo_settings.assistant_deadline_seconds,
        )
        if demo_settings.enabled
        else base
    )
    request.app.state.assistant_application_service = service
    return service

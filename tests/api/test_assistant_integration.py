from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.agent.dispatcher import ToolDispatcher
from app.agent.loop_models import AgentModelTurn, AgentRequestedToolCall
from app.agent.models import ToolResultStatus
from app.agent.service import AgentService
from app.api.application import create_app
from app.api.knowledge_models import KnowledgeCitation, KnowledgeQueryResponse
from app.knowledge.query_service import KnowledgeQueryService
from app.repositories.demo import DemoRepository
from app.services.employee import EmployeeService
from app.services.it import ITService

PRIMARY_SESSION = {"X-Demo-Session": "demo-v1-7f4c2a91"}


class FakeSession:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.responses = []

    def next(self, tool_responses=()):
        self.responses.append(tool_responses)
        return next(self.turns)


class FakeProvider:
    def __init__(self, session: FakeSession):
        self.session = session

    def start(self, _message):
        return self.session


def _call(name: str, arguments: dict[str, object], call_id: str):
    return AgentRequestedToolCall(
        name=name,
        arguments=arguments,
        provider_call_id=call_id,
    )


def _client(turns):
    repository = DemoRepository()
    knowledge_service = Mock(spec=KnowledgeQueryService)
    knowledge_service.query.return_value = KnowledgeQueryResponse(
        status="answered",
        answer="Eligible employees receive twenty days.",
        citations=(
            KnowledgeCitation(
                doc_code="POL-HR-001",
                title="Annual Leave Policy",
                version="2.0",
                section_anchor="entitlement",
            ),
        ),
    )
    session = FakeSession(turns)
    service = AgentService(
        provider=FakeProvider(session),
        dispatcher=ToolDispatcher(
            employee_service=EmployeeService(repository),
            it_service=ITService(repository),
            knowledge_service=knowledge_service,
            demo_repository=repository,
        ),
    )
    return (
        TestClient(
            create_app(
                repository=repository,
                agent_service=service,
            ),
            raise_server_exceptions=False,
        ),
        session,
        knowledge_service,
    )


def test_real_profile_dispatch_through_fastapi() -> None:
    client, _session, _knowledge = _client(
        [
            AgentModelTurn(requested_calls=(_call("get_my_profile", {}, "call-1"),)),
            AgentModelTurn(final_text="Your work email is alex.morgan@example.test."),
        ]
    )

    with client:
        response = client.post(
            "/api/v1/assistant/query",
            headers=PRIMARY_SESSION,
            json={"message": "What is my work email?"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["answer"] == "Your work email is alex.morgan@example.test."
    assert response.json()["citations"] == []


def test_real_ticket_dispatch_through_fastapi() -> None:
    client, _session, _knowledge = _client(
        [
            AgentModelTurn(
                requested_calls=(_call("get_my_ticket", {"ticket_id": "TKT-1001"}, "call-1"),)
            ),
            AgentModelTurn(final_text="Your ticket is open."),
        ]
    )

    with client:
        response = client.post(
            "/api/v1/assistant/query",
            headers=PRIMARY_SESSION,
            json={"message": "Check ticket TKT-1001."},
        )

    assert response.status_code == 200
    assert response.json()["answer"] == "Your ticket is open."


def test_real_multi_tool_dispatch_preserves_trusted_knowledge_citation() -> None:
    client, _session, knowledge_service = _client(
        [
            AgentModelTurn(
                requested_calls=(
                    _call(
                        "knowledge_query",
                        {"question": "What is our annual leave policy?"},
                        "call-1",
                    ),
                    _call("get_my_leave_balances", {}, "call-2"),
                )
            ),
            AgentModelTurn(
                final_text="The policy provides twenty days and you currently have 76 hours."
            ),
        ]
    )

    with client:
        response = client.post(
            "/api/v1/assistant/query",
            headers=PRIMARY_SESSION,
            json={
                "message": ("What is our annual leave policy and how much annual leave do I have?")
            },
        )

    assert response.status_code == 200
    assert response.json()["citations"] == [
        {
            "doc_code": "POL-HR-001",
            "title": "Annual Leave Policy",
            "version": "2.0",
            "section_anchor": "entitlement",
            "page": None,
        }
    ]
    knowledge_service.query.assert_called_once()


def test_cross_user_ticket_failure_stays_non_revealing_through_fastapi() -> None:
    client, session, _knowledge = _client(
        [
            AgentModelTurn(
                requested_calls=(_call("get_my_ticket", {"ticket_id": "TKT-2001"}, "call-1"),)
            ),
            AgentModelTurn(final_text="I couldn't access that ticket."),
        ]
    )

    with client:
        response = client.post(
            "/api/v1/assistant/query",
            headers=PRIMARY_SESSION,
            json={"message": "Check ticket TKT-2001."},
        )

    assert response.status_code == 200
    assert response.json()["answer"] == "I couldn't access that ticket."
    tool_result = session.responses[1][0].result
    assert tool_result.status is ToolResultStatus.NOT_FOUND_OR_INACCESSIBLE
    assert "EMP-1002" not in tool_result.model_dump_json()
    assert "owner" not in tool_result.model_dump_json().lower()

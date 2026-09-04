from datetime import date
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.agent.dispatcher import ToolDispatcher
from app.agent.loop_models import AgentModelTurn, AgentRequestedToolCall
from app.agent.models import ToolResultStatus
from app.agent.service import AgentService
from app.api.application import create_app
from app.api.assistant_application import NoOpActionCreationService
from app.api.knowledge_models import KnowledgeCitation, KnowledgeQueryResponse
from app.identity import AuthenticatedEmployeeContext
from app.knowledge.query_service import KnowledgeQueryService
from app.repositories.demo import DemoRepository
from app.services.employee import EmployeeService
from app.services.it import ITService
from app.services.leave_preparation import LeavePreparationService

PRIMARY_SESSION = {"X-Demo-Session": "demo-v1-7f4c2a91"}


def _app_with_noop_actions(**kwargs):
    app = create_app(**kwargs)
    app.state.action_creation_service = NoOpActionCreationService()
    return app


class FixedClock:
    def today(self) -> date:
        return date(2026, 8, 26)


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

    def start(self, _message, _trusted_today):
        return self.session


class SequencedProvider:
    def __init__(self, sessions):
        self._sessions = iter(sessions)
        self.messages = []

    def start(self, message, _trusted_today):
        self.messages.append(message)
        return next(self._sessions)


def _call(name: str, arguments: dict[str, object], call_id: str):
    return AgentRequestedToolCall(
        name=name,
        arguments=arguments,
        provider_call_id=call_id,
    )


def _client(turns):
    repository = DemoRepository()
    employee_service = EmployeeService(repository)
    clock = FixedClock()
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
            employee_service=employee_service,
            it_service=ITService(repository),
            knowledge_service=knowledge_service,
            demo_repository=repository,
            leave_preparation_service=LeavePreparationService(employee_service),
        ),
        clock=clock,
    )
    return (
        TestClient(
            _app_with_noop_actions(repository=repository, agent_service=service),
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


def test_policy_balance_and_prepare_flow_returns_deterministic_public_draft() -> None:
    client, _session, _knowledge = _client(
        [
            AgentModelTurn(
                requested_calls=(
                    _call(
                        "knowledge_query",
                        {"question": "What is our annual leave policy?"},
                        "call-1",
                    ),
                    _call("get_my_leave_balances", {}, "call-2"),
                    _call(
                        "prepare_leave_request",
                        {
                            "leave_type": "annual",
                            "start_date": "2026-08-28",
                            "end_date": "2026-08-28",
                        },
                        "call-3",
                    ),
                )
            ),
            AgentModelTurn(
                final_text="I prepared 80 hours of annual leave according to the policy."
            ),
        ]
    )

    with client:
        response = client.post(
            "/api/v1/assistant/query",
            headers=PRIMARY_SESSION,
            json={
                "message": (
                    "Based on our annual leave policy and my current balance, "
                    "prepare annual leave for next Friday."
                )
            },
        )

    assert response.status_code == 200
    assert response.json()["citations"][0]["doc_code"] == "POL-HR-001"
    assert response.json()["prepared_action"] == {
        "type": "leave_request",
        "leave_type": "annual",
        "start_date": "2026-08-28",
        "end_date": "2026-08-28",
        "scheduled_work_days": 1,
        "requested_hours": 7.6,
        "current_balance_hours": 76.0,
        "projected_balance_hours": 68.4,
        "preparation_status": "ready",
        "reason": None,
        "public_holiday_check_required": True,
        "non_executing": True,
        "authority": "preview",
    }
    assert response.json()["answer"].startswith("I prepared 80 hours")


def test_separate_yes_request_cannot_recover_or_execute_previous_draft() -> None:
    repository = DemoRepository()
    employee_service = EmployeeService(repository)
    clock = FixedClock()
    first_session = FakeSession(
        turns=[
            AgentModelTurn(
                requested_calls=(
                    _call(
                        "prepare_leave_request",
                        {
                            "leave_type": "annual",
                            "start_date": "2026-08-28",
                            "end_date": "2026-08-28",
                        },
                        "call-1",
                    ),
                )
            ),
            AgentModelTurn(final_text="I prepared a non-executing draft."),
        ]
    )
    second_session = FakeSession(
        turns=[
            AgentModelTurn(
                final_text=("I cannot submit leave or recover a prior draft from another request.")
            )
        ]
    )
    provider = SequencedProvider([first_session, second_session])
    service = AgentService(
        provider=provider,
        dispatcher=ToolDispatcher(
            employee_service=employee_service,
            it_service=ITService(repository),
            knowledge_service=Mock(spec=KnowledgeQueryService),
            demo_repository=repository,
            leave_preparation_service=LeavePreparationService(employee_service),
        ),
        clock=clock,
    )
    client = TestClient(
        _app_with_noop_actions(repository=repository, agent_service=service),
        raise_server_exceptions=False,
    )
    context = AuthenticatedEmployeeContext(employee_id="EMP-1001")
    before_balances = employee_service.get_my_leave_balances(context)

    with client:
        first = client.post(
            "/api/v1/assistant/query",
            headers=PRIMARY_SESSION,
            json={"message": "Prepare annual leave for Friday."},
        )
        second = client.post(
            "/api/v1/assistant/query",
            headers=PRIMARY_SESSION,
            json={"message": "Yes, submit it."},
        )

    assert first.json()["prepared_action"]["non_executing"] is True
    assert second.status_code == 200
    assert second.json()["prepared_action"] is None
    assert "cannot authorize or execute" in second.json()["answer"]
    assert provider.messages == ["Prepare annual leave for Friday."]
    assert employee_service.get_my_leave_balances(context) == before_balances

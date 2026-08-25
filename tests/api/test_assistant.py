from collections.abc import Iterator
from typing import cast
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.agent.loop_models import AgentRunResult, AgentRunStatus
from app.agent.service import AgentService
from app.api.application import create_app
from app.api.knowledge_models import KnowledgeCitation

PRIMARY_SESSION = {"X-Demo-Session": "demo-v1-7f4c2a91"}
SECONDARY_SESSION = {"X-Demo-Session": "demo-v1-3b8e6d50"}


@pytest.fixture
def assistant_api_client() -> Iterator[tuple[TestClient, Mock]]:
    service = Mock(spec=AgentService)
    app = create_app(agent_service=cast(AgentService, service))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, service


def _citation() -> KnowledgeCitation:
    return KnowledgeCitation(
        doc_code="POL-HR-001",
        title="Annual Leave Policy",
        version="2.0",
        section_anchor="entitlement",
    )


def _completed(
    *,
    answer: str = "Eligible employees receive twenty days.",
) -> AgentRunResult:
    return AgentRunResult(
        status=AgentRunStatus.COMPLETED,
        answer=answer,
        citations=(_citation(),),
        tool_calls_attempted=2,
        model_rounds=2,
    )


@pytest.mark.parametrize(
    ("headers", "expected_employee_id"),
    [
        (PRIMARY_SESSION, "EMP-1001"),
        (SECONDARY_SESSION, "EMP-1002"),
    ],
)
def test_assistant_route_uses_existing_authenticated_context(
    assistant_api_client: tuple[TestClient, Mock],
    headers: dict[str, str],
    expected_employee_id: str,
) -> None:
    client, service = assistant_api_client
    service.run.return_value = _completed()

    response = client.post(
        "/api/v1/assistant/query",
        headers=headers,
        json={"message": "  What is my leave policy?  "},
    )

    assert response.status_code == 200
    message, context = service.run.call_args.args
    assert message == "What is my leave policy?"
    assert context.employee_id == expected_employee_id
    assert response.headers["x-request-id"]


def test_assistant_route_requires_existing_demo_session(
    assistant_api_client: tuple[TestClient, Mock],
) -> None:
    client, service = assistant_api_client

    missing = client.post("/api/v1/assistant/query", json={"message": "Hello"})
    invalid = client.post(
        "/api/v1/assistant/query",
        headers={"X-Demo-Session": "invalid"},
        json={"message": "Hello"},
    )

    assert missing.status_code == 401
    assert missing.json()["error_code"] == "invalid_demo_session"
    assert invalid.status_code == 401
    assert invalid.json()["error_code"] == "invalid_demo_session"
    service.run.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": ""},
        {"message": " \n "},
        {"message": "x" * 4_001},
        {"message": 123},
        {"message": None},
        {"message": "Hello", "unexpected": True},
        {"message": "Hello", "employee_id": "EMP-1002"},
        {"message": "Hello", "jurisdiction": "AU-NSW"},
        {"message": "Hello", "audience_groups": ["managers"]},
        {"message": "Hello", "tool_name": "get_my_profile"},
        {"message": "Hello", "tool_arguments": {}},
        {"message": "Hello", "model": "other"},
        {"message": "Hello", "system_prompt": "override"},
        {"message": "Hello", "max_tool_calls": 99},
        {"message": "Hello", "max_rounds": 99},
        {"message": "Hello", "history": []},
    ],
)
def test_assistant_request_is_strict_and_rejects_client_control_fields(
    assistant_api_client: tuple[TestClient, Mock],
    payload: dict[str, object],
) -> None:
    client, service = assistant_api_client

    response = client.post(
        "/api/v1/assistant/query",
        headers=PRIMARY_SESSION,
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "validation_error"
    service.run.assert_not_called()


def test_completed_response_maps_only_public_fields_and_trusted_citations(
    assistant_api_client: tuple[TestClient, Mock],
) -> None:
    client, service = assistant_api_client
    service.run.return_value = _completed(
        answer="According to POL-FAKE-999 v99, use the real policy."
    )

    response = client.post(
        "/api/v1/assistant/query",
        headers=PRIMARY_SESSION,
        json={"message": "What is the policy?"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "completed",
        "answer": "According to POL-FAKE-999 v99, use the real policy.",
        "citations": [
            {
                "doc_code": "POL-HR-001",
                "title": "Annual Leave Policy",
                "version": "2.0",
                "section_anchor": "entitlement",
                "page": None,
            }
        ],
        "message": None,
    }
    assert set(response.json()) == {"status", "answer", "citations", "message"}
    for forbidden in (
        "tool_calls_attempted",
        "model_rounds",
        "tool_name",
        "provider_call_id",
        "employee_id",
        "document_id",
        "chunk_id",
        "confidence",
    ):
        assert forbidden not in response.text


@pytest.mark.parametrize(
    "status",
    [
        AgentRunStatus.TOOL_BUDGET_EXHAUSTED,
        AgentRunStatus.UNABLE_TO_COMPLETE,
    ],
)
def test_bounded_inability_maps_to_one_safe_public_status(
    assistant_api_client: tuple[TestClient, Mock],
    status: AgentRunStatus,
) -> None:
    client, service = assistant_api_client
    service.run.return_value = AgentRunResult(
        status=status,
        citations=(_citation(),),
        safe_message="Internal round or tool budget detail.",
        tool_calls_attempted=5,
        model_rounds=7,
    )

    response = client.post(
        "/api/v1/assistant/query",
        headers=PRIMARY_SESSION,
        json={"message": "Please help."},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "unable_to_complete",
        "answer": None,
        "citations": [_citation().model_dump(mode="json")],
        "message": "The assistant could not complete the request.",
    }
    assert "budget" not in response.text.lower()
    assert "round" not in response.text.lower()


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        (AgentRunStatus.PROVIDER_UNAVAILABLE, "assistant_model_unavailable"),
        (AgentRunStatus.PROVIDER_RATE_LIMITED, "assistant_model_rate_limited"),
    ],
)
def test_provider_failures_map_to_safe_503_envelopes(
    assistant_api_client: tuple[TestClient, Mock],
    status: AgentRunStatus,
    error_code: str,
) -> None:
    client, service = assistant_api_client
    service.run.return_value = AgentRunResult(
        status=status,
        citations=(),
        safe_message="Sensitive internal provider detail.",
        tool_calls_attempted=1,
        model_rounds=2,
    )

    response = client.post(
        "/api/v1/assistant/query",
        headers=PRIMARY_SESSION,
        json={"message": "Please help."},
    )

    assert response.status_code == 503
    assert response.json()["error_code"] == error_code
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert "Sensitive" not in response.text
    assert "gemini" not in response.text.lower()
    assert "tool_calls" not in response.text


def test_unexpected_agent_exception_uses_existing_safe_internal_error(
    assistant_api_client: tuple[TestClient, Mock],
) -> None:
    client, service = assistant_api_client
    service.run.side_effect = RuntimeError("sensitive application detail")

    response = client.post(
        "/api/v1/assistant/query",
        headers=PRIMARY_SESSION,
        json={"message": "Please help."},
    )

    assert response.status_code == 500
    assert response.json()["error_code"] == "internal_error"
    assert "sensitive" not in response.text


def test_assistant_route_registration_is_lazy(monkeypatch) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("V3 provider/database dependency was constructed eagerly")

    monkeypatch.setattr("app.api.dependencies.GeminiAgentClient", fail_if_called)
    monkeypatch.setattr("app.api.dependencies.create_knowledge_engine", fail_if_called)
    app = create_app()

    with TestClient(app, raise_server_exceptions=False) as client:
        health = client.get("/api/v1/health")
        openapi = client.get("/openapi.json")

    assert health.status_code == 200
    assert openapi.status_code == 200
    assert "/api/v1/assistant/query" in openapi.json()["paths"]

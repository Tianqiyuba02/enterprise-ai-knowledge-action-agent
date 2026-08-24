from typing import cast
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.api.application import create_app
from app.llm.client import GeminiStructuredClient
from app.llm.models import QuestionAnalysis

PRIMARY_SESSION = {"X-Demo-Session": "demo-v1-7f4c2a91"}


def test_unavailable_v2_database_does_not_change_v1_routes(monkeypatch) -> None:
    monkeypatch.setenv(
        "KNOWLEDGE_DATABASE_URL",
        "postgresql+psycopg://unavailable:unavailable@127.0.0.1:1/unavailable",
    )
    llm_client = Mock(spec=GeminiStructuredClient)
    llm_client.analyze.return_value = QuestionAnalysis.model_validate(
        {
            "category": "general",
            "summary": "The existing V1 chat path remains isolated.",
            "requires_action": False,
            "confidence": 0.9,
        }
    )

    app = create_app(llm_client=cast(GeminiStructuredClient, llm_client))
    with TestClient(app, raise_server_exceptions=False) as client:
        health = client.get("/api/v1/health")
        profile = client.get("/api/v1/me/profile", headers=PRIMARY_SESSION)
        balances = client.get("/api/v1/me/leave/balances", headers=PRIMARY_SESSION)
        ticket = client.get("/api/v1/me/tickets/TKT-1001", headers=PRIMARY_SESSION)
        chat = client.post("/api/v1/chat", json={"question": "Is V1 still isolated?"})

    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "service": "enterprise-ai-knowledge-action-agent",
        "milestone": "V1",
    }
    assert profile.status_code == 200
    assert profile.json()["employee_id"] == "EMP-1001"
    assert balances.status_code == 200
    assert ticket.status_code == 200
    assert ticket.json()["ticket_id"] == "TKT-1001"
    assert chat.status_code == 200
    assert chat.json() == {
        "category": "general",
        "summary": "The existing V1 chat path remains isolated.",
        "requires_action": False,
        "confidence": 0.9,
    }

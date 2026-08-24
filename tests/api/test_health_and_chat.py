import re
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.api.application import create_app
from app.llm.client import ProviderServiceError
from app.llm.models import QuestionAnalysis


def assert_error_envelope(response, expected_code: str) -> None:
    body = response.json()
    assert body["error_code"] == expected_code
    assert isinstance(body["message"], str) and body["message"]
    assert body["request_id"] == response.headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", body["request_id"])


def test_health_returns_typed_liveness_response(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "enterprise-ai-knowledge-action-agent",
        "milestone": "V1",
    }
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


def test_chat_returns_mocked_validated_v0_response(
    api_client: TestClient, mocked_llm_client: Mock
) -> None:
    mocked_llm_client.analyze.return_value = QuestionAnalysis.model_validate(
        {
            "category": "it",
            "summary": "Reset payroll portal access.",
            "requires_action": True,
            "confidence": 0.96,
        }
    )

    response = api_client.post(
        "/api/v1/chat",
        json={"question": "Please reset my payroll portal access"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "category": "it",
        "summary": "Reset payroll portal access.",
        "requires_action": True,
        "confidence": 0.96,
    }
    mocked_llm_client.analyze.assert_called_once_with("Please reset my payroll portal access")


def test_blank_chat_question_is_rejected_before_provider_call(
    api_client: TestClient, mocked_llm_client: Mock
) -> None:
    response = api_client.post("/api/v1/chat", json={"question": "  \n\t  "})

    assert response.status_code == 422
    assert_error_envelope(response, "validation_error")
    mocked_llm_client.analyze.assert_not_called()


def test_malformed_chat_request_uses_safe_validation_envelope(
    api_client: TestClient, mocked_llm_client: Mock
) -> None:
    response = api_client.post("/api/v1/chat", json={"question": 123})

    assert response.status_code == 422
    assert_error_envelope(response, "validation_error")
    mocked_llm_client.analyze.assert_not_called()


def test_chat_request_cannot_add_employee_identity(
    api_client: TestClient, mocked_llm_client: Mock
) -> None:
    response = api_client.post(
        "/api/v1/chat",
        json={"question": "Hello", "employee_id": "EMP-1002"},
    )

    assert response.status_code == 422
    assert_error_envelope(response, "validation_error")
    mocked_llm_client.analyze.assert_not_called()


def test_provider_failure_maps_to_safe_api_error(
    api_client: TestClient, mocked_llm_client: Mock
) -> None:
    mocked_llm_client.analyze.side_effect = ProviderServiceError(
        "provider-internal-sensitive-detail"
    )

    response = api_client.post("/api/v1/chat", json={"question": "Help me"})

    assert response.status_code == 503
    assert_error_envelope(response, "model_service_unavailable")
    assert "provider-internal-sensitive-detail" not in response.text


def test_unexpected_provider_failure_uses_safe_internal_error(
    api_client: TestClient, mocked_llm_client: Mock
) -> None:
    mocked_llm_client.analyze.side_effect = RuntimeError("unexpected-sensitive-detail")

    response = api_client.post("/api/v1/chat", json={"question": "Help me"})

    assert response.status_code == 500
    assert_error_envelope(response, "internal_error")
    assert "unexpected-sensitive-detail" not in response.text


def test_chat_configuration_failure_does_not_prevent_app_startup(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        assert client.get("/api/v1/health").status_code == 200
        response = client.post("/api/v1/chat", json={"question": "Help me"})

    assert response.status_code == 503
    assert_error_envelope(response, "model_not_configured")

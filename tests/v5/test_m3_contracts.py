"""Deterministic M3 public-demo contracts with no provider calls."""

import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from google.genai import types
from pydantic import SecretStr

from app.agent.provider import build_provider_function_declarations
from app.api.application import create_app
from app.config import PublicDemoSettings
from app.demo.adapters import (
    PublicDemoAssistantApplicationService,
    QuotaActionCreationService,
    _public_demo_agent_config,
    _request_deadline,
)
from app.demo.calendar import DemoHolidayCalendarService
from app.demo.service import REQUIRED_DOCUMENT_IDENTITIES
from app.errors import DemoCapacityReachedError
from app.workflow.action_creation import ActionCreationDisposition

ROOT = Path(__file__).parents[2]


def test_public_demo_normalizes_provider_schema_without_changing_sealed_registry() -> None:
    original_declarations = build_provider_function_declarations()
    original = types.GenerateContentConfig(
        temperature=0,
        tools=[types.Tool(function_declarations=list(original_declarations))],
    )

    adapted = _public_demo_agent_config(original)

    assert original.temperature == 0
    assert original_declarations[0].parameters_json_schema is not None
    assert adapted.temperature is None
    declarations = adapted.tools[0].function_declarations
    assert len(declarations) == len(original_declarations)
    no_argument_names = {"get_my_profile", "get_my_leave_balances"}
    assert {
        declaration.name for declaration in declarations if declaration.parameters is None
    } == no_argument_names
    assert all(declaration.parameters_json_schema is None for declaration in declarations)
    serialized = json.dumps(adapted.model_dump(mode="json", exclude_none=True))
    assert "employee_id" not in serialized
    assert "jurisdiction" not in serialized
    assert "audience" not in serialized


def test_public_demo_defaults_match_frozen_capacity_contract() -> None:
    settings = PublicDemoSettings(_env_file=None)
    assert settings.enabled is False
    assert settings.visitor_assistant_daily_limit == 8
    assert settings.visitor_action_daily_limit == 4
    assert settings.visitor_revision_daily_limit == 8
    assert settings.global_assistant_daily_limit == 60
    assert settings.global_execution_daily_limit == 40
    assert settings.global_provider_daily_limit == 300
    assert {
        ("POL-HR-001", "2.0"),
        ("SOP-IT-003", "1.0"),
    } == REQUIRED_DOCUMENT_IDENTITIES


def test_public_demo_requires_a_strong_internal_key_when_enabled() -> None:
    settings = PublicDemoSettings(
        enabled=True,
        internal_portal_key=SecretStr("short"),
        _env_file=None,
    )
    try:
        settings.require_internal_key()
    except Exception as exc:
        assert type(exc).__name__ == "KnowledgeConfigurationError"
    else:
        raise AssertionError("weak public-demo key was accepted")


def test_render_blueprint_exposes_only_the_next_portal() -> None:
    blueprint = (ROOT / "render.yaml").read_text()
    assert "type: web\n            name: enterprise-ai-demo-portal" in blueprint
    assert "type: pserv\n            name: enterprise-ai-demo-api" in blueprint
    assert "type: worker\n            name: enterprise-ai-demo-worker" in blueprint
    assert "type: cron\n            name: enterprise-ai-demo-reset" in blueprint
    assert "region: singapore" in blueprint
    assert 'postgresMajorVersion: "17"' in blueprint
    assert "enterprise-ai-demo-worker --poll-seconds 1" in blueprint
    assert '- key: GEMINI_TIMEOUT_SECONDS\n                value: "30"' in blueprint
    assert '- key: AGENT_TIMEOUT_SECONDS\n                value: "30"' in blueprint


def test_browser_bff_does_not_accept_identity_authority_or_execute_routes() -> None:
    route = (ROOT / "ui/app/api/portal/[...path]/route.ts").read_text()
    assert "employee_id" not in route
    assert "execute" not in route.lower()
    assert "X-Demo-Session" not in route
    assert "X-Demo-Visitor-ID" in route
    assert "originIsSameSite" in route
    assert "TextEncoder" in route


def test_browser_session_origin_and_body_checks_are_proxy_aware() -> None:
    visitor = (ROOT / "ui/lib/visitor.ts").read_text()
    session = (ROOT / "ui/app/api/session/route.ts").read_text()
    assert "x-forwarded-host" in visitor
    assert "x-forwarded-proto" in visitor
    assert 'request.headers.get("host")' in visitor
    assert "TextEncoder" in session


def test_public_backend_requires_private_bff_key_and_hides_docs(monkeypatch) -> None:
    key = "m3-test-internal-key-32-characters"
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "true")
    monkeypatch.setenv("INTERNAL_PORTAL_KEY", key)
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/v1/health").status_code == 200
        blocked = client.get(
            "/api/v1/me/profile",
            headers={"X-Demo-Session": "demo-v1-7f4c2a91"},
        )
        assert blocked.status_code == 404
        assert blocked.json()["error_code"] == "not_found"
        allowed = client.get(
            "/api/v1/me/profile",
            headers={
                "X-Internal-Portal-Key": key,
                "X-Demo-Session": "demo-v1-7f4c2a91",
            },
        )
        assert allowed.status_code == 200
        assert allowed.json()["employee_id"] == "EMP-1001"
        assert (
            client.get(
                "/openapi.json",
                headers={"X-Internal-Portal-Key": key},
            ).status_code
            == 404
        )


def test_public_backend_rejects_actual_oversized_body_before_provider(monkeypatch) -> None:
    key = "m3-test-internal-key-32-characters"
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "true")
    monkeypatch.setenv("INTERNAL_PORTAL_KEY", key)
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/assistant/query",
            content="x" * 32_769,
            headers={"X-Internal-Portal-Key": key},
        )
    assert response.status_code == 413
    assert response.json()["error_code"] == "request_too_large"
    assert "traceback" not in response.text.lower()


def test_private_backend_credentials_are_only_in_server_only_module() -> None:
    module = (ROOT / "ui/lib/server-persona.ts").read_text()
    assert 'import "server-only"' in module
    assert "demo-v1-7f4c2a91" in module
    assert "demo-v1-3b8e6d50" in module
    assert "employee_id" not in (ROOT / "ui/lib/visitor.ts").read_text()


def test_future_afl_candidate_dates_fail_closed() -> None:
    service = DemoHolidayCalendarService()

    class UnusedRepository:
        def list_holidays(self, *args, **kwargs):
            raise AssertionError("unresolved dates must not query absence as authority")

    service._repository = UnusedRepository()  # type: ignore[assignment]
    result = service.holidays_for_range(
        object(),  # type: ignore[arg-type]
        jurisdiction="AU-VIC",
        start_date=date(2027, 9, 24),
        end_date=date(2027, 9, 24),
    )
    assert result.covered is False
    assert result.reason == "future_holiday_not_finalized"


def test_calendar_outside_reviewed_horizon_fails_closed() -> None:
    service = DemoHolidayCalendarService()

    class UnusedRepository:
        def list_holidays(self, *args, **kwargs):
            raise AssertionError("unsupported horizons must not query absence as authority")

    service._repository = UnusedRepository()  # type: ignore[assignment]
    result = service.holidays_for_range(
        object(),  # type: ignore[arg-type]
        jurisdiction="AU-VIC",
        start_date=date(2029, 1, 1),
        end_date=date(2029, 1, 1),
    )
    assert result.covered is False
    assert result.reason == "outside_versioned_coverage"


def test_public_ui_has_honest_worker_and_failure_copy() -> None:
    workspace = (ROOT / "ui/components/assistant-workspace.tsx").read_text()
    annual_review = (ROOT / "ui/components/review-authorization.tsx").read_text()
    it_review = (ROOT / "ui/components/it-review-authorization.tsx").read_text()
    assert "Worker delayed" in workspace
    assert "demo_capacity_reached" in workspace
    assert "demo_maintenance" in workspace
    assert "AI provider is temporarily unavailable" in workspace
    assert "No leave request has been submitted yet" in annual_review
    assert "No IT ticket has been created yet" in it_review


def test_demo_capacity_error_is_stable_and_safe() -> None:
    assert DemoCapacityReachedError.error_code == "demo_capacity_reached"
    assert "usage limit" in DemoCapacityReachedError.public_message


def test_expired_public_assistant_request_cannot_persist_a_late_draft() -> None:
    class NeverCalledInner:
        def create_or_reuse(self, *args, **kwargs):
            raise AssertionError("expired request reached authoritative persistence")

    class NeverCalledControl:
        def consume(self, **kwargs):
            raise AssertionError("expired request consumed action capacity")

    token = _request_deadline.set(0.0)
    try:
        result = QuotaActionCreationService(
            NeverCalledInner(),
            NeverCalledControl(),  # type: ignore[arg-type]
        ).create_or_reuse()
    finally:
        _request_deadline.reset(token)
    assert result.disposition is ActionCreationDisposition.NOT_CREATED
    assert result.ineligibility_reason == "not_executable"


def test_chat_authorization_uses_no_provider_capacity() -> None:
    calls: list[tuple[str, int]] = []

    class Control:
        def consume(self, *, visitor_id, metric, amount=1):
            calls.append((metric, amount))

    class Inner:
        def query(self, message, context, *, initiation_id=None):
            return "safe"

    service = PublicDemoAssistantApplicationService(
        Inner(),  # type: ignore[arg-type]
        Control(),  # type: ignore[arg-type]
        45,
    )
    assert service.query("yes, submit it", object(), visitor_id="visitor-a") == "safe"
    assert calls == [("assistant", 1)]

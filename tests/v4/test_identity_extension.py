import inspect

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.contracts import V3_TOOL_ALLOWLIST
from app.agent.leave_models import PrepareLeaveRequestArguments
from app.api.application import create_app
from app.api.dependencies import (
    DEMO_IDENTITY_BINDINGS,
    DEMO_SESSIONS,
    get_authenticated_employee,
)
from app.api.models import ConfirmActionRequest
from app.errors import InvalidDemoSessionError
from app.identity import AuthenticatedEmployeeContext
from app.knowledge.applicability import resolve_knowledge_applicability
from app.knowledge.vocabulary import Jurisdiction
from app.repositories.demo import DemoRepository

PRIMARY_SESSION = {"X-Demo-Session": "demo-v1-7f4c2a91"}
FORBIDDEN_IDENTITY_FIELDS = {
    "employee_id",
    "subject_id",
    "session_id",
    "jurisdiction",
    "confirmation_session_id",
    "owner_subject_id",
}


def test_employee_id_only_construction_remains_valid() -> None:
    context = AuthenticatedEmployeeContext(employee_id="EMP-1001")

    assert context.employee_id == "EMP-1001"
    assert context.subject_id is None
    assert context.session_id is None
    assert context.jurisdiction is None


def test_trusted_dependency_fills_v4_identity_fields() -> None:
    primary = get_authenticated_employee(x_demo_session="demo-v1-7f4c2a91")
    secondary = get_authenticated_employee(x_demo_session="demo-v1-3b8e6d50")

    assert primary == DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"]
    assert primary.employee_id == "EMP-1001"
    assert primary.subject_id == "subj_9f2c4e81a6b047d3"
    assert primary.session_id == "sess_c4a81f07e2d94b6a"
    assert primary.jurisdiction == "AU-VIC"
    assert secondary.employee_id == "EMP-1002"
    assert secondary.subject_id == "subj_1a8e5c03d7f249b6"
    assert secondary.session_id == "sess_e50b3d6a91c8472f"
    assert secondary.jurisdiction == "AU-VIC"
    assert primary.session_id != secondary.session_id
    assert primary.subject_id != secondary.subject_id
    assert "demo-v1-7f4c2a91" not in {primary.subject_id, primary.session_id}
    assert DEMO_SESSIONS["demo-v1-7f4c2a91"] == "EMP-1001"


def test_dependency_still_rejects_missing_and_invalid_sessions() -> None:
    with pytest.raises(InvalidDemoSessionError):
        get_authenticated_employee(x_demo_session=None)
    with pytest.raises(InvalidDemoSessionError):
        get_authenticated_employee(x_demo_session="not-a-session")


def test_trusted_dependency_does_not_accept_request_body_identity() -> None:
    signature = inspect.signature(get_authenticated_employee)

    assert list(signature.parameters) == ["x_demo_session"]
    assert "employee_id" not in signature.parameters
    assert "subject_id" not in signature.parameters
    assert "session_id" not in signature.parameters
    assert "jurisdiction" not in signature.parameters


def test_model_and_tool_inputs_cannot_supply_trusted_identity_fields() -> None:
    with pytest.raises(ValidationError):
        PrepareLeaveRequestArguments.model_validate(
            {
                "leave_type": "annual",
                "start_date": "2026-09-01",
                "end_date": "2026-09-01",
                "subject_id": "subj_injected",
                "session_id": "sess_injected",
                "jurisdiction": "AU-NSW",
                "employee_id": "EMP-1002",
            }
        )

    for contract in V3_TOOL_ALLOWLIST.values():
        assert FORBIDDEN_IDENTITY_FIELDS.isdisjoint(contract.llm_arguments)

    with pytest.raises(ValidationError):
        ConfirmActionRequest.model_validate(
            {
                "challenge_id": "11111111-1111-1111-1111-111111111111",
                "confirmation_token": "token",
                "employee_id": "EMP-1002",
                "subject_id": "subj_injected",
                "session_id": "sess_injected",
                "confirmed": True,
                "execute": True,
            }
        )


def test_v1_v2_v3_identity_behavior_remains_unchanged() -> None:
    with TestClient(create_app(), raise_server_exceptions=False) as api_client:
        response = api_client.get("/api/v1/me/profile", headers=PRIMARY_SESSION)
        body = response.json()
        assert response.status_code == 200
        assert body["employee_id"] == "EMP-1001"
        assert body["full_name"] == "Alex Morgan"
        assert "subject_id" not in body
        assert "session_id" not in body
        assert "jurisdiction" not in body

    applicability = resolve_knowledge_applicability(
        AuthenticatedEmployeeContext(employee_id="EMP-1001"),
        DemoRepository(),
    )
    assert applicability.jurisdiction is Jurisdiction.AU_VIC

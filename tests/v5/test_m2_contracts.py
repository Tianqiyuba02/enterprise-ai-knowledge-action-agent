"""Deterministic M2 boundary tests that require no provider or database."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agent.contracts import V3_TOOL_ALLOWLIST, ToolCapability, V3ToolName
from app.agent.dispatcher import ToolDispatcher
from app.agent.models import GetMyTicketArguments, PreparedITSupportTicketToolData
from app.api.assistant_models import AssistantQueryRequest
from app.identity import AuthenticatedEmployeeContext
from app.it.domain import PrepareITSupportTicketArguments, ReviseITSupportTicketRequest
from app.knowledge.query_service import KnowledgeQueryService
from app.repositories.demo import DemoRepository
from app.services.employee import EmployeeService
from app.services.it import ITService
from app.services.leave_preparation import LeavePreparationService


def _dispatcher() -> ToolDispatcher:
    repository = DemoRepository()
    employees = EmployeeService(repository)
    return ToolDispatcher(
        employee_service=employees,
        it_service=ITService(repository),
        knowledge_service=Mock(spec=KnowledgeQueryService),
        demo_repository=repository,
        leave_preparation_service=LeavePreparationService(employees),
    )


def test_it_prepare_contract_has_only_four_business_fields() -> None:
    contract = V3_TOOL_ALLOWLIST[V3ToolName.PREPARE_IT_SUPPORT_TICKET]
    assert contract.capability is ToolCapability.PREPARE
    assert contract.llm_arguments == ("category", "summary", "description", "urgency")
    schema = contract.argument_model.model_json_schema()
    assert set(schema["properties"]) == {"category", "summary", "description", "urgency"}
    forbidden = {
        "employee_id",
        "owner_subject_id",
        "action_type",
        "action_id",
        "revision",
        "ticket_id",
        "status",
        "expires_at",
        "draft_hash",
        "authority_snapshot_hash",
    }
    assert forbidden.isdisjoint(schema["properties"])


def test_it_prepare_dispatch_accepts_wire_enums_and_rejects_authority_fields() -> None:
    context = AuthenticatedEmployeeContext(employee_id="EMP-1001")
    business_fields = {
        "category": "software",
        "summary": "Meeting app closes",
        "description": "The synthetic meeting app closes at startup.",
        "urgency": "medium",
    }
    prepared = _dispatcher().dispatch(
        name="prepare_it_support_ticket",
        arguments=business_fields,
        context=context,
    )
    rejected = _dispatcher().dispatch(
        name="prepare_it_support_ticket",
        arguments={**business_fields, "employee_id": "EMP-1002"},
        context=context,
    )
    assert isinstance(prepared.data, PreparedITSupportTicketToolData)
    assert prepared.data.draft.non_executing is True
    assert rejected.status.value == "invalid_arguments"


def test_ticket_id_parser_has_no_four_digit_ceiling() -> None:
    assert GetMyTicketArguments(ticket_id="TKT-1000001").ticket_id == "TKT-1000001"
    with pytest.raises(ValidationError):
        GetMyTicketArguments(ticket_id="TKT-12A")


def test_http_initiation_value_is_not_identity_or_model_authority() -> None:
    request = AssistantQueryRequest(message="Prepare IT help", initiation_id=uuid4())
    assert set(request.model_dump()) == {"message", "initiation_id"}
    with pytest.raises(ValidationError):
        AssistantQueryRequest.model_validate(
            {"message": "Prepare IT help", "employee_id": "EMP-1002"}
        )


@pytest.mark.parametrize(
    "model",
    (PrepareITSupportTicketArguments, ReviseITSupportTicketRequest),
)
def test_it_write_models_forbid_unknown_authority_fields(model) -> None:
    payload = {
        "category": "access",
        "summary": "Portal access blocked",
        "description": "The synthetic employee portal rejects sign-in.",
        "urgency": "high",
        "employee_id": "EMP-1002",
    }
    if model is ReviseITSupportTicketRequest:
        payload["expected_revision"] = 1
    with pytest.raises(ValidationError):
        model.model_validate(payload)

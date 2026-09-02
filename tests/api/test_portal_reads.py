from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import Mock
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.application import create_app
from app.api.portal_models import (
    ActionAuditEventResponse,
    ActionDetailResponse,
    ActionListItemResponse,
    ActionListResponse,
    AuthoritativeAnnualLeaveDraftResponse,
    LeaveBalanceProjectionResponse,
    LeaveSummaryResponse,
    PolicyDocumentDetailResponse,
    PolicyDocumentListResponse,
    PolicyDocumentSummaryResponse,
    PolicySectionResponse,
    StableAuthorityResponse,
)
from app.portal.service import PortalReadService
from app.workflow.confirmation import ConfirmationService
from app.workflow.domain import WorkflowState

AUTH = {"X-Demo-Session": "demo-v1-7f4c2a91"}
ACTION_ID = UUID("14c01778-e59b-487d-a666-9d6f6d7b51e1")
NOW = datetime(2026, 9, 2, 1, 30, tzinfo=UTC)


def _draft() -> AuthoritativeAnnualLeaveDraftResponse:
    return AuthoritativeAnnualLeaveDraftResponse(
        action_type="submit_annual_leave",
        leave_type="annual",
        start_date=date(2026, 10, 12),
        end_date=date(2026, 10, 16),
        requested_hours=Decimal("38.00"),
        projected_balance_hours=Decimal("38.00"),
        readiness="ready",
        reason="Family trip",
        calendar_version="vic-2026-v1",
        ruleset_version="annual-leave-v1",
        authority_snapshot_hash="a" * 64,
        scheduled_work_days=5,
        stable_authority=StableAuthorityResponse(
            employee_id="EMP-1001",
            jurisdiction="AU-VIC",
            work_days=("monday", "tuesday", "wednesday", "thursday", "friday"),
            hours_per_day=Decimal("7.60"),
            timezone="Australia/Melbourne",
            calendar_version="vic-2026-v1",
            ruleset_version="annual-leave-v1",
        ),
    )


def _detail() -> ActionDetailResponse:
    return ActionDetailResponse(
        action_id=ACTION_ID,
        revision=1,
        action_type="submit_annual_leave",
        state=WorkflowState.AWAITING_CONFIRMATION,
        authoritative_draft=_draft(),
        created_at=NOW,
        updated_at=NOW,
        action_expires_at=NOW,
        confirmed_at=None,
        confirmed_expires_at=None,
        confirmation_required=True,
        manual_review_required=False,
        result=None,
        audit_events=(
            ActionAuditEventResponse(
                event_id=UUID("c5bab1aa-8f5e-44a1-813f-17e7c6697158"),
                event_type="ACTION_PREPARED",
                actor_type="EMPLOYEE",
                from_state=None,
                to_state="AWAITING_CONFIRMATION",
                safe_metadata={"disposition": "CREATED"},
                created_at=NOW,
            ),
        ),
    )


def _client() -> tuple[TestClient, Mock, Mock]:
    portal = Mock(spec=PortalReadService)
    confirmation = Mock(spec=ConfirmationService)
    app = create_app(portal_read_service=cast(PortalReadService, portal))
    app.state.confirmation_service = confirmation
    return TestClient(app, raise_server_exceptions=False), portal, confirmation


def test_owner_scoped_leave_and_action_projections_serialize_decimal_hours() -> None:
    client, portal, _confirmation = _client()
    portal.leave_summary.return_value = LeaveSummaryResponse(
        balances=(
            LeaveBalanceProjectionResponse(
                leave_type="annual",
                base_balance_hours=Decimal("76.00"),
                committed_hours=Decimal("0.00"),
                available_hours=Decimal("76.00"),
                source_as_of_date=date(2026, 8, 24),
            ),
        ),
        requests=(),
        computed_at=NOW,
    )
    portal.list_actions.return_value = ActionListResponse(
        items=(
            ActionListItemResponse(
                action_id=ACTION_ID,
                revision=1,
                action_type="submit_annual_leave",
                state=WorkflowState.AWAITING_CONFIRMATION,
                start_date=date(2026, 10, 12),
                end_date=date(2026, 10, 16),
                requested_hours=Decimal("38.00"),
                reason="Family trip",
                created_at=NOW,
                updated_at=NOW,
                action_expires_at=NOW,
                confirmed_expires_at=None,
                confirmation_required=True,
                result=None,
            ),
        ),
        total=1,
    )

    with client:
        leave = client.get("/api/v1/me/leave/summary", headers=AUTH)
        actions = client.get("/api/v1/me/actions?limit=10", headers=AUTH)

    assert leave.status_code == 200
    assert leave.json()["balances"][0]["available_hours"] == "76.00"
    assert actions.status_code == 200
    assert actions.json()["items"][0]["requested_hours"] == "38.00"
    assert portal.leave_summary.call_args.args[0].employee_id == "EMP-1001"
    assert portal.list_actions.call_args.kwargs == {"limit": 10}


def test_action_detail_normalizes_v4_state_then_returns_exact_persisted_draft() -> None:
    client, portal, confirmation = _client()
    portal.action_detail.return_value = _detail()

    with client:
        response = client.get(f"/api/v1/actions/{ACTION_ID}/detail", headers=AUTH)

    assert response.status_code == 200
    payload = response.json()
    assert payload["authoritative_draft"]["authority_snapshot_hash"] == "a" * 64
    assert payload["authoritative_draft"]["requested_hours"] == "38.00"
    assert payload["audit_events"][0]["safe_metadata"] == {"disposition": "CREATED"}
    confirmation.get_action.assert_called_once()
    portal.action_detail.assert_called_once()


def test_policy_library_only_uses_server_resolved_applicability() -> None:
    client, portal, _confirmation = _client()
    summary = PolicyDocumentSummaryResponse(
        doc_code="HR-LEAVE-001",
        version="2026.1",
        title="Annual Leave Policy",
        status="approved",
        effective_date=date(2026, 1, 1),
        expiry_date=None,
        jurisdiction="AU-VIC",
        audience_groups=("all_employees",),
        source_uri="corpus://annual-leave-policy",
        section_count=1,
    )
    portal.list_policy_documents.return_value = PolicyDocumentListResponse(
        items=(summary,), total=1
    )
    portal.policy_document.return_value = PolicyDocumentDetailResponse(
        **summary.model_dump(),
        sections=(
            PolicySectionResponse(
                section_label="Entitlement",
                anchor="entitlement",
                page=2,
                content="Employees receive annual leave.",
            ),
        ),
    )

    with client:
        listing = client.get("/api/v1/knowledge/documents", headers=AUTH)
        detail = client.get(
            "/api/v1/knowledge/documents/HR-LEAVE-001/versions/2026.1",
            headers=AUTH,
        )

    assert listing.status_code == 200
    assert listing.json()["items"][0]["status"] == "approved"
    assert detail.status_code == 200
    assert detail.json()["sections"][0]["anchor"] == "entitlement"
    applicability = portal.list_policy_documents.call_args.args[0]
    assert applicability.jurisdiction.value == "AU-VIC"
    assert {group.value for group in applicability.audience_groups} == {
        "all_employees",
        "melbourne_employees",
    }


def test_new_portal_reads_require_trusted_demo_session() -> None:
    client, portal, _confirmation = _client()

    with client:
        responses = (
            client.get("/api/v1/me/leave/summary"),
            client.get("/api/v1/me/actions"),
            client.get(f"/api/v1/actions/{ACTION_ID}/detail"),
            client.get("/api/v1/knowledge/documents"),
        )

    assert all(response.status_code == 401 for response in responses)
    assert portal.mock_calls == []

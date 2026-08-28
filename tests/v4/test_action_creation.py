from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.agent.leave_models import LeavePreparationStatus, LeaveRequestDraft
from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.errors import ActionCreationIdentityError
from app.identity import AuthenticatedEmployeeContext
from app.workflow.action_creation import ActionCreationService, require_v4_execution_identity
from app.workflow.canonical import business_request_key

ROOT = Path(__file__).resolve().parents[2]
ALEX = DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"]


def _draft(**overrides: object) -> LeaveRequestDraft:
    payload = {
        "leave_type": "annual",
        "start_date": date(2026, 10, 21),
        "end_date": date(2026, 10, 21),
        "scheduled_work_days": 1,
        "requested_hours": Decimal("7.60"),
        "current_balance_hours": Decimal("76.00"),
        "projected_balance_hours": Decimal("68.40"),
        "preparation_status": LeavePreparationStatus.READY,
        "reason": "Family visit",
        "public_holiday_check_required": True,
        "non_executing": True,
    }
    payload.update(overrides)
    return LeaveRequestDraft.model_validate(payload)


def test_missing_v4_identity_fields_fail_closed() -> None:
    with pytest.raises(ActionCreationIdentityError):
        require_v4_execution_identity(
            AuthenticatedEmployeeContext(
                employee_id="EMP-1001", session_id="s", jurisdiction="AU-VIC"
            )
        )
    with pytest.raises(ActionCreationIdentityError):
        require_v4_execution_identity(
            AuthenticatedEmployeeContext(
                employee_id="EMP-1001", subject_id="subj", jurisdiction="AU-VIC"
            )
        )
    with pytest.raises(ActionCreationIdentityError):
        require_v4_execution_identity(
            AuthenticatedEmployeeContext(employee_id="EMP-1001", subject_id="subj", session_id="s")
        )


def test_create_or_reuse_requires_identity_before_persistence() -> None:
    service = ActionCreationService(session_factory=None)  # type: ignore[arg-type]
    incomplete = AuthenticatedEmployeeContext(employee_id=ALEX.employee_id)
    with pytest.raises(ActionCreationIdentityError):
        service.create_or_reuse(incomplete, _draft())


def test_business_request_key_is_the_frozen_contract() -> None:
    key = business_request_key(
        employee_id=ALEX.employee_id,
        leave_type="annual",
        start_date=date(2026, 10, 21),
        end_date=date(2026, 10, 21),
    )
    assert key == business_request_key(
        employee_id=ALEX.employee_id,
        leave_type="annual",
        start_date=date(2026, 10, 21),
        end_date=date(2026, 10, 21),
    )
    assert key != business_request_key(
        employee_id="EMP-1002",
        leave_type="annual",
        start_date=date(2026, 10, 21),
        end_date=date(2026, 10, 21),
    )


def test_action_creation_source_has_no_provider_or_confirmation_side_effects() -> None:
    source = (ROOT / "src" / "app" / "workflow" / "action_creation.py").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "gemini" not in lowered
    assert "google.genai" not in lowered
    assert "confirmation_token" not in source
    assert "issue_challenge" not in source
    assert "LeaveSubmissionExecutor" not in source
    assert "execution_key" not in source

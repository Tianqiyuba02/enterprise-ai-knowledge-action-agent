from datetime import date
from decimal import Decimal

import pytest

from app.workflow.authority import AuthoritySnapshot, CanonicalDraft
from app.workflow.canonical import (
    CANONICALIZATION_VERSION,
    business_request_key,
    canonicalize,
    draft_hash,
    sha256_digest,
)
from app.workflow.domain import ActionType, LeaveType


def _snapshot() -> AuthoritySnapshot:
    return AuthoritySnapshot(
        employee_id="EMP-1001",
        jurisdiction="AU-VIC",
        work_days=("monday", "tuesday", "wednesday", "thursday", "friday"),
        hours_per_day=Decimal("7.60"),
        timezone="Australia/Melbourne",
        trusted_base_balance_hours=Decimal("76.00"),
        committed_submitted_hours=Decimal("7.60"),
        effective_available_hours=Decimal("68.40"),
        calendar_version="AU-VIC-2026-v1",
        ruleset_version="v4-annual-leave-1",
    )


def _draft(**overrides: object) -> CanonicalDraft:
    snapshot = _snapshot()
    values: dict[str, object] = {
        "action_type": ActionType.SUBMIT_ANNUAL_LEAVE.value,
        "leave_type": LeaveType.ANNUAL.value,
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 1),
        "requested_hours": Decimal("7.6"),
        "projected_balance_hours": Decimal("68.4"),
        "readiness": "ready",
        "reason": "Family visit",
        "calendar_version": "AU-VIC-2026-v1",
        "ruleset_version": "v4-annual-leave-1",
        "authority_snapshot_hash": snapshot.fingerprint(),
    }
    values.update(overrides)
    return CanonicalDraft(**values)  # type: ignore[arg-type]


def test_canonical_json_is_sorted_utf8_and_rejects_floats() -> None:
    payload = {"b": Decimal("7.60"), "a": date(2026, 1, 2), "reason": "Café"}
    encoded = canonicalize(payload)

    assert encoded == b'{"a":"2026-01-02","b":"7.60","reason":"Caf\xc3\xa9"}'
    assert CANONICALIZATION_VERSION == "v4-canonical-1"
    with pytest.raises(TypeError, match="float"):
        canonicalize({"hours": 7.6})


def test_draft_hash_includes_reason_and_excludes_assistant_prose() -> None:
    with_reason = _draft(reason="Family visit")
    without_reason = _draft(reason=None)
    changed_hours = _draft(requested_hours=Decimal("15.20"))

    assert with_reason.fingerprint() != without_reason.fingerprint()
    assert with_reason.fingerprint() != changed_hours.fingerprint()
    assert with_reason.fingerprint() == draft_hash(
        {
            "action_type": with_reason.action_type,
            "leave_type": with_reason.leave_type,
            "start_date": with_reason.start_date,
            "end_date": with_reason.end_date,
            "requested_hours": with_reason.requested_hours,
            "projected_balance_hours": with_reason.projected_balance_hours,
            "readiness": with_reason.readiness,
            "reason": with_reason.reason,
            "calendar_version": with_reason.calendar_version,
            "ruleset_version": with_reason.ruleset_version,
            "authority_snapshot_hash": with_reason.authority_snapshot_hash,
        }
    )
    with pytest.raises(ValueError, match="assistant prose"):
        draft_hash(
            {
                "action_type": with_reason.action_type,
                "leave_type": with_reason.leave_type,
                "start_date": with_reason.start_date,
                "end_date": with_reason.end_date,
                "requested_hours": with_reason.requested_hours,
                "projected_balance_hours": with_reason.projected_balance_hours,
                "readiness": with_reason.readiness,
                "reason": with_reason.reason,
                "calendar_version": with_reason.calendar_version,
                "ruleset_version": with_reason.ruleset_version,
                "authority_snapshot_hash": with_reason.authority_snapshot_hash,
                "assistant_prose": "Sure, I submitted it.",
            }
        )


def test_business_request_key_uses_only_current_leave_identity() -> None:
    first = business_request_key(
        employee_id="EMP-1001",
        leave_type="annual",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
    )
    second = business_request_key(
        employee_id="EMP-1001",
        leave_type="annual",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
    )
    different_employee = business_request_key(
        employee_id="EMP-1002",
        leave_type="annual",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
    )

    assert first == second
    assert first != different_employee
    assert first == sha256_digest(
        {
            "canonicalization_version": CANONICALIZATION_VERSION,
            "kind": "business_request_key",
            "employee_id": "EMP-1001",
            "leave_type": "annual",
            "start_date": date(2026, 9, 1),
            "end_date": date(2026, 9, 2),
        }
    )


def test_authority_snapshot_is_trusted_and_model_cannot_supply_it() -> None:
    snapshot = _snapshot()
    other = AuthoritySnapshot(
        employee_id="EMP-1001",
        jurisdiction="AU-VIC",
        work_days=snapshot.work_days,
        hours_per_day=Decimal("7.60"),
        timezone="Australia/Melbourne",
        trusted_base_balance_hours=Decimal("76.00"),
        committed_submitted_hours=Decimal("0.00"),
        effective_available_hours=Decimal("76.00"),
        calendar_version="AU-VIC-2026-v1",
        ruleset_version="v4-annual-leave-1",
    )

    assert snapshot.fingerprint() != other.fingerprint()
    assert "assistant" not in snapshot.fingerprint()
    with pytest.raises(TypeError):
        AuthoritySnapshot(
            employee_id="EMP-1001",
            jurisdiction="AU-VIC",
            work_days=snapshot.work_days,
            hours_per_day=7.6,  # type: ignore[arg-type]
            timezone="Australia/Melbourne",
            trusted_base_balance_hours=Decimal("76.00"),
            committed_submitted_hours=Decimal("0.00"),
            effective_available_hours=Decimal("76.00"),
            calendar_version="AU-VIC-2026-v1",
            ruleset_version="v4-annual-leave-1",
        )

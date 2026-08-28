from datetime import date
from decimal import Decimal

import pytest

from app.workflow.authority import AuthoritySnapshot, CanonicalDraft
from app.workflow.calendar import V4_CALENDAR_VERSION
from app.workflow.domain import ActionType, LeaveType
from app.workflow.errors import WorkflowIntegrityError
from app.workflow.executable_preparation import (
    READINESS_READY,
    V4ExecutablePreparationService,
    holiday_adjusted_scheduled_work_days,
    reconstruct_canonical_draft,
    serialize_canonical_draft,
    verify_persisted_draft_integrity,
)


class _Revision:
    def __init__(self, payload: dict, draft_hash: str, authority_snapshot_hash: str) -> None:
        self.draft_payload = payload
        self.draft_hash = draft_hash
        self.authority_snapshot_hash = authority_snapshot_hash


def _draft() -> CanonicalDraft:
    snapshot = AuthoritySnapshot(
        employee_id="EMP-1001",
        jurisdiction="AU-VIC",
        work_days=("monday", "tuesday", "wednesday", "thursday", "friday"),
        hours_per_day=Decimal("7.60"),
        timezone="Australia/Melbourne",
        trusted_base_balance_hours=Decimal("76.00"),
        committed_submitted_hours=Decimal("0.00"),
        effective_available_hours=Decimal("76.00"),
        calendar_version=V4_CALENDAR_VERSION,
        ruleset_version="v4-annual-leave-1",
    )
    return CanonicalDraft(
        action_type=ActionType.SUBMIT_ANNUAL_LEAVE.value,
        leave_type=LeaveType.ANNUAL.value,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
        requested_hours=Decimal("7.60"),
        projected_balance_hours=Decimal("68.40"),
        readiness=READINESS_READY,
        reason="Family visit",
        calendar_version=V4_CALENDAR_VERSION,
        ruleset_version="v4-annual-leave-1",
        authority_snapshot_hash=snapshot.fingerprint(),
    )


def test_melbourne_cup_is_removed_from_scheduled_workdays() -> None:
    days = holiday_adjusted_scheduled_work_days(
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 6),
        work_days=("monday", "tuesday", "wednesday", "thursday", "friday"),
        holiday_dates={date(2026, 11, 3)},
    )
    assert days == 4


def test_canonical_payload_round_trip_preserves_draft_hash() -> None:
    draft = _draft()
    payload = serialize_canonical_draft(draft, scheduled_work_days=1)
    reconstructed = reconstruct_canonical_draft(payload)
    assert reconstructed.fingerprint() == draft.fingerprint()
    assert payload["scheduled_work_days"] == 1


def test_partial_stored_payload_is_integrity_error_not_stale() -> None:
    draft = _draft()
    revision = _Revision(
        {"leave_type": "annual", "reason": "Family visit"},
        draft.fingerprint(),
        draft.authority_snapshot_hash,
    )
    with pytest.raises(WorkflowIntegrityError, match="missing canonical fields"):
        verify_persisted_draft_integrity(revision)


def test_calendar_version_drift_is_stale_without_mixing_authorities() -> None:
    draft = _draft()
    payload = serialize_canonical_draft(draft, scheduled_work_days=1)
    payload["calendar_version"] = "AU-VIC-2025-v1"
    drifted = reconstruct_canonical_draft(payload)

    class _Workflow:
        owner_employee_id = "EMP-1001"
        owner_subject_id = "sub"
        jurisdiction = "AU-VIC"

    class _DriftedRevision:
        draft_payload = serialize_canonical_draft(drifted, scheduled_work_days=1)
        draft_hash = drifted.fingerprint()
        authority_snapshot_hash = drifted.authority_snapshot_hash
        calendar_version = "AU-VIC-2025-v1"
        ruleset_version = drifted.ruleset_version
        business_request_key = "unused"

    result = V4ExecutablePreparationService().revalidate_confirmed(
        None,  # type: ignore[arg-type]
        _Workflow(),
        _DriftedRevision(),
    )
    assert result.stale is True
    assert result.preparation is None
    assert result.persisted_draft.calendar_version == "AU-VIC-2025-v1"


def test_hash_payload_mismatch_is_integrity_error() -> None:
    draft = _draft()
    payload = serialize_canonical_draft(draft, scheduled_work_days=1)
    payload["requested_hours"] = "15.20"
    revision = _Revision(payload, draft.fingerprint(), draft.authority_snapshot_hash)
    with pytest.raises(WorkflowIntegrityError, match="does not match draft_hash"):
        verify_persisted_draft_integrity(revision)

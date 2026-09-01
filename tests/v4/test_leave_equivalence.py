from datetime import date
from decimal import Decimal
from uuid import UUID

from app.workflow.domain import LeaveRequestStatus, LeaveType
from app.workflow.leave_equivalence import leaves_trusted_equivalent

ACTION_ID = UUID("11111111-1111-1111-1111-111111111111")


def _leave(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "employee_id": "EMP-1001",
        "source_action_id": ACTION_ID,
        "source_action_revision": 1,
        "leave_type": LeaveType.ANNUAL.value,
        "start_date": date(2026, 10, 21),
        "end_date": date(2026, 10, 21),
        "requested_hours": Decimal("7.60"),
        "business_request_key": "key-1",
        "reason": "Family visit",
        "calendar_version": "AU-VIC-2026-v1",
        "ruleset_version": "v4-annual-leave-1",
        "status": LeaveRequestStatus.SUBMITTED.value,
    }
    row.update(overrides)
    return row


def _equivalent(leave: dict[str, object]) -> bool:
    return leaves_trusted_equivalent(
        employee_id="EMP-1001",
        action_id=ACTION_ID,
        revision=1,
        leave_type=LeaveType.ANNUAL.value,
        start_date=date(2026, 10, 21),
        end_date=date(2026, 10, 21),
        requested_hours=Decimal("7.60"),
        business_request_key="key-1",
        reason="Family visit",
        calendar_version="AU-VIC-2026-v1",
        ruleset_version="v4-annual-leave-1",
        leave=leave,
    )


def test_exact_equivalence_requires_reason_and_mutation_metadata() -> None:
    assert _equivalent(_leave()) is True
    assert _equivalent(_leave(reason="tampered")) is False
    assert _equivalent(_leave(reason=None)) is False
    assert _equivalent(_leave(calendar_version="AU-VIC-CHANGED")) is False
    assert _equivalent(_leave(ruleset_version="changed")) is False
    assert _equivalent(_leave(source_action_revision=2)) is False
    assert _equivalent(_leave(employee_id="EMP-1002")) is False
    assert _equivalent(_leave(requested_hours=Decimal("8.00"))) is False


def test_equivalence_ignores_conversational_fields() -> None:
    assert _equivalent(_leave(assistant_prose="please approve", submitted_at="ignored")) is True

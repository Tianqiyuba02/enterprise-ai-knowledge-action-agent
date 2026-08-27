from datetime import date
from decimal import Decimal

import pytest

from app.workflow.balance import date_ranges_overlap, effective_available_hours


def test_effective_available_hours_subtracts_committed_submitted_hours() -> None:
    available = effective_available_hours(
        trusted_base_balance_hours=Decimal("76.00"),
        committed_submitted_hours=Decimal("15.20"),
    )

    assert available == Decimal("60.80")
    with pytest.raises(TypeError):
        effective_available_hours(
            trusted_base_balance_hours=76.0,  # type: ignore[arg-type]
            committed_submitted_hours=Decimal("0"),
        )


def test_overlap_detection_for_active_submitted_annual_leave() -> None:
    assert date_ranges_overlap(
        date(2026, 9, 1),
        date(2026, 9, 3),
        date(2026, 9, 3),
        date(2026, 9, 5),
    )
    assert date_ranges_overlap(
        date(2026, 9, 1),
        date(2026, 9, 5),
        date(2026, 9, 2),
        date(2026, 9, 2),
    )
    assert not date_ranges_overlap(
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
        date(2026, 9, 4),
    )

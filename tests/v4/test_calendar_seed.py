from datetime import date

from app.workflow.calendar import (
    V4_CALENDAR_COVERAGE_END,
    V4_CALENDAR_COVERAGE_START,
    V4_CALENDAR_JURISDICTION,
    V4_CALENDAR_VERSION,
    VIC_2026_STATEWIDE_HOLIDAYS,
    is_date_inside_trusted_coverage,
    is_range_inside_trusted_coverage,
    statewide_holiday_name,
)

EXPECTED_HOLIDAYS = (
    (date(2026, 1, 1), "New Year's Day"),
    (date(2026, 1, 26), "Australia Day"),
    (date(2026, 3, 9), "Labour Day"),
    (date(2026, 4, 3), "Good Friday"),
    (date(2026, 4, 4), "Saturday before Easter Sunday"),
    (date(2026, 4, 5), "Easter Sunday"),
    (date(2026, 4, 6), "Easter Monday"),
    (date(2026, 4, 25), "ANZAC Day"),
    (date(2026, 6, 8), "King's Birthday"),
    (date(2026, 9, 25), "Friday before the AFL Grand Final"),
    (date(2026, 11, 3), "Melbourne Cup"),
    (date(2026, 12, 25), "Christmas Day"),
    (date(2026, 12, 26), "Boxing Day"),
    (date(2026, 12, 28), "Additional public holiday for Boxing Day"),
)


def test_trusted_calendar_manifest_and_coverage() -> None:
    assert V4_CALENDAR_JURISDICTION == "AU-VIC"
    assert V4_CALENDAR_VERSION == "AU-VIC-2026-v1"
    assert date(2026, 1, 1) == V4_CALENDAR_COVERAGE_START
    assert date(2026, 12, 31) == V4_CALENDAR_COVERAGE_END
    assert VIC_2026_STATEWIDE_HOLIDAYS == EXPECTED_HOLIDAYS
    assert len(VIC_2026_STATEWIDE_HOLIDAYS) == 14
    assert len({holiday_date for holiday_date, _ in VIC_2026_STATEWIDE_HOLIDAYS}) == 14


def test_coverage_is_versioned_and_does_not_imply_holiday_from_absence() -> None:
    assert is_date_inside_trusted_coverage(date(2026, 1, 2)) is True
    assert statewide_holiday_name(date(2026, 1, 2)) is None
    assert statewide_holiday_name(date(2026, 1, 1)) == "New Year's Day"
    assert is_date_inside_trusted_coverage(date(2025, 12, 31)) is False
    assert is_date_inside_trusted_coverage(date(2027, 1, 1)) is False
    assert (
        is_date_inside_trusted_coverage(date(2026, 6, 15), calendar_version="AU-VIC-2027-v1")
        is False
    )
    assert is_range_inside_trusted_coverage(date(2026, 1, 1), date(2026, 12, 31)) is True
    assert is_range_inside_trusted_coverage(date(2026, 12, 31), date(2027, 1, 1)) is False

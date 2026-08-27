"""Version-controlled trusted VIC public-holiday coverage for V4 Stage 1."""

from datetime import date
from typing import Final

V4_CALENDAR_JURISDICTION: Final = "AU-VIC"
V4_CALENDAR_VERSION: Final = "AU-VIC-2026-v1"
V4_CALENDAR_COVERAGE_START: Final = date(2026, 1, 1)
V4_CALENDAR_COVERAGE_END: Final = date(2026, 12, 31)
V4_RULESET_VERSION: Final = "v4-annual-leave-1"

VIC_2026_STATEWIDE_HOLIDAYS: Final = (
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


def is_trusted_calendar(
    *,
    jurisdiction: str,
    calendar_version: str,
) -> bool:
    return jurisdiction == V4_CALENDAR_JURISDICTION and calendar_version == V4_CALENDAR_VERSION


def is_date_inside_trusted_coverage(
    value: date,
    *,
    jurisdiction: str = V4_CALENDAR_JURISDICTION,
    calendar_version: str = V4_CALENDAR_VERSION,
) -> bool:
    """Return True only when the date sits inside the versioned coverage window."""

    if not is_trusted_calendar(jurisdiction=jurisdiction, calendar_version=calendar_version):
        return False
    return V4_CALENDAR_COVERAGE_START <= value <= V4_CALENDAR_COVERAGE_END


def is_range_inside_trusted_coverage(
    start_date: date,
    end_date: date,
    *,
    jurisdiction: str = V4_CALENDAR_JURISDICTION,
    calendar_version: str = V4_CALENDAR_VERSION,
) -> bool:
    return is_date_inside_trusted_coverage(
        start_date,
        jurisdiction=jurisdiction,
        calendar_version=calendar_version,
    ) and is_date_inside_trusted_coverage(
        end_date,
        jurisdiction=jurisdiction,
        calendar_version=calendar_version,
    )


def statewide_holiday_name(value: date) -> str | None:
    """Return the seeded statewide holiday name, or None if the date is not a holiday."""

    for holiday_date, holiday_name in VIC_2026_STATEWIDE_HOLIDAYS:
        if holiday_date == value:
            return holiday_name
    return None

from datetime import date
from unittest.mock import Mock

from sqlalchemy.orm import Session

from app.workflow.calendar import V4_CALENDAR_VERSION, VIC_2026_STATEWIDE_HOLIDAYS
from app.workflow.calendar_service import CalendarCoverage, TrustedHolidayCalendarService
from app.workflow.holiday_repository import HolidayRecord


class _StaticHolidayRepository:
    def list_holidays(
        self,
        session: Session,
        *,
        jurisdiction: str,
        start_date: date,
        end_date: date,
        calendar_version: str = V4_CALENDAR_VERSION,
    ) -> tuple[HolidayRecord, ...]:
        del session
        return tuple(
            HolidayRecord(
                jurisdiction=jurisdiction,
                holiday_date=holiday_date,
                holiday_name=holiday_name,
                calendar_version=calendar_version,
            )
            for holiday_date, holiday_name in VIC_2026_STATEWIDE_HOLIDAYS
            if start_date <= holiday_date <= end_date
        )


def test_trusted_calendar_service_exposes_version_and_coverage() -> None:
    service = TrustedHolidayCalendarService(_StaticHolidayRepository())
    session = Mock(spec=Session)

    assert service.calendar_version == "AU-VIC-2026-v1"
    assert service.jurisdiction == "AU-VIC"

    covered_empty = service.holidays_for_range(
        session,
        jurisdiction="AU-VIC",
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
    )
    covered_holiday = service.holidays_for_range(
        session,
        jurisdiction="AU-VIC",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
    )
    unresolved = service.holidays_for_range(
        session,
        jurisdiction="AU-VIC",
        start_date=date(2027, 1, 1),
        end_date=date(2027, 1, 1),
    )

    assert covered_empty.coverage is CalendarCoverage.COVERED
    assert covered_empty.holidays == ()
    assert covered_holiday.holidays[0].holiday_name == "New Year's Day"
    assert unresolved.coverage is CalendarCoverage.UNRESOLVED
    assert unresolved.holidays == ()
    assert service.is_range_covered(
        jurisdiction="AU-VIC",
        start_date=date(2026, 12, 31),
        end_date=date(2026, 12, 31),
    )
    assert not service.is_range_covered(
        jurisdiction="AU-NSW",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
    )

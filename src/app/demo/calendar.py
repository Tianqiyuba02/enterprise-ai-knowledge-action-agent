"""M3-only trusted public-demo calendar coverage, additive to sealed V4."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

from sqlalchemy.orm import Session

from app.workflow.holiday_repository import HolidayCalendarRepository, HolidayRecord

M3_DEMO_CALENDAR_VERSION: Final = "AU-VIC-2026-2028-v1"
M3_DEMO_CALENDAR_JURISDICTION: Final = "AU-VIC"
M3_DEMO_CALENDAR_START: Final = date(2026, 1, 1)
M3_DEMO_CALENDAR_END: Final = date(2028, 12, 31)


def _contains_unresolved_afl_candidate(start_date: date, end_date: date) -> bool:
    current = start_date
    while current <= end_date:
        if current.year in {2027, 2028} and current.month in {9, 10} and current.weekday() == 4:
            return True
        current += timedelta(days=1)
    return False


@dataclass(frozen=True, slots=True)
class DemoCalendarResult:
    covered: bool
    reason: str | None
    holidays: tuple[HolidayRecord, ...]


class DemoHolidayCalendarService:
    def __init__(self, repository: HolidayCalendarRepository | None = None) -> None:
        self._repository = repository or HolidayCalendarRepository()

    def holidays_for_range(
        self,
        session: Session,
        *,
        jurisdiction: str,
        start_date: date,
        end_date: date,
    ) -> DemoCalendarResult:
        if jurisdiction != M3_DEMO_CALENDAR_JURISDICTION:
            return DemoCalendarResult(False, "unsupported_jurisdiction", ())
        if not M3_DEMO_CALENDAR_START <= start_date <= end_date <= M3_DEMO_CALENDAR_END:
            return DemoCalendarResult(False, "outside_versioned_coverage", ())
        if _contains_unresolved_afl_candidate(start_date, end_date):
            return DemoCalendarResult(False, "future_holiday_not_finalized", ())
        holidays = self._repository.list_holidays(
            session,
            jurisdiction=jurisdiction,
            start_date=start_date,
            end_date=end_date,
            calendar_version=M3_DEMO_CALENDAR_VERSION,
        )
        return DemoCalendarResult(True, None, holidays)

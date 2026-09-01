"""Read primitives for the trusted public-holiday calendar."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.workflow_models import PublicHoliday
from app.workflow.calendar import V4_CALENDAR_VERSION, is_range_inside_trusted_coverage


@dataclass(frozen=True, slots=True)
class HolidayRecord:
    jurisdiction: str
    holiday_date: date
    holiday_name: str
    calendar_version: str


class HolidayCalendarRepository:
    """Fetch seeded holidays. Coverage is version-controlled, not implied by absence."""

    def list_holidays(
        self,
        session: Session,
        *,
        jurisdiction: str,
        start_date: date,
        end_date: date,
        calendar_version: str = V4_CALENDAR_VERSION,
    ) -> tuple[HolidayRecord, ...]:
        rows = session.execute(
            select(PublicHoliday)
            .where(
                PublicHoliday.jurisdiction == jurisdiction,
                PublicHoliday.calendar_version == calendar_version,
                PublicHoliday.holiday_date >= start_date,
                PublicHoliday.holiday_date <= end_date,
            )
            .order_by(PublicHoliday.holiday_date)
        ).scalars()
        return tuple(
            HolidayRecord(
                jurisdiction=row.jurisdiction,
                holiday_date=row.holiday_date,
                holiday_name=row.holiday_name,
                calendar_version=row.calendar_version,
            )
            for row in rows
        )

    def calendar_version(self) -> str:
        return V4_CALENDAR_VERSION

    def range_is_inside_trusted_coverage(
        self,
        start_date: date,
        end_date: date,
        *,
        jurisdiction: str,
        calendar_version: str = V4_CALENDAR_VERSION,
    ) -> bool:
        return is_range_inside_trusted_coverage(
            start_date,
            end_date,
            jurisdiction=jurisdiction,
            calendar_version=calendar_version,
        )

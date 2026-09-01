"""Deterministic trusted holiday-calendar service. Makes no network calls."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from sqlalchemy.orm import Session

from app.workflow.calendar import (
    V4_CALENDAR_COVERAGE_END,
    V4_CALENDAR_COVERAGE_START,
    V4_CALENDAR_JURISDICTION,
    V4_CALENDAR_VERSION,
    is_range_inside_trusted_coverage,
)
from app.workflow.holiday_repository import HolidayCalendarRepository, HolidayRecord


class CalendarCoverage(StrEnum):
    COVERED = "covered"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class HolidayCalendarResult:
    jurisdiction: str
    calendar_version: str
    coverage: CalendarCoverage
    coverage_start: date
    coverage_end: date
    holidays: tuple[HolidayRecord, ...]


class TrustedHolidayCalendarService:
    """Resolve holidays only from trusted jurisdiction plus versioned coverage."""

    def __init__(self, repository: HolidayCalendarRepository) -> None:
        self._repository = repository

    @property
    def calendar_version(self) -> str:
        return V4_CALENDAR_VERSION

    @property
    def jurisdiction(self) -> str:
        return V4_CALENDAR_JURISDICTION

    def holidays_for_range(
        self,
        session: Session,
        *,
        jurisdiction: str,
        start_date: date,
        end_date: date,
        calendar_version: str = V4_CALENDAR_VERSION,
    ) -> HolidayCalendarResult:
        if jurisdiction != V4_CALENDAR_JURISDICTION:
            return HolidayCalendarResult(
                jurisdiction=jurisdiction,
                calendar_version=calendar_version,
                coverage=CalendarCoverage.UNRESOLVED,
                coverage_start=V4_CALENDAR_COVERAGE_START,
                coverage_end=V4_CALENDAR_COVERAGE_END,
                holidays=(),
            )
        if not is_range_inside_trusted_coverage(
            start_date,
            end_date,
            jurisdiction=jurisdiction,
            calendar_version=calendar_version,
        ):
            return HolidayCalendarResult(
                jurisdiction=jurisdiction,
                calendar_version=calendar_version,
                coverage=CalendarCoverage.UNRESOLVED,
                coverage_start=V4_CALENDAR_COVERAGE_START,
                coverage_end=V4_CALENDAR_COVERAGE_END,
                holidays=(),
            )
        holidays = self._repository.list_holidays(
            session,
            jurisdiction=jurisdiction,
            start_date=start_date,
            end_date=end_date,
            calendar_version=calendar_version,
        )
        return HolidayCalendarResult(
            jurisdiction=jurisdiction,
            calendar_version=calendar_version,
            coverage=CalendarCoverage.COVERED,
            coverage_start=V4_CALENDAR_COVERAGE_START,
            coverage_end=V4_CALENDAR_COVERAGE_END,
            holidays=holidays,
        )

    def is_range_covered(
        self,
        *,
        jurisdiction: str,
        start_date: date,
        end_date: date,
        calendar_version: str = V4_CALENDAR_VERSION,
    ) -> bool:
        return is_range_inside_trusted_coverage(
            start_date,
            end_date,
            jurisdiction=jurisdiction,
            calendar_version=calendar_version,
        )

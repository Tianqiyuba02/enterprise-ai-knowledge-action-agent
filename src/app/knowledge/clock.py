"""Trusted Melbourne-aware date source for document authority filtering."""

from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

MELBOURNE_TIMEZONE = ZoneInfo("Australia/Melbourne")


class TrustedClock(Protocol):
    def today(self) -> date: ...


class MelbourneClock:
    """Return the current calendar date in Australia/Melbourne."""

    def today(self) -> date:
        return datetime.now(MELBOURNE_TIMEZONE).date()

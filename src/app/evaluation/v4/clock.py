"""Evaluator-owned trusted business clock for v4-product-dev-1.

This clock is injected at evaluator construction. It does not replace the
PostgreSQL operational clock used for created_at, TTL, leases, or audit rows.
"""

from datetime import date
from typing import Final

V4_DEVELOPMENT_BUSINESS_DATE: Final = date(2026, 8, 28)
V4_DEVELOPMENT_BUSINESS_TIMEZONE: Final = "Australia/Melbourne"
V4_DEVELOPMENT_BUSINESS_CLOCK_VERSION: Final = "v4-product-dev-1"


class V4DevelopmentBusinessClock:
    """Fixed Australia/Melbourne business date for development evaluation."""

    def today(self) -> date:
        return V4_DEVELOPMENT_BUSINESS_DATE


def business_clock_identity() -> dict[str, str]:
    return {
        "version": V4_DEVELOPMENT_BUSINESS_CLOCK_VERSION,
        "trusted_date": V4_DEVELOPMENT_BUSINESS_DATE.isoformat(),
        "timezone": V4_DEVELOPMENT_BUSINESS_TIMEZONE,
    }

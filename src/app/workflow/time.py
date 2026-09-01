"""Authoritative database time for V4 expiry decisions."""

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session


def database_now(session: Session) -> datetime:
    """Return timezone-aware PostgreSQL clock_timestamp(), not client or model time."""

    value = session.execute(text("SELECT clock_timestamp()")).scalar_one()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value

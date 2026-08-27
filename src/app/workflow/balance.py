"""Deterministic effective-balance and overlap primitives."""

from datetime import date
from decimal import Decimal

from app.workflow.canonical import quantize_hours, require_decimal


def effective_available_hours(
    *,
    trusted_base_balance_hours: Decimal,
    committed_submitted_hours: Decimal,
) -> Decimal:
    """trusted base minus hours committed by active submitted V4 annual leave."""

    return quantize_hours(
        require_decimal(trusted_base_balance_hours) - require_decimal(committed_submitted_hours)
    )


def date_ranges_overlap(
    start_date: date,
    end_date: date,
    other_start: date,
    other_end: date,
) -> bool:
    return start_date <= other_end and other_start <= end_date

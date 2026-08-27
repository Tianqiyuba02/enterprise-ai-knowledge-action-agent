"""Trusted authority-snapshot and executable-draft representations."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.workflow.canonical import (
    authority_snapshot_hash,
    draft_hash,
    quantize_hours,
    require_decimal,
)


@dataclass(frozen=True, slots=True)
class AuthoritySnapshot:
    """Trusted executable-preparation inputs. Never constructed from model output."""

    employee_id: str
    jurisdiction: str
    work_days: tuple[str, ...]
    hours_per_day: Decimal
    timezone: str
    trusted_base_balance_hours: Decimal
    committed_submitted_hours: Decimal
    effective_available_hours: Decimal
    calendar_version: str
    ruleset_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "hours_per_day",
            quantize_hours(require_decimal(self.hours_per_day)),
        )
        object.__setattr__(
            self,
            "trusted_base_balance_hours",
            quantize_hours(require_decimal(self.trusted_base_balance_hours)),
        )
        object.__setattr__(
            self,
            "committed_submitted_hours",
            quantize_hours(require_decimal(self.committed_submitted_hours)),
        )
        object.__setattr__(
            self,
            "effective_available_hours",
            quantize_hours(require_decimal(self.effective_available_hours)),
        )

    def fingerprint(self) -> str:
        return authority_snapshot_hash(self)


@dataclass(frozen=True, slots=True)
class CanonicalDraft:
    """Exact structured draft the human will later confirm."""

    action_type: str
    leave_type: str
    start_date: date
    end_date: date
    requested_hours: Decimal
    projected_balance_hours: Decimal
    readiness: str
    reason: str | None
    calendar_version: str
    ruleset_version: str
    authority_snapshot_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requested_hours",
            quantize_hours(require_decimal(self.requested_hours)),
        )
        object.__setattr__(
            self,
            "projected_balance_hours",
            quantize_hours(require_decimal(self.projected_balance_hours)),
        )

    def fingerprint(self) -> str:
        return draft_hash(self)

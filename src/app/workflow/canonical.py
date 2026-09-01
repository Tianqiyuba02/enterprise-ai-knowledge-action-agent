"""Versioned deterministic canonicalization and hash primitives for V4."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Final
from uuid import UUID

CANONICALIZATION_VERSION: Final = "v4-canonical-1"
HOUR_QUANTUM: Final = Decimal("0.01")


def require_decimal(value: Decimal) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError("canonical values must use Decimal, not float")
    return value


def quantize_hours(value: Decimal) -> Decimal:
    return require_decimal(value).quantize(HOUR_QUANTUM)


def canonicalize(value: object) -> bytes:
    """Return a stable UTF-8 canonical JSON document with sorted keys."""

    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        indent=None,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_digest(value: object) -> str:
    return hashlib.sha256(canonicalize(value)).hexdigest()


def draft_hash(draft: object) -> str:
    payload = _mapping(draft)
    required = {
        "action_type",
        "leave_type",
        "start_date",
        "end_date",
        "requested_hours",
        "projected_balance_hours",
        "readiness",
        "reason",
        "calendar_version",
        "ruleset_version",
        "authority_snapshot_hash",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"canonical draft is missing fields: {sorted(missing)}")
    forbidden = {"assistant_prose", "model_prose", "message"}
    if forbidden & set(payload):
        raise ValueError("canonical draft must not include assistant prose")
    return sha256_digest(
        {
            "canonicalization_version": CANONICALIZATION_VERSION,
            "kind": "draft_hash",
            "action_type": payload["action_type"],
            "leave_type": payload["leave_type"],
            "start_date": payload["start_date"],
            "end_date": payload["end_date"],
            "requested_hours": payload["requested_hours"],
            "projected_balance_hours": payload["projected_balance_hours"],
            "readiness": payload["readiness"],
            "reason": payload["reason"],
            "calendar_version": payload["calendar_version"],
            "ruleset_version": payload["ruleset_version"],
            "authority_snapshot_hash": payload["authority_snapshot_hash"],
        }
    )


def authority_snapshot_hash(snapshot: object) -> str:
    payload = _mapping(snapshot)
    return sha256_digest(
        {
            "canonicalization_version": CANONICALIZATION_VERSION,
            "kind": "authority_snapshot_hash",
            **payload,
        }
    )


def business_request_key(
    *,
    employee_id: str,
    leave_type: str,
    start_date: date,
    end_date: date,
) -> str:
    return sha256_digest(
        {
            "canonicalization_version": CANONICALIZATION_VERSION,
            "kind": "business_request_key",
            "employee_id": employee_id,
            "leave_type": leave_type,
            "start_date": start_date,
            "end_date": end_date,
        }
    )


def _mapping(value: object) -> dict[str, object]:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    raise TypeError("canonical payload must be a mapping or dataclass")


def _normalize(value: object) -> object:
    if value is None or isinstance(value, bool | str | int):
        return value
    if isinstance(value, float):
        raise TypeError("float is not allowed in V4 canonicalization")
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, Decimal):
        return format(quantize_hours(value), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TypeError("canonical datetimes must be timezone-aware")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _normalize(asdict(value))
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_normalize(item) for item in value]
    raise TypeError(f"unsupported canonical type: {type(value)!r}")

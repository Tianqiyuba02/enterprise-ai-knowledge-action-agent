"""Deterministic PostgreSQL transaction-scoped advisory locks.

Lock keys are derived from SHA-256. Python ``hash()`` is never used.
Collisions may over-serialize but must never weaken correctness.
"""

import hashlib
from typing import Final

from sqlalchemy import text
from sqlalchemy.orm import Session

BUSINESS_REQUEST_LOCK_NAMESPACE: Final = "v4-business-request"
EMPLOYEE_LOCK_NAMESPACE: Final = "v4-employee"


def signed_advisory_lock_key(namespace: str, value: str) -> int:
    """Return a stable signed 64-bit key from SHA-256(namespace, value)."""

    digest = hashlib.sha256(f"{namespace}\0{value}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def acquire_transaction_advisory_lock(
    session: Session,
    *,
    namespace: str,
    value: str,
) -> int:
    """Acquire ``pg_advisory_xact_lock`` for the remainder of the transaction."""

    lock_key = signed_advisory_lock_key(namespace, value)
    session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
    return lock_key


def acquire_business_request_lock(session: Session, business_request_key: str) -> int:
    return acquire_transaction_advisory_lock(
        session,
        namespace=BUSINESS_REQUEST_LOCK_NAMESPACE,
        value=business_request_key,
    )


def acquire_employee_lock(session: Session, employee_id: str) -> int:
    return acquire_transaction_advisory_lock(
        session,
        namespace=EMPLOYEE_LOCK_NAMESPACE,
        value=employee_id,
    )

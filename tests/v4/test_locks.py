import hashlib
from pathlib import Path

from app.workflow.locks import (
    BUSINESS_REQUEST_LOCK_NAMESPACE,
    EMPLOYEE_LOCK_NAMESPACE,
    signed_advisory_lock_key,
)


def test_advisory_lock_keys_are_stable_sha256_not_python_hash() -> None:
    first = signed_advisory_lock_key(BUSINESS_REQUEST_LOCK_NAMESPACE, "abc")
    second = signed_advisory_lock_key(BUSINESS_REQUEST_LOCK_NAMESPACE, "abc")
    other = signed_advisory_lock_key(EMPLOYEE_LOCK_NAMESPACE, "abc")
    digest = hashlib.sha256(b"v4-business-request\0abc").digest()
    expected = int.from_bytes(digest[:8], byteorder="big", signed=True)

    assert first == second == expected
    assert other != first
    assert isinstance(first, int)
    assert -(2**63) <= first < 2**63
    source = (
        Path(__file__)
        .resolve()
        .parents[2]
        .joinpath("src", "app", "workflow", "locks.py")
        .read_text(encoding="utf-8")
    )
    assert "hashlib.sha256" in source
    assert "hash(f" not in source
    assert "hash(namespace" not in source
    assert "hash(value" not in source

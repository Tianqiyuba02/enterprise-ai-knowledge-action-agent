"""Opaque confirmation-token generation and constant-time digest comparison."""

import hashlib
import hmac
import secrets

CONFIRMATION_TOKEN_BYTES = 32


def generate_confirmation_token() -> str:
    """Return a 256-bit hex token. Callers must not persist or log this value."""

    return secrets.token_hex(CONFIRMATION_TOKEN_BYTES)


def hash_confirmation_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def confirmation_tokens_match(*, plaintext: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_confirmation_token(plaintext), token_hash)

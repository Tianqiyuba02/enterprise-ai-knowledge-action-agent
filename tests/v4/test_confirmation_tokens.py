from app.api.application import create_app
from app.workflow.tokens import (
    CONFIRMATION_TOKEN_BYTES,
    confirmation_tokens_match,
    generate_confirmation_token,
    hash_confirmation_token,
)


def test_confirmation_token_is_256_bit_hex_and_compared_constantly() -> None:
    token = generate_confirmation_token()
    digest = hash_confirmation_token(token)

    assert len(bytes.fromhex(token)) == CONFIRMATION_TOKEN_BYTES
    assert confirmation_tokens_match(plaintext=token, token_hash=digest)
    assert not confirmation_tokens_match(plaintext="0" * 64, token_hash=digest)


def test_confirmation_http_rejects_unauthenticated_and_malformed_bodies() -> None:
    app = create_app()
    from fastapi.testclient import TestClient

    action_id = "11111111-1111-1111-1111-111111111111"
    with TestClient(app, raise_server_exceptions=False) as client:
        missing = client.get(f"/api/v1/actions/{action_id}")
        malformed = client.post(
            f"/api/v1/actions/{action_id}/confirm",
            headers={"X-Demo-Session": "demo-v1-7f4c2a91"},
            json={"token": "nope", "confirmed": True},
        )

    assert missing.status_code == 401
    assert missing.json()["error_code"] == "invalid_demo_session"
    assert malformed.status_code == 422
    assert malformed.json()["error_code"] == "validation_error"

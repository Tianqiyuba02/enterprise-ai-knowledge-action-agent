from fastapi.testclient import TestClient

PRIMARY_SESSION = {"X-Demo-Session": "demo-v1-7f4c2a91"}
SECONDARY_SESSION = {"X-Demo-Session": "demo-v1-3b8e6d50"}


def public_error(response) -> tuple[int, str, str]:
    body = response.json()
    assert body["request_id"] == response.headers["x-request-id"]
    return response.status_code, body["error_code"], body["message"]


def test_valid_demo_session_resolves_correct_employee(api_client: TestClient) -> None:
    primary = api_client.get("/api/v1/me/profile", headers=PRIMARY_SESSION)
    secondary = api_client.get("/api/v1/me/profile", headers=SECONDARY_SESSION)

    assert primary.status_code == 200
    assert primary.json()["employee_id"] == "EMP-1001"
    assert primary.json()["full_name"] == "Alex Morgan"
    assert secondary.status_code == 200
    assert secondary.json()["employee_id"] == "EMP-1002"
    assert secondary.json()["full_name"] == "Sam Lee"


def test_missing_and_invalid_sessions_fail_safely(api_client: TestClient) -> None:
    missing = api_client.get("/api/v1/me/profile")
    invalid = api_client.get("/api/v1/me/profile", headers={"X-Demo-Session": "not-a-session"})

    assert public_error(missing) == (
        401,
        "invalid_demo_session",
        "A valid demo session is required.",
    )
    assert public_error(invalid) == public_error(missing)


def test_request_body_cannot_override_authenticated_employee(api_client: TestClient) -> None:
    response = api_client.request(
        "GET",
        "/api/v1/me/profile",
        headers=PRIMARY_SESSION,
        json={"employee_id": "EMP-1002"},
    )

    assert response.status_code == 200
    assert response.json()["employee_id"] == "EMP-1001"


def test_query_parameter_cannot_override_authenticated_employee(
    api_client: TestClient,
) -> None:
    response = api_client.get(
        "/api/v1/me/profile?employee_id=EMP-1002",
        headers=PRIMARY_SESSION,
    )

    assert response.status_code == 200
    assert response.json()["employee_id"] == "EMP-1001"
    assert response.json()["full_name"] == "Alex Morgan"


def test_leave_balances_are_scoped_to_authenticated_employee(api_client: TestClient) -> None:
    primary = api_client.get("/api/v1/me/leave/balances", headers=PRIMARY_SESSION)
    secondary = api_client.get("/api/v1/me/leave/balances", headers=SECONDARY_SESSION)

    assert primary.status_code == 200
    assert primary.json()["balances"] == [
        {"leave_type": "annual", "balance_hours": 76.0, "as_of_date": "2026-08-24"},
        {"leave_type": "personal", "balance_hours": 38.0, "as_of_date": "2026-08-24"},
    ]
    assert secondary.status_code == 200
    assert secondary.json()["balances"] == [
        {"leave_type": "annual", "balance_hours": 48.0, "as_of_date": "2026-08-24"},
        {"leave_type": "personal", "balance_hours": 24.0, "as_of_date": "2026-08-24"},
    ]


def test_employee_can_retrieve_own_ticket(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/me/tickets/TKT-1001", headers=PRIMARY_SESSION)

    assert response.status_code == 200
    assert response.json()["ticket_id"] == "TKT-1001"
    assert response.json()["status"] == "open"
    assert "employee_id" not in response.json()


def test_cross_user_and_nonexistent_tickets_are_publicly_equivalent(
    api_client: TestClient,
) -> None:
    cross_user = api_client.get("/api/v1/me/tickets/TKT-2001", headers=PRIMARY_SESSION)
    nonexistent = api_client.get("/api/v1/me/tickets/TKT-9999", headers=PRIMARY_SESSION)

    assert public_error(cross_user) == (
        404,
        "ticket_not_found",
        "The requested ticket was not found.",
    )
    assert public_error(nonexistent) == public_error(cross_user)


def test_malformed_ticket_id_uses_validation_envelope(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/me/tickets/not-valid", headers=PRIMARY_SESSION)

    assert public_error(response) == (
        422,
        "validation_error",
        "Request validation failed.",
    )

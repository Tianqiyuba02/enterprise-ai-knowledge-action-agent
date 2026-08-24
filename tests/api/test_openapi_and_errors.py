from fastapi.testclient import TestClient


def test_openapi_lists_v1_paths_and_typed_contracts(api_client: TestClient) -> None:
    response = api_client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert set(schema["paths"]) == {
        "/api/v1/chat",
        "/api/v1/health",
        "/api/v1/me/leave/balances",
        "/api/v1/me/profile",
        "/api/v1/me/tickets/{ticket_id}",
    }
    component_names = set(schema["components"]["schemas"])
    assert {
        "ChatRequest",
        "ChatResponse",
        "EmployeeProfileResponse",
        "ErrorResponse",
        "HealthResponse",
        "LeaveBalancesResponse",
        "TicketResponse",
    } <= component_names
    chat_properties = schema["components"]["schemas"]["ChatRequest"]["properties"]
    assert set(chat_properties) == {"question"}
    assert "employee_id" not in chat_properties


def test_swagger_documentation_renders(api_client: TestClient) -> None:
    response = api_client.get("/docs")

    assert response.status_code == 200
    assert "Swagger UI" in response.text


def test_unknown_route_uses_consistent_error_envelope(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/not-a-route")

    assert response.status_code == 404
    assert response.json()["error_code"] == "not_found"
    assert response.json()["message"] == "The requested resource was not found."
    assert response.json()["request_id"] == response.headers["x-request-id"]

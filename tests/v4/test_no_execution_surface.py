from pathlib import Path

from fastapi.testclient import TestClient

from app.agent.contracts import V3_TOOL_ALLOWLIST, V3ToolName
from app.api.application import create_app

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_ROUTE_FRAGMENTS = (
    "confirm",
    "workflow",
    "action_id",
    "leave-request",
    "leave_request",
    "execute",
)
FORBIDDEN_TOOL_FRAGMENTS = ("execute", "submit", "confirm", "workflow")


def test_no_v4_execution_route_exists() -> None:
    app = create_app()
    paths = set(app.openapi()["paths"])

    assert "/api/v1/assistant/query" in paths
    assert "/api/v1/me/leave/balances" in paths
    for path in paths:
        lowered = path.lower()
        assert not any(fragment in lowered for fragment in FORBIDDEN_ROUTE_FRAGMENTS)

    with TestClient(app, raise_server_exceptions=False) as client:
        missing = client.post("/api/v1/actions/confirm", json={"token": "nope"})
        assert missing.status_code == 404


def test_no_v4_execution_tool_exists() -> None:
    names = {name.value for name in V3_TOOL_ALLOWLIST}
    assert names == {
        V3ToolName.KNOWLEDGE_QUERY.value,
        V3ToolName.GET_MY_PROFILE.value,
        V3ToolName.GET_MY_LEAVE_BALANCES.value,
        V3ToolName.GET_MY_TICKET.value,
        V3ToolName.PREPARE_LEAVE_REQUEST.value,
    }
    assert all(not any(fragment in name for fragment in FORBIDDEN_TOOL_FRAGMENTS) for name in names)
    assert V3_TOOL_ALLOWLIST[V3ToolName.PREPARE_LEAVE_REQUEST].capability.value == "prepare"


def test_langgraph_is_pinned_without_langchain_application_api() -> None:
    requirements = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "langgraph==1.2.11" in requirements
    assert "langgraph-checkpoint-postgres==3.1.2" in requirements
    assert '"langchain"' not in requirements
    assert "langchain==" not in requirements

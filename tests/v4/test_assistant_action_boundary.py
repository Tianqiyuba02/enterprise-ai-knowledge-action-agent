from pathlib import Path

from app.agent.contracts import V3_TOOL_ALLOWLIST, V3ToolName

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_TOOLS = (
    "create_action",
    "issue_confirmation",
    "confirm",
    "execute",
    "cancel",
    "submit",
)


def test_tool_registry_has_no_action_or_execution_tools() -> None:
    names = {name.value for name in V3_TOOL_ALLOWLIST}
    assert names == {
        V3ToolName.KNOWLEDGE_QUERY.value,
        V3ToolName.GET_MY_PROFILE.value,
        V3ToolName.GET_MY_LEAVE_BALANCES.value,
        V3ToolName.GET_MY_TICKET.value,
        V3ToolName.PREPARE_LEAVE_REQUEST.value,
    }
    assert all(forbidden not in names for forbidden in FORBIDDEN_TOOLS)
    dispatcher = (ROOT / "src" / "app" / "agent" / "dispatcher.py").read_text(encoding="utf-8")
    service = (ROOT / "src" / "app" / "agent" / "service.py").read_text(encoding="utf-8")
    application = (ROOT / "src" / "app" / "api" / "assistant_application.py").read_text(
        encoding="utf-8"
    )
    assert "ActionCreationService" not in dispatcher
    assert "ActionCreationService" not in service
    assert "confirmation_token" not in service
    assert "action_id" not in service
    assert "AgentService.run" in application or "self._agent.run" in application
    assert "create_or_reuse" in application

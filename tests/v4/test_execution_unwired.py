from pathlib import Path

from app.agent.contracts import V3_TOOL_ALLOWLIST, V3ToolName

ROOT = Path(__file__).resolve().parents[2]


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_assistant_and_public_confirmation_still_do_not_execute() -> None:
    confirmation = _read("src", "app", "workflow", "confirmation.py")
    routes = _read("src", "app", "api", "routes", "actions.py")
    assistant = _read("src", "app", "agent", "service.py")
    combined = "\n".join((confirmation, routes, assistant))
    assert "LeaveSubmissionExecutor" not in combined
    assert "ExecutionReservationService" not in combined
    assert "reserve_execution" not in combined
    assert "execute_business_action" not in combined


def test_stage_4_still_has_no_llm_execution_tool() -> None:
    names = {name.value for name in V3_TOOL_ALLOWLIST}
    assert V3ToolName.PREPARE_LEAVE_REQUEST.value in names
    assert all("execute" not in name for name in names)
    assert all("submit" not in name for name in names)

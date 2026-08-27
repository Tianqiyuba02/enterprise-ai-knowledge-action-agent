from pathlib import Path

from app.agent.contracts import V3_TOOL_ALLOWLIST, V3ToolName

ROOT = Path(__file__).resolve().parents[2]


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_stage_4a_graph_and_worker_do_not_call_execution() -> None:
    graph = _read("src", "app", "workflow", "graph.py")
    worker = _read("src", "app", "workflow", "worker.py")
    confirmation = _read("src", "app", "workflow", "confirmation.py")
    routes = _read("src", "app", "api", "routes", "actions.py")
    combined = "\n".join((graph, worker, confirmation, routes))
    assert "LeaveSubmissionExecutor" not in combined
    assert "ExecutionReservationService" not in combined
    assert "reserve_execution" not in combined
    assert "execute_business_action" not in combined
    assert "confirmed_barrier" in graph
    assert 'graph.add_edge("confirmed_barrier", END)' in graph


def test_stage_4a_still_has_no_llm_execution_tool() -> None:
    names = {name.value for name in V3_TOOL_ALLOWLIST}
    assert V3ToolName.PREPARE_LEAVE_REQUEST.value in names
    assert all("execute" not in name for name in names)
    assert all("submit" not in name for name in names)

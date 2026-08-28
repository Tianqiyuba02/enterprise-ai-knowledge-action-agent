from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.workflow.domain import WorkflowState
from app.workflow.graph import (
    EXECUTION_NODE_NAMES,
    WAITING_FOR_CONFIRMATION,
    AuthoritativeObservation,
    build_workflow_graph,
    interrupt_payload,
    route_authoritative_state,
)
from app.workflow.orchestration import _wake_payload

ROOT = Path(__file__).resolve().parents[2]
GRAPH_SOURCE = (ROOT / "src" / "app" / "workflow" / "graph.py").read_text(encoding="utf-8")
ORCH_SOURCE = (ROOT / "src" / "app" / "workflow" / "orchestration.py").read_text(encoding="utf-8")


def _observation(state: str) -> AuthoritativeObservation:
    return AuthoritativeObservation(
        action_id="11111111-1111-1111-1111-111111111111",
        revision=1,
        langgraph_thread_id="thread-action-1",
        state=state,
    )


def _compile(reload):
    return build_workflow_graph(reload).compile(checkpointer=InMemorySaver())


def test_empty_resume_is_normalized_to_a_wake_marker() -> None:
    assert _wake_payload(None) == {"wake": True}
    assert _wake_payload({}) == {"wake": True}
    assert _wake_payload({"confirmed": True}) == {"confirmed": True}


def test_interrupt_payload_is_json_safe_and_has_no_secrets() -> None:
    payload = interrupt_payload(
        {
            "action_id": "11111111-1111-1111-1111-111111111111",
            "revision": 1,
            "langgraph_thread_id": "thread-action-1",
        }
    )
    assert payload == {
        "action_id": "11111111-1111-1111-1111-111111111111",
        "revision": 1,
        "waiting_for": WAITING_FOR_CONFIRMATION,
    }
    forbidden = {
        "token",
        "token_hash",
        "challenge",
        "subject_id",
        "session_id",
        "assistant_prose",
        "execute",
    }
    assert forbidden.isdisjoint(payload)


def test_route_uses_postgres_observation_not_cached_graph_state() -> None:
    assert (
        route_authoritative_state(
            _observation(WorkflowState.CONFIRMED.value),
            execution_enabled=True,
        )
        == "confirmed_barrier"
    )
    assert (
        route_authoritative_state(
            _observation(WorkflowState.AWAITING_CONFIRMATION.value),
            execution_enabled=False,
        )
        == "await_confirmation"
    )
    assert (
        route_authoritative_state(
            _observation(WorkflowState.CANCELLED.value),
            execution_enabled=True,
        )
        == "terminal_barrier"
    )
    assert (
        route_authoritative_state(
            _observation(WorkflowState.EXECUTING.value),
            execution_enabled=False,
        )
        == "terminal_barrier"
    )
    assert (
        route_authoritative_state(
            _observation(WorkflowState.EXECUTING.value),
            execution_enabled=True,
        )
        == "reconcile_execution"
    )
    assert (
        route_authoritative_state(
            _observation(WorkflowState.UNKNOWN_OUTCOME.value),
            execution_enabled=True,
        )
        == "reconcile_execution"
    )


def test_resume_payload_cannot_confirm_or_execute() -> None:
    graph = _compile(lambda state: _observation(WorkflowState.AWAITING_CONFIRMATION.value))
    config = {"configurable": {"thread_id": "thread-action-1"}}
    first = graph.invoke(
        {
            "action_id": "11111111-1111-1111-1111-111111111111",
            "revision": 1,
            "langgraph_thread_id": "thread-action-1",
            "observed_state": WorkflowState.CONFIRMED.value,
        },
        config=config,
    )
    resumed = graph.invoke(
        Command(resume={"confirmed": True, "authorized": True, "execute": True}),
        config=config,
    )

    assert first["__interrupt__"][0].value["waiting_for"] == WAITING_FOR_CONFIRMATION
    assert resumed["__interrupt__"][0].value["waiting_for"] == WAITING_FOR_CONFIRMATION
    assert resumed["observed_state"] == WorkflowState.AWAITING_CONFIRMATION.value
    assert graph.get_state(config).next == ("await_confirmation",)


def test_empty_resume_payload_is_still_only_a_wake() -> None:
    calls = {"count": 0}

    def reload(state):
        calls["count"] += 1
        if calls["count"] == 1:
            return _observation(WorkflowState.AWAITING_CONFIRMATION.value)
        return _observation(WorkflowState.CONFIRMED.value)

    graph = _compile(reload)
    config = {"configurable": {"thread_id": "thread-action-1"}}
    graph.invoke(
        {
            "action_id": "11111111-1111-1111-1111-111111111111",
            "revision": 1,
            "langgraph_thread_id": "thread-action-1",
        },
        config=config,
    )
    result = graph.invoke(Command(resume=_wake_payload({})), config=config)

    assert "__interrupt__" not in result
    assert result["observed_state"] == WorkflowState.CONFIRMED.value


def test_confirmed_barrier_requires_authoritative_confirmed_state() -> None:
    calls = {"count": 0}

    def reload(state):
        calls["count"] += 1
        if calls["count"] == 1:
            return _observation(WorkflowState.AWAITING_CONFIRMATION.value)
        return _observation(WorkflowState.CONFIRMED.value)

    graph = _compile(reload)
    config = {"configurable": {"thread_id": "thread-action-1"}}
    graph.invoke(
        {
            "action_id": "11111111-1111-1111-1111-111111111111",
            "revision": 1,
            "langgraph_thread_id": "thread-action-1",
        },
        config=config,
    )
    result = graph.invoke(Command(resume={"confirmed": False}), config=config)

    assert "__interrupt__" not in result
    assert result["observed_state"] == WorkflowState.CONFIRMED.value
    assert graph.get_state(config).next == ()


def test_cancelled_or_executing_postgres_state_reaches_terminal_barrier() -> None:
    states = iter(
        [
            WorkflowState.AWAITING_CONFIRMATION.value,
            WorkflowState.CANCELLED.value,
            WorkflowState.CANCELLED.value,
            WorkflowState.CANCELLED.value,
        ]
    )

    graph = _compile(lambda state: _observation(next(states)))
    config = {"configurable": {"thread_id": "thread-action-1"}}
    graph.invoke(
        {
            "action_id": "11111111-1111-1111-1111-111111111111",
            "revision": 1,
            "langgraph_thread_id": "thread-action-1",
        },
        config=config,
    )
    result = graph.invoke(Command(resume={"execute": True}), config=config)

    assert "__interrupt__" not in result
    assert result["observed_state"] == WorkflowState.CANCELLED.value
    assert graph.get_state(config).next == ()


def test_threads_do_not_share_checkpointed_interrupt_state() -> None:
    def reload(state):
        return AuthoritativeObservation(
            action_id=state["action_id"],
            revision=state["revision"],
            langgraph_thread_id=state["langgraph_thread_id"],
            state=WorkflowState.AWAITING_CONFIRMATION.value,
        )

    graph = _compile(reload)
    first = graph.invoke(
        {
            "action_id": "11111111-1111-1111-1111-111111111111",
            "revision": 1,
            "langgraph_thread_id": "thread-action-1",
        },
        config={"configurable": {"thread_id": "thread-action-1"}},
    )
    second = graph.invoke(
        {
            "action_id": "22222222-2222-2222-2222-222222222222",
            "revision": 1,
            "langgraph_thread_id": "thread-action-2",
        },
        config={"configurable": {"thread_id": "thread-action-2"}},
    )

    assert first["__interrupt__"][0].value["action_id"].endswith("1111")
    assert second["__interrupt__"][0].value["action_id"].endswith("2222")
    assert (
        graph.get_state({"configurable": {"thread_id": "thread-action-1"}})
        .values["action_id"]
        .endswith("1111")
    )


def test_employee_graph_capability_excludes_execution_nodes() -> None:
    employee = build_workflow_graph(lambda state: _observation(WorkflowState.CONFIRMED.value))
    worker = build_workflow_graph(
        lambda state: _observation(WorkflowState.CONFIRMED.value),
        execution=_FakeExecution(),
    )
    assert EXECUTION_NODE_NAMES.isdisjoint(employee.nodes)
    assert set(worker.nodes) >= EXECUTION_NODE_NAMES


class _FakeExecution:
    def reserve(self, action_id: str, revision: int) -> str:
        return "RESERVED"

    def execute(self, action_id: str, revision: int) -> str:
        return "EXECUTING"

    def reconcile(self, action_id: str, revision: int) -> str:
        return "RECONCILING"

    def finalize(self, action_id: str, revision: int) -> str:
        return "SUCCEEDED"


def test_graph_source_has_no_provider_or_execution_surface() -> None:
    combined = f"{GRAPH_SOURCE}\n{ORCH_SOURCE}".lower()
    assert "gemini" not in combined
    assert "google.genai" not in combined
    assert "apply_revision_state" not in combined
    assert "checkpointer.setup" not in combined
    assert "saver.setup" not in combined
    assert "command(resume" in combined

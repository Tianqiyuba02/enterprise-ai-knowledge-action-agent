"""Durable V4 action-workflow graph. Resume is a wake signal, not confirmation."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.workflow.domain import TERMINAL_WORKFLOW_STATES, WorkflowState

WAITING_FOR_CONFIRMATION = "out_of_band_confirmation"
ROUTE_CONFIRMED = "confirmed_barrier"
ROUTE_AWAIT = "await_confirmation"
ROUTE_TERMINAL = "terminal_barrier"
RouteName = Literal["confirmed_barrier", "await_confirmation", "terminal_barrier"]


class WorkflowGraphState(TypedDict):
    action_id: str
    revision: int
    langgraph_thread_id: str
    observed_state: NotRequired[str | None]


@dataclass(frozen=True, slots=True)
class AuthoritativeObservation:
    action_id: str
    revision: int
    langgraph_thread_id: str
    state: str


ReloadObservation = Callable[[WorkflowGraphState], AuthoritativeObservation]


def interrupt_payload(state: WorkflowGraphState) -> dict[str, str | int]:
    return {
        "action_id": state["action_id"],
        "revision": state["revision"],
        "waiting_for": WAITING_FOR_CONFIRMATION,
    }


def observation_update(observation: AuthoritativeObservation) -> WorkflowGraphState:
    return {
        "action_id": observation.action_id,
        "revision": observation.revision,
        "langgraph_thread_id": observation.langgraph_thread_id,
        "observed_state": observation.state,
    }


def route_authoritative_state(
    observation: AuthoritativeObservation,
) -> RouteName:
    """Route from PostgreSQL state only. Cached graph observations never authorize."""

    if observation.state == WorkflowState.CONFIRMED.value:
        return ROUTE_CONFIRMED
    if observation.state == WorkflowState.AWAITING_CONFIRMATION.value:
        return ROUTE_AWAIT
    if observation.state in {state.value for state in TERMINAL_WORKFLOW_STATES}:
        return ROUTE_TERMINAL
    return ROUTE_TERMINAL


def build_workflow_graph(reload_observation: ReloadObservation) -> StateGraph:
    """Compile-ready graph that stops at confirmed_barrier and never executes."""

    def load_authoritative_revision(state: WorkflowGraphState) -> WorkflowGraphState:
        return observation_update(reload_observation(state))

    def await_confirmation(state: WorkflowGraphState) -> dict[str, object]:
        interrupt(interrupt_payload(state))
        return {}

    def reload_after_wake(state: WorkflowGraphState) -> WorkflowGraphState:
        return observation_update(reload_observation(state))

    def choose_route(state: WorkflowGraphState) -> RouteName:
        return route_authoritative_state(reload_observation(state))

    def confirmed_barrier(state: WorkflowGraphState) -> WorkflowGraphState:
        return observation_update(reload_observation(state))

    def terminal_barrier(state: WorkflowGraphState) -> WorkflowGraphState:
        return observation_update(reload_observation(state))

    graph: StateGraph = StateGraph(WorkflowGraphState)
    graph.add_node("load_authoritative_revision", load_authoritative_revision)
    graph.add_node("await_confirmation", await_confirmation)
    graph.add_node("reload_after_wake", reload_after_wake)
    graph.add_node("confirmed_barrier", confirmed_barrier)
    graph.add_node("terminal_barrier", terminal_barrier)
    graph.add_edge(START, "load_authoritative_revision")
    graph.add_edge("load_authoritative_revision", "await_confirmation")
    graph.add_edge("await_confirmation", "reload_after_wake")
    graph.add_conditional_edges(
        "reload_after_wake",
        choose_route,
        {
            ROUTE_CONFIRMED: "confirmed_barrier",
            ROUTE_AWAIT: "await_confirmation",
            ROUTE_TERMINAL: "terminal_barrier",
        },
    )
    graph.add_edge("confirmed_barrier", END)
    graph.add_edge("terminal_barrier", END)
    return graph

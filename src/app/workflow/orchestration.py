"""PostgreSQL-authority orchestration for the V4 workflow graph."""

from typing import Any
from uuid import UUID

from langgraph.types import Command
from sqlalchemy.orm import Session, sessionmaker

from app.config import KnowledgeSettings
from app.workflow.checkpointing import open_postgres_checkpointer
from app.workflow.domain import V4_REVISION, WorkflowState
from app.workflow.errors import (
    OrchestrationAuthorityError,
    ThreadBindingError,
    WorkflowOwnershipError,
    WorkflowRowNotFoundError,
)
from app.workflow.execution import ReservationOutcome
from app.workflow.graph import AuthoritativeObservation, WorkflowGraphState, build_workflow_graph
from app.workflow.runtime import WorkflowExecutionRuntime
from app.workflow.workflow_repository import WorkflowRepository


class WorkflowOrchestrationService:
    """Open/resume an action using the persisted thread_id. Resume never confirms."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._workflows = WorkflowRepository()

    def load_owner_action(
        self,
        *,
        action_id: UUID,
        owner_subject_id: str,
        requested_thread_id: str | None = None,
        revision: int = V4_REVISION,
    ) -> AuthoritativeObservation:
        with self._session_factory() as session:
            workflow = self._workflows.get_workflow_for_owner(
                session,
                action_id=action_id,
                owner_subject_id=owner_subject_id,
            )
            if workflow is None:
                if self._workflows.get_workflow(session, action_id) is None:
                    raise WorkflowRowNotFoundError("action workflow was not found")
                raise WorkflowOwnershipError("action is not visible to this owner")
            if (
                requested_thread_id is not None
                and requested_thread_id != workflow.langgraph_thread_id
            ):
                raise ThreadBindingError("thread_id is not bound to this action")
            row = self._workflows.get_revision(session, action_id, revision)
            if row is None:
                raise WorkflowRowNotFoundError("action revision was not found")
            return AuthoritativeObservation(
                action_id=str(workflow.action_id),
                revision=row.revision,
                langgraph_thread_id=workflow.langgraph_thread_id,
                state=row.state,
            )

    def reload_observation(self, state: WorkflowGraphState) -> AuthoritativeObservation:
        action_id = UUID(state["action_id"])
        with self._session_factory() as session:
            workflow = self._workflows.get_workflow(session, action_id)
            if workflow is None:
                raise OrchestrationAuthorityError("checkpoint cannot authorize a missing action")
            if workflow.langgraph_thread_id != state["langgraph_thread_id"]:
                raise ThreadBindingError("graph thread_id does not match stored binding")
            row = self._workflows.get_revision(session, action_id, state["revision"])
            if row is None:
                raise OrchestrationAuthorityError("checkpoint cannot authorize a missing revision")
            return AuthoritativeObservation(
                action_id=str(workflow.action_id),
                revision=row.revision,
                langgraph_thread_id=workflow.langgraph_thread_id,
                state=row.state,
            )

    def start(
        self,
        *,
        action_id: UUID,
        owner_subject_id: str,
        settings: KnowledgeSettings | None = None,
    ) -> dict[str, Any]:
        observation = self.load_owner_action(
            action_id=action_id,
            owner_subject_id=owner_subject_id,
        )
        config = _thread_config(observation.langgraph_thread_id)
        with open_postgres_checkpointer(settings) as checkpointer:
            graph = build_workflow_graph(self.reload_observation).compile(checkpointer=checkpointer)
            return graph.invoke(
                {
                    "action_id": observation.action_id,
                    "revision": observation.revision,
                    "langgraph_thread_id": observation.langgraph_thread_id,
                    "observed_state": observation.state,
                },
                config=config,
            )

    def resume(
        self,
        *,
        action_id: UUID,
        owner_subject_id: str,
        resume_payload: object = None,
        settings: KnowledgeSettings | None = None,
    ) -> dict[str, Any]:
        observation = self.load_owner_action(
            action_id=action_id,
            owner_subject_id=owner_subject_id,
        )
        return self._resume_observation(
            observation, resume_payload=resume_payload, settings=settings
        )

    def resume_internal(
        self,
        *,
        action_id: UUID,
        settings: KnowledgeSettings | None = None,
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        """Wake a persisted thread as a system actor. Does not grant employee authority."""

        observation = self._load_system_action(action_id)
        runtime = None
        if worker_id:
            runtime = WorkflowExecutionRuntime(
                self._session_factory,
                settings,
                worker_id=worker_id,
            )
        result = self._resume_observation(
            observation,
            resume_payload={"wake": True},
            settings=settings,
            execution=runtime,
        )
        if runtime is not None:
            result = self._advance_execution(action_id, runtime, result)
        return result

    def _load_system_action(self, action_id: UUID) -> AuthoritativeObservation:
        with self._session_factory() as session:
            workflow = self._workflows.get_workflow(session, action_id)
            if workflow is None:
                raise OrchestrationAuthorityError("system wake cannot guess a missing action")
            row = self._workflows.get_revision(session, action_id, V4_REVISION)
            if row is None:
                raise OrchestrationAuthorityError("system wake cannot guess a missing revision")
            return AuthoritativeObservation(
                action_id=str(workflow.action_id),
                revision=row.revision,
                langgraph_thread_id=workflow.langgraph_thread_id,
                state=row.state,
            )

    def _advance_execution(
        self,
        action_id: UUID,
        runtime: WorkflowExecutionRuntime,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        observation = self._load_system_action(action_id)
        state = observation.state
        if state == WorkflowState.CONFIRMED.value:
            outcome = runtime.reserve(observation.action_id, observation.revision)
            if outcome in {ReservationOutcome.RESERVED, ReservationOutcome.ALREADY_RESERVED}:
                runtime.execute(observation.action_id, observation.revision)
        elif state in {
            WorkflowState.EXECUTING.value,
            WorkflowState.UNKNOWN_OUTCOME.value,
            WorkflowState.RECONCILING.value,
        }:
            runtime.reconcile(observation.action_id, observation.revision)
        observation = self._load_system_action(action_id)
        updated = dict(result)
        updated["observed_state"] = observation.state
        updated["action_id"] = observation.action_id
        updated["revision"] = observation.revision
        updated["langgraph_thread_id"] = observation.langgraph_thread_id
        return updated

    def _resume_observation(
        self,
        observation: AuthoritativeObservation,
        *,
        resume_payload: object,
        settings: KnowledgeSettings | None,
        execution: WorkflowExecutionRuntime | None = None,
    ) -> dict[str, Any]:
        config = _thread_config(observation.langgraph_thread_id)
        with open_postgres_checkpointer(settings) as checkpointer:
            existing = checkpointer.get(config)
            if existing is None:
                raise OrchestrationAuthorityError("missing checkpoint is an orchestration failure")
            _reject_corrupt_checkpoint(existing, observation)
            graph = build_workflow_graph(
                self.reload_observation,
                execution=execution,
            ).compile(checkpointer=checkpointer)
            snapshot = graph.get_state(config)
            if snapshot.next == ():
                return dict(snapshot.values)
            if snapshot.next == ("await_confirmation",):
                return graph.invoke(Command(resume=_wake_payload(resume_payload)), config=config)
            return graph.invoke(None, config=config)


def _wake_payload(resume_payload: object) -> object:
    """LangGraph ignores Command(resume={}) / None; empty wakes still must resume."""

    if resume_payload is None or resume_payload == {}:
        return {"wake": True}
    return resume_payload


def _thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}


def _reject_corrupt_checkpoint(
    checkpoint: dict[str, Any],
    observation: AuthoritativeObservation,
) -> None:
    values = checkpoint.get("channel_values")
    if not isinstance(values, dict):
        raise OrchestrationAuthorityError("corrupt checkpoint is an orchestration failure")
    action_id = values.get("action_id")
    thread_id = values.get("langgraph_thread_id")
    if action_id != observation.action_id or thread_id != observation.langgraph_thread_id:
        raise OrchestrationAuthorityError("corrupt checkpoint is an orchestration failure")

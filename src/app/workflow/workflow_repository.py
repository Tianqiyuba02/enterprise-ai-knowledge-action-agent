"""Workflow and revision persistence primitives. No execution transitions."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.workflow_models import ActionRevision, ActionWorkflow
from app.workflow.domain import V4_REVISION, ActionType, WorkflowState
from app.workflow.errors import WorkflowRowNotFoundError


@dataclass(frozen=True, slots=True)
class NewWorkflowRevision:
    owner_subject_id: str
    owner_employee_id: str
    jurisdiction: str
    action_type: ActionType
    state: WorkflowState
    draft_payload: dict[str, Any]
    draft_hash: str
    authority_snapshot_hash: str
    business_request_key: str
    ruleset_version: str
    calendar_version: str
    action_expires_at: datetime
    langgraph_thread_id: str | None = None
    action_id: UUID | None = None


class WorkflowRepository:
    """Create and load revision=1 workflows with optional row locking."""

    def create_workflow_and_revision(
        self,
        session: Session,
        spec: NewWorkflowRevision,
    ) -> tuple[ActionWorkflow, ActionRevision]:
        action_id = spec.action_id or uuid4()
        workflow = ActionWorkflow(
            action_id=action_id,
            owner_subject_id=spec.owner_subject_id,
            owner_employee_id=spec.owner_employee_id,
            jurisdiction=spec.jurisdiction,
            action_type=spec.action_type.value,
            current_revision=V4_REVISION,
            langgraph_thread_id=spec.langgraph_thread_id or str(uuid4()),
        )
        revision = ActionRevision(
            revision_id=uuid4(),
            action_id=action_id,
            revision=V4_REVISION,
            state=spec.state.value,
            draft_payload=spec.draft_payload,
            draft_hash=spec.draft_hash,
            authority_snapshot_hash=spec.authority_snapshot_hash,
            business_request_key=spec.business_request_key,
            ruleset_version=spec.ruleset_version,
            calendar_version=spec.calendar_version,
            action_expires_at=spec.action_expires_at,
        )
        session.add(workflow)
        session.flush()
        session.add(revision)
        session.flush()
        return workflow, revision

    def get_workflow(self, session: Session, action_id: UUID) -> ActionWorkflow | None:
        return session.get(ActionWorkflow, action_id)

    def get_revision(
        self,
        session: Session,
        action_id: UUID,
        revision: int = V4_REVISION,
    ) -> ActionRevision | None:
        return session.execute(
            select(ActionRevision).where(
                ActionRevision.action_id == action_id,
                ActionRevision.revision == revision,
            )
        ).scalar_one_or_none()

    def get_workflow_for_owner(
        self,
        session: Session,
        *,
        action_id: UUID,
        owner_subject_id: str,
    ) -> ActionWorkflow | None:
        return session.execute(
            select(ActionWorkflow).where(
                ActionWorkflow.action_id == action_id,
                ActionWorkflow.owner_subject_id == owner_subject_id,
            )
        ).scalar_one_or_none()

    def lock_revision(
        self,
        session: Session,
        *,
        action_id: UUID,
        revision: int = V4_REVISION,
    ) -> ActionRevision:
        row = session.execute(
            select(ActionRevision)
            .where(
                ActionRevision.action_id == action_id,
                ActionRevision.revision == revision,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise WorkflowRowNotFoundError("action revision was not found for locking")
        return row

    def lock_revision_statement(
        self,
        *,
        action_id: UUID,
        revision: int = V4_REVISION,
    ):
        return (
            select(ActionRevision)
            .where(
                ActionRevision.action_id == action_id,
                ActionRevision.revision == revision,
            )
            .with_for_update()
        )

"""Workflow and revision persistence primitives. No execution transitions."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.workflow_models import ActionRevision, ActionWorkflow
from app.workflow.domain import (
    V4_REVISION,
    ActionType,
    WorkflowState,
)
from app.workflow.errors import WorkflowRowNotFoundError
from app.workflow.occupancy import FINAL_OCCUPANCY_STATES


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

    def lock_workflow(self, session: Session, action_id: UUID) -> ActionWorkflow:
        row = session.execute(
            select(ActionWorkflow).where(ActionWorkflow.action_id == action_id).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise WorkflowRowNotFoundError("action workflow was not found for locking")
        return row

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

    def apply_revision_state(
        self,
        session: Session,
        *,
        action_id: UUID,
        state: WorkflowState,
        revision: int = V4_REVISION,
    ) -> ActionRevision:
        """CAS-style state write for future confirmation/service use. Not HTTP."""

        row = self.lock_revision(session, action_id=action_id, revision=revision)
        row.state = state.value
        session.flush()
        return row

    def lock_occupying_revision_for_business_request(
        self,
        session: Session,
        business_request_key: str,
    ) -> tuple[ActionWorkflow, ActionRevision] | None:
        """Lock the occupying revision only. Do not FOR UPDATE action_workflows."""

        revision = session.execute(
            select(ActionRevision)
            .where(
                ActionRevision.business_request_key == business_request_key,
                ActionRevision.state.in_(FINAL_OCCUPANCY_STATES),
            )
            .with_for_update()
        ).scalar_one_or_none()
        if revision is None:
            return None
        workflow = session.get(ActionWorkflow, revision.action_id)
        if workflow is None:
            raise WorkflowRowNotFoundError("occupying action workflow was not found")
        return workflow, revision

    def lock_owner_revisions_for_business_request(
        self,
        session: Session,
        *,
        owner_employee_id: str,
        owner_subject_id: str,
        business_request_key: str,
    ) -> tuple[tuple[ActionWorkflow, ActionRevision], ...]:
        rows = session.execute(
            select(ActionWorkflow, ActionRevision)
            .join(ActionRevision, ActionRevision.action_id == ActionWorkflow.action_id)
            .where(
                ActionWorkflow.owner_employee_id == owner_employee_id,
                ActionWorkflow.owner_subject_id == owner_subject_id,
                ActionRevision.business_request_key == business_request_key,
                ActionRevision.revision == V4_REVISION,
            )
            .with_for_update()
            .order_by(ActionRevision.created_at.asc())
        ).all()
        return tuple((workflow, revision) for workflow, revision in rows)

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

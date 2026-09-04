"""Owner-scoped V5 read models. This module has no mutation or execution authority."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api.portal_models import (
    ActionAuditEventResponse,
    ActionDetail,
    ActionDetailResponse,
    ActionListItem,
    ActionListItemResponse,
    ActionListResponse,
    AuthoritativeAnnualLeaveDraftResponse,
    AuthoritativeITSupportTicketDraftResponse,
    ITActionDetailResponse,
    ITActionListItemResponse,
    ITTicketResultResponse,
    LeaveBalanceProjectionResponse,
    LeaveRequestResultResponse,
    LeaveSummaryResponse,
    PolicyDocumentDetailResponse,
    PolicyDocumentListResponse,
    PolicyDocumentSummaryResponse,
    PolicySectionResponse,
)
from app.db.models import Document, DocumentChunk
from app.db.workflow_models import (
    ActionAuditEvent,
    ActionRevision,
    ActionWorkflow,
    ITTicket,
    LeaveRequest,
)
from app.errors import (
    ActionNotFoundError,
    EmployeeNotFoundError,
    PolicyDocumentNotFoundError,
    PortalReadUnavailableError,
)
from app.identity import AuthenticatedEmployeeContext
from app.knowledge.context import KnowledgeApplicabilityContext
from app.repositories.demo import DemoRepository
from app.workflow.canonical import quantize_hours
from app.workflow.domain import ActionType, WorkflowState
from app.workflow.time import database_now

_EMPLOYEE_SAFE_AUDIT_METADATA_FIELDS: dict[str, frozenset[str]] = {
    "EXECUTION_FAILED": frozenset({"failure_kind"}),
    "ACTION_STALE": frozenset({"failure_kind"}),
    "ACTION_EXPIRED": frozenset({"reason"}),
    "EXECUTION_SUCCEEDED": frozenset({"ticket_id"}),
    "ACTION_REVISION_SUPERSEDED": frozenset({"superseded_by_revision"}),
    "ACTION_REVISION_CREATED": frozenset({"supersedes_revision"}),
}
_MAX_EMPLOYEE_AUDIT_METADATA_LENGTH = 64


class PortalReadService:
    """Build browser-friendly projections without changing V4 state or authority."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        demo_repository: DemoRepository,
    ) -> None:
        self._session_factory = session_factory
        self._demo = demo_repository

    def leave_summary(self, context: AuthenticatedEmployeeContext) -> LeaveSummaryResponse:
        if self._demo.get_employee(context.employee_id) is None:
            raise EmployeeNotFoundError
        seeded = self._demo.list_leave_balances(context.employee_id)
        try:
            with self._session_factory() as session:
                requests = tuple(
                    session.scalars(
                        select(LeaveRequest)
                        .where(LeaveRequest.employee_id == context.employee_id)
                        .order_by(LeaveRequest.submitted_at.desc(), LeaveRequest.leave_request_id)
                    )
                )
                committed_annual = quantize_hours(
                    Decimal(
                        session.scalar(
                            select(func.coalesce(func.sum(LeaveRequest.requested_hours), 0)).where(
                                LeaveRequest.employee_id == context.employee_id,
                                LeaveRequest.leave_type == "annual",
                                LeaveRequest.status == "submitted",
                            )
                        )
                        or 0
                    )
                )
                computed_at = database_now(session)
        except SQLAlchemyError as exc:
            raise PortalReadUnavailableError from exc

        balances: list[LeaveBalanceProjectionResponse] = []
        for balance in seeded:
            base = quantize_hours(Decimal(str(balance.balance_hours)))
            committed = committed_annual if balance.leave_type == "annual" else Decimal("0.00")
            balances.append(
                LeaveBalanceProjectionResponse(
                    leave_type=balance.leave_type,
                    base_balance_hours=base,
                    committed_hours=committed,
                    available_hours=quantize_hours(base - committed),
                    source_as_of_date=balance.as_of_date,
                )
            )
        return LeaveSummaryResponse(
            balances=tuple(balances),
            requests=tuple(_leave_result(row) for row in requests),
            computed_at=computed_at,
        )

    def list_actions(
        self,
        context: AuthenticatedEmployeeContext,
        *,
        limit: int,
    ) -> ActionListResponse:
        subject_id = _require_subject(context)
        try:
            with self._session_factory() as session:
                total = int(
                    session.scalar(
                        select(func.count())
                        .select_from(ActionWorkflow)
                        .where(
                            ActionWorkflow.owner_employee_id == context.employee_id,
                            ActionWorkflow.owner_subject_id == subject_id,
                        )
                    )
                    or 0
                )
                rows = session.execute(
                    select(ActionWorkflow, ActionRevision)
                    .join(
                        ActionRevision,
                        (ActionRevision.action_id == ActionWorkflow.action_id)
                        & (ActionRevision.revision == ActionWorkflow.current_revision),
                    )
                    .where(
                        ActionWorkflow.owner_employee_id == context.employee_id,
                        ActionWorkflow.owner_subject_id == subject_id,
                    )
                    .order_by(ActionWorkflow.created_at.desc(), ActionWorkflow.action_id)
                    .limit(limit)
                ).all()
                action_ids = [workflow.action_id for workflow, _revision in rows]
                leave_results = _leave_results_by_action(session, action_ids)
                ticket_results = _it_results_by_action(session, action_ids)
                items = tuple(
                    _action_list_item(
                        workflow,
                        revision,
                        leave_results.get(workflow.action_id),
                        ticket_results.get(workflow.action_id),
                    )
                    for workflow, revision in rows
                )
        except SQLAlchemyError as exc:
            raise PortalReadUnavailableError from exc
        return ActionListResponse(items=items, total=total)

    def action_detail(
        self,
        action_id: UUID,
        context: AuthenticatedEmployeeContext,
    ) -> ActionDetail:
        subject_id = _require_subject(context)
        try:
            with self._session_factory() as session:
                row = session.execute(
                    select(ActionWorkflow, ActionRevision)
                    .join(
                        ActionRevision,
                        (ActionRevision.action_id == ActionWorkflow.action_id)
                        & (ActionRevision.revision == ActionWorkflow.current_revision),
                    )
                    .where(
                        ActionWorkflow.action_id == action_id,
                        ActionWorkflow.owner_employee_id == context.employee_id,
                        ActionWorkflow.owner_subject_id == subject_id,
                    )
                ).one_or_none()
                if row is None:
                    raise ActionNotFoundError
                workflow, revision = row
                audits = tuple(
                    session.scalars(
                        select(ActionAuditEvent)
                        .where(ActionAuditEvent.action_id == action_id)
                        .order_by(ActionAuditEvent.created_at, ActionAuditEvent.event_id)
                    )
                )
                common = {
                    "action_id": workflow.action_id,
                    "revision": revision.revision,
                    "action_type": workflow.action_type,
                    "state": WorkflowState(revision.state),
                    "created_at": workflow.created_at,
                    "updated_at": revision.updated_at,
                    "action_expires_at": revision.action_expires_at,
                    "confirmed_at": revision.confirmed_at,
                    "confirmed_expires_at": revision.confirmed_expires_at,
                    "confirmation_required": (
                        revision.state == WorkflowState.AWAITING_CONFIRMATION.value
                    ),
                    "manual_review_required": revision.manual_review_required,
                    "audit_events": tuple(_audit_event(item) for item in audits),
                }
                if workflow.action_type == ActionType.CREATE_IT_SUPPORT_TICKET.value:
                    ticket = session.scalar(
                        select(ITTicket).where(ITTicket.source_action_id == action_id)
                    )
                    return ITActionDetailResponse(
                        **common,
                        authoritative_draft=(
                            AuthoritativeITSupportTicketDraftResponse.model_validate(
                                revision.draft_payload
                            )
                        ),
                        result=None if ticket is None else _it_result(ticket),
                    )
                result = session.scalar(
                    select(LeaveRequest).where(LeaveRequest.source_action_id == action_id)
                )
                return ActionDetailResponse(
                    **common,
                    authoritative_draft=AuthoritativeAnnualLeaveDraftResponse.model_validate(
                        revision.draft_payload
                    ),
                    result=None if result is None else _leave_result(result),
                )
        except ActionNotFoundError:
            raise
        except SQLAlchemyError as exc:
            raise PortalReadUnavailableError from exc

    def list_policy_documents(
        self,
        applicability: KnowledgeApplicabilityContext,
        *,
        trusted_today: date,
    ) -> PolicyDocumentListResponse:
        try:
            with self._session_factory() as session:
                documents = tuple(
                    session.scalars(
                        _applicable_documents_statement(applicability, trusted_today).order_by(
                            Document.title, Document.doc_code, Document.version
                        )
                    )
                )
                counts = _section_counts(session, [document.id for document in documents])
        except SQLAlchemyError as exc:
            raise PortalReadUnavailableError from exc
        items = tuple(_policy_summary(item, counts.get(item.id, 0)) for item in documents)
        return PolicyDocumentListResponse(items=items, total=len(items))

    def policy_document(
        self,
        doc_code: str,
        version: str,
        applicability: KnowledgeApplicabilityContext,
        *,
        trusted_today: date,
    ) -> PolicyDocumentDetailResponse:
        try:
            with self._session_factory() as session:
                document = session.scalar(
                    _applicable_documents_statement(applicability, trusted_today).where(
                        Document.doc_code == doc_code,
                        Document.version == version,
                    )
                )
                if document is None:
                    raise PolicyDocumentNotFoundError
                chunks = tuple(
                    session.scalars(
                        select(DocumentChunk)
                        .where(DocumentChunk.document_id == document.id)
                        .order_by(DocumentChunk.chunk_index)
                    )
                )
        except PolicyDocumentNotFoundError:
            raise
        except SQLAlchemyError as exc:
            raise PortalReadUnavailableError from exc
        return PolicyDocumentDetailResponse(
            **_policy_summary(document, len(chunks)).model_dump(),
            sections=tuple(
                PolicySectionResponse(
                    section_label=item.section_label,
                    anchor=item.anchor,
                    page=item.page,
                    content=item.content,
                )
                for item in chunks
            ),
        )


def _require_subject(context: AuthenticatedEmployeeContext) -> str:
    if not context.subject_id:
        raise ActionNotFoundError
    return context.subject_id


def _leave_result(row: LeaveRequest) -> LeaveRequestResultResponse:
    return LeaveRequestResultResponse(
        leave_request_id=row.leave_request_id,
        source_action_id=row.source_action_id,
        leave_type=row.leave_type,
        start_date=row.start_date,
        end_date=row.end_date,
        requested_hours=row.requested_hours,
        reason=row.reason,
        status=row.status,
        submitted_at=row.submitted_at,
        calendar_version=row.calendar_version,
        ruleset_version=row.ruleset_version,
    )


def _leave_results_by_action(
    session: Session,
    action_ids: list[UUID],
) -> dict[UUID, LeaveRequest]:
    if not action_ids:
        return {}
    rows = session.scalars(
        select(LeaveRequest).where(LeaveRequest.source_action_id.in_(action_ids))
    )
    return {row.source_action_id: row for row in rows}


def _it_result(row: ITTicket) -> ITTicketResultResponse:
    return ITTicketResultResponse(
        ticket_id=row.ticket_id,
        category=row.category,
        summary=row.summary,
        urgency=row.urgency,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _it_results_by_action(
    session: Session,
    action_ids: list[UUID],
) -> dict[UUID, ITTicket]:
    if not action_ids:
        return {}
    rows = session.scalars(select(ITTicket).where(ITTicket.source_action_id.in_(action_ids)))
    return {row.source_action_id: row for row in rows if row.source_action_id is not None}


def _action_list_item(
    workflow: ActionWorkflow,
    revision: ActionRevision,
    leave_result: LeaveRequest | None,
    ticket_result: ITTicket | None,
) -> ActionListItem:
    if workflow.action_type == ActionType.CREATE_IT_SUPPORT_TICKET.value:
        draft = AuthoritativeITSupportTicketDraftResponse.model_validate(revision.draft_payload)
        return ITActionListItemResponse(
            action_id=workflow.action_id,
            revision=revision.revision,
            action_type=workflow.action_type,
            state=WorkflowState(revision.state),
            category=draft.category,
            summary=draft.summary,
            urgency=draft.urgency,
            created_at=workflow.created_at,
            updated_at=revision.updated_at,
            action_expires_at=revision.action_expires_at,
            confirmed_expires_at=revision.confirmed_expires_at,
            confirmation_required=(revision.state == WorkflowState.AWAITING_CONFIRMATION.value),
            result=None if ticket_result is None else _it_result(ticket_result),
        )
    draft = AuthoritativeAnnualLeaveDraftResponse.model_validate(revision.draft_payload)
    return ActionListItemResponse(
        action_id=workflow.action_id,
        revision=revision.revision,
        action_type=workflow.action_type,
        state=WorkflowState(revision.state),
        start_date=draft.start_date,
        end_date=draft.end_date,
        requested_hours=draft.requested_hours,
        reason=draft.reason,
        created_at=workflow.created_at,
        updated_at=revision.updated_at,
        action_expires_at=revision.action_expires_at,
        confirmed_expires_at=revision.confirmed_expires_at,
        confirmation_required=(revision.state == WorkflowState.AWAITING_CONFIRMATION.value),
        result=None if leave_result is None else _leave_result(leave_result),
    )


def _audit_event(row: ActionAuditEvent) -> ActionAuditEventResponse:
    allowed_fields = _EMPLOYEE_SAFE_AUDIT_METADATA_FIELDS.get(row.event_type, frozenset())
    employee_metadata = {
        key: value
        for key in allowed_fields
        if isinstance((value := row.safe_metadata.get(key)), str)
        and 0 < len(value) <= _MAX_EMPLOYEE_AUDIT_METADATA_LENGTH
    }
    return ActionAuditEventResponse(
        event_id=row.event_id,
        event_type=row.event_type,
        revision=getattr(row, "revision", 1),
        actor_type=row.actor_type,
        from_state=row.from_state,
        to_state=row.to_state,
        safe_metadata=employee_metadata,
        created_at=row.created_at,
    )


def _applicable_documents_statement(
    applicability: KnowledgeApplicabilityContext,
    trusted_today: date,
):
    trusted_audiences = sorted(group.value for group in applicability.audience_groups)
    return select(Document).where(
        Document.status == "approved",
        Document.effective_date <= trusted_today,
        or_(Document.expiry_date.is_(None), Document.expiry_date > trusted_today),
        or_(
            Document.jurisdiction == "GLOBAL",
            Document.jurisdiction == applicability.jurisdiction.value,
        ),
        Document.audience_groups.overlap(trusted_audiences),
    )


def _section_counts(session: Session, document_ids: list[UUID]) -> dict[UUID, int]:
    if not document_ids:
        return {}
    rows = session.execute(
        select(DocumentChunk.document_id, func.count(DocumentChunk.id))
        .where(DocumentChunk.document_id.in_(document_ids))
        .group_by(DocumentChunk.document_id)
    ).all()
    return {document_id: int(count) for document_id, count in rows}


def _policy_summary(document: Document, section_count: int) -> PolicyDocumentSummaryResponse:
    return PolicyDocumentSummaryResponse(
        doc_code=document.doc_code,
        version=document.version,
        title=document.title,
        status=document.status,
        effective_date=document.effective_date,
        expiry_date=document.expiry_date,
        jurisdiction=document.jurisdiction,
        audience_groups=tuple(document.audience_groups),
        source_uri=document.source_uri,
        section_count=section_count,
    )

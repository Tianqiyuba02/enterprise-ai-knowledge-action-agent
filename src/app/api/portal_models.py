"""Strict public contracts for the M1 employee-portal read projections."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.workflow.domain import WorkflowState


class PortalAPIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Hours = Annotated[Decimal, Field(ge=Decimal("0"))]


class StableAuthorityResponse(PortalAPIModel):
    employee_id: str
    jurisdiction: str
    work_days: tuple[str, ...]
    hours_per_day: Hours
    timezone: str
    calendar_version: str
    ruleset_version: str

    @field_serializer("hours_per_day", when_used="json")
    def serialize_hours(self, value: Decimal) -> str:
        return format(value, "f")


class AuthoritativeAnnualLeaveDraftResponse(PortalAPIModel):
    action_type: Literal["submit_annual_leave"]
    leave_type: Literal["annual"]
    start_date: date
    end_date: date
    requested_hours: Hours
    projected_balance_hours: Decimal
    readiness: str
    reason: str | None
    calendar_version: str
    ruleset_version: str
    authority_snapshot_hash: str
    scheduled_work_days: Annotated[int, Field(ge=0)]
    stable_authority: StableAuthorityResponse

    @field_serializer(
        "requested_hours",
        "projected_balance_hours",
        when_used="json",
    )
    def serialize_hours(self, value: Decimal) -> str:
        return format(value, "f")


class LeaveRequestResultResponse(PortalAPIModel):
    leave_request_id: UUID
    source_action_id: UUID
    leave_type: Literal["annual"]
    start_date: date
    end_date: date
    requested_hours: Hours
    reason: str | None
    status: Literal["submitted"]
    submitted_at: datetime
    calendar_version: str
    ruleset_version: str

    @field_serializer("requested_hours", when_used="json")
    def serialize_hours(self, value: Decimal) -> str:
        return format(value, "f")


class ActionAuditEventResponse(PortalAPIModel):
    event_id: UUID
    event_type: str
    actor_type: str
    from_state: str | None
    to_state: str | None
    safe_metadata: dict[str, str]
    created_at: datetime


class ActionListItemResponse(PortalAPIModel):
    action_id: UUID
    revision: int
    action_type: Literal["submit_annual_leave"]
    state: WorkflowState
    start_date: date
    end_date: date
    requested_hours: Hours
    reason: str | None
    created_at: datetime
    updated_at: datetime
    action_expires_at: datetime
    confirmed_expires_at: datetime | None
    confirmation_required: bool
    result: LeaveRequestResultResponse | None = None

    @field_serializer("requested_hours", when_used="json")
    def serialize_hours(self, value: Decimal) -> str:
        return format(value, "f")


class ActionListResponse(PortalAPIModel):
    items: tuple[ActionListItemResponse, ...]
    total: Annotated[int, Field(ge=0)]


class ActionDetailResponse(PortalAPIModel):
    action_id: UUID
    revision: int
    action_type: Literal["submit_annual_leave"]
    state: WorkflowState
    authoritative_draft: AuthoritativeAnnualLeaveDraftResponse
    created_at: datetime
    updated_at: datetime
    action_expires_at: datetime
    confirmed_at: datetime | None
    confirmed_expires_at: datetime | None
    confirmation_required: bool
    manual_review_required: bool
    result: LeaveRequestResultResponse | None = None
    audit_events: tuple[ActionAuditEventResponse, ...]


class LeaveBalanceProjectionResponse(PortalAPIModel):
    leave_type: Literal["annual", "personal"]
    base_balance_hours: Hours
    committed_hours: Hours
    available_hours: Hours
    source_as_of_date: date

    @field_serializer(
        "base_balance_hours",
        "committed_hours",
        "available_hours",
        when_used="json",
    )
    def serialize_hours(self, value: Decimal) -> str:
        return format(value, "f")


class LeaveSummaryResponse(PortalAPIModel):
    balances: tuple[LeaveBalanceProjectionResponse, ...]
    requests: tuple[LeaveRequestResultResponse, ...]
    computed_at: datetime


class PolicyDocumentSummaryResponse(PortalAPIModel):
    doc_code: str
    version: str
    title: str
    status: Literal["approved"]
    effective_date: date
    expiry_date: date | None
    jurisdiction: str
    audience_groups: tuple[str, ...]
    source_uri: str
    section_count: Annotated[int, Field(ge=1)]


class PolicyDocumentListResponse(PortalAPIModel):
    items: tuple[PolicyDocumentSummaryResponse, ...]
    total: Annotated[int, Field(ge=0)]


class PolicySectionResponse(PortalAPIModel):
    section_label: str
    anchor: str
    page: Annotated[int | None, Field(ge=1)] = None
    content: str


class PolicyDocumentDetailResponse(PolicyDocumentSummaryResponse):
    sections: tuple[PolicySectionResponse, ...]

    @model_validator(mode="after")
    def validate_section_count(self) -> Self:
        if self.section_count != len(self.sections):
            raise ValueError("section_count must match sections")
        return self

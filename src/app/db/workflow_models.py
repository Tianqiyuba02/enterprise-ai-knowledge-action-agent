"""SQLAlchemy models for the V4 workflow and business-state foundation."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.workflow.calendar import V4_CALENDAR_JURISDICTION, V4_CALENDAR_VERSION
from app.workflow.domain import (
    FINAL_TARGET_WORKFLOW_STATES,
    V4_REVISION,
    ActionType,
    ActorType,
    ChallengeStatus,
    LeaveRequestStatus,
    LeaveType,
    sql_in_clause,
)

SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"
WORKFLOW_STATE_SQL = sql_in_clause(FINAL_TARGET_WORKFLOW_STATES)
CHALLENGE_STATUS_SQL = sql_in_clause(ChallengeStatus)
ACTION_TYPE_SQL = sql_in_clause(ActionType)
ACTOR_TYPE_SQL = sql_in_clause(ActorType)
LEAVE_STATUS_SQL = sql_in_clause(LeaveRequestStatus)
LEAVE_TYPE_SQL = sql_in_clause(LeaveType)


class PublicHoliday(Base):
    """Seeded statewide public holiday belonging to a versioned trusted calendar."""

    __tablename__ = "public_holidays"
    __table_args__ = (
        UniqueConstraint(
            "jurisdiction",
            "holiday_date",
            "calendar_version",
            name="uq_public_holidays_jurisdiction_date_version",
        ),
        CheckConstraint(
            "btrim(jurisdiction) <> ''",
            name="ck_public_holidays_jurisdiction_nonempty",
        ),
        CheckConstraint("btrim(holiday_name) <> ''", name="ck_public_holidays_name_nonempty"),
        CheckConstraint(
            "btrim(calendar_version) <> ''",
            name="ck_public_holidays_calendar_version_nonempty",
        ),
        Index("ix_public_holidays_jurisdiction_date", "jurisdiction", "holiday_date"),
        Index("ix_public_holidays_calendar_version", "calendar_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    jurisdiction: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text(f"'{V4_CALENDAR_JURISDICTION}'"),
    )
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    holiday_name: Mapped[str] = mapped_column(Text, nullable=False)
    calendar_version: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text(f"'{V4_CALENDAR_VERSION}'"),
    )


class ActionWorkflow(Base):
    """Durable owner-scoped action identity. V4 supports revision=1 only."""

    __tablename__ = "action_workflows"
    __table_args__ = (
        CheckConstraint(
            f"current_revision = {V4_REVISION}",
            name="ck_action_workflows_current_revision",
        ),
        CheckConstraint(
            f"action_type IN ({ACTION_TYPE_SQL})",
            name="ck_action_workflows_action_type",
        ),
        CheckConstraint(
            "btrim(owner_subject_id) <> ''",
            name="ck_action_workflows_owner_subject_id_nonempty",
        ),
        CheckConstraint(
            "btrim(owner_employee_id) <> ''",
            name="ck_action_workflows_owner_employee_id_nonempty",
        ),
        CheckConstraint(
            "btrim(jurisdiction) <> ''",
            name="ck_action_workflows_jurisdiction_nonempty",
        ),
        Index("ix_action_workflows_owner_subject_id", "owner_subject_id"),
        Index("ix_action_workflows_owner_employee_id", "owner_employee_id"),
    )

    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    owner_subject_id: Mapped[str] = mapped_column(Text, nullable=False)
    owner_employee_id: Mapped[str] = mapped_column(Text, nullable=False)
    jurisdiction: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    current_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text(str(V4_REVISION)),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ActionRevision(Base):
    """Single V4 revision carrying the hashed canonical draft and workflow state."""

    __tablename__ = "action_revisions"
    __table_args__ = (
        UniqueConstraint("action_id", "revision", name="uq_action_revisions_action_revision"),
        CheckConstraint(f"revision = {V4_REVISION}", name="ck_action_revisions_revision"),
        CheckConstraint(
            f"state IN ({WORKFLOW_STATE_SQL})",
            name="ck_action_revisions_state",
        ),
        CheckConstraint(
            f"draft_hash ~ '{SHA256_HEX_PATTERN}'",
            name="ck_action_revisions_draft_hash_sha256",
        ),
        CheckConstraint(
            f"authority_snapshot_hash ~ '{SHA256_HEX_PATTERN}'",
            name="ck_action_revisions_authority_snapshot_hash_sha256",
        ),
        CheckConstraint(
            "btrim(business_request_key) <> ''",
            name="ck_action_revisions_business_request_key_nonempty",
        ),
        CheckConstraint(
            "btrim(ruleset_version) <> ''",
            name="ck_action_revisions_ruleset_version_nonempty",
        ),
        CheckConstraint(
            "btrim(calendar_version) <> ''",
            name="ck_action_revisions_calendar_version_nonempty",
        ),
        ForeignKeyConstraint(
            ["action_id"],
            ["action_workflows.action_id"],
            name="fk_action_revisions_action_id",
            ondelete="RESTRICT",
        ),
        Index("ix_action_revisions_state", "state"),
        Index("ix_action_revisions_business_request_key", "business_request_key"),
        Index(
            "uq_action_revisions_final_occupying_business_request_key",
            "business_request_key",
            unique=True,
            postgresql_where=text("state IN ('AWAITING_CONFIRMATION', 'CONFIRMED', 'SUCCEEDED')"),
        ),
    )

    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    action_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text(str(V4_REVISION)),
    )
    state: Mapped[str] = mapped_column(Text, nullable=False)
    draft_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    draft_hash: Mapped[str] = mapped_column(Text, nullable=False)
    authority_snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    business_request_key: Mapped[str] = mapped_column(Text, nullable=False)
    ruleset_version: Mapped[str] = mapped_column(Text, nullable=False)
    calendar_version: Mapped[str] = mapped_column(Text, nullable=False)
    action_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    manual_review_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ConfirmationChallenge(Base):
    """Schema foundation for one live confirmation challenge per action revision."""

    __tablename__ = "confirmation_challenges"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({CHALLENGE_STATUS_SQL})",
            name="ck_confirmation_challenges_status",
        ),
        CheckConstraint(
            f"draft_hash ~ '{SHA256_HEX_PATTERN}'",
            name="ck_confirmation_challenges_draft_hash_sha256",
        ),
        CheckConstraint(
            f"token_hash ~ '{SHA256_HEX_PATTERN}'",
            name="ck_confirmation_challenges_token_hash_sha256",
        ),
        CheckConstraint(
            "btrim(owner_subject_id) <> ''",
            name="ck_confirmation_challenges_owner_subject_id_nonempty",
        ),
        CheckConstraint(
            "btrim(confirmation_session_id) <> ''",
            name="ck_confirmation_challenges_session_id_nonempty",
        ),
        ForeignKeyConstraint(
            ["action_id", "revision"],
            ["action_revisions.action_id", "action_revisions.revision"],
            name="fk_confirmation_challenges_revision",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_confirmation_challenges_one_active",
            "action_id",
            "revision",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_confirmation_challenges_action_revision", "action_id", "revision"),
        Index("ix_confirmation_challenges_status", "status"),
    )

    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    action_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    owner_subject_id: Mapped[str] = mapped_column(Text, nullable=False)
    confirmation_session_id: Mapped[str] = mapped_column(Text, nullable=False)
    draft_hash: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionAuditEvent(Base):
    """Application-owned audit fact. Repository contract is INSERT-only."""

    __tablename__ = "action_audit_events"
    __table_args__ = (
        CheckConstraint(
            f"actor_type IN ({ACTOR_TYPE_SQL})",
            name="ck_action_audit_events_actor_type",
        ),
        CheckConstraint(
            "btrim(event_type) <> ''",
            name="ck_action_audit_events_event_type_nonempty",
        ),
        ForeignKeyConstraint(
            ["action_id", "revision"],
            ["action_revisions.action_id", "action_revisions.revision"],
            name="fk_action_audit_events_revision",
            ondelete="RESTRICT",
        ),
        Index("ix_action_audit_events_action_revision", "action_id", "revision"),
        Index("ix_action_audit_events_created_at", "created_at"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    action_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_subject_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class LeaveRequest(Base):
    """V4 business-state foundation. Stage 1 exposes no product insert path."""

    __tablename__ = "leave_requests"
    __table_args__ = (
        UniqueConstraint("business_request_key", name="uq_leave_requests_business_request_key"),
        UniqueConstraint("source_action_id", name="uq_leave_requests_source_action_id"),
        CheckConstraint(f"leave_type IN ({LEAVE_TYPE_SQL})", name="ck_leave_requests_leave_type"),
        CheckConstraint(f"status IN ({LEAVE_STATUS_SQL})", name="ck_leave_requests_status"),
        CheckConstraint("requested_hours > 0", name="ck_leave_requests_requested_hours_positive"),
        CheckConstraint("end_date >= start_date", name="ck_leave_requests_date_order"),
        CheckConstraint(
            "btrim(employee_id) <> ''",
            name="ck_leave_requests_employee_id_nonempty",
        ),
        CheckConstraint(
            "btrim(business_request_key) <> ''",
            name="ck_leave_requests_business_request_key_nonempty",
        ),
        CheckConstraint(
            "btrim(calendar_version) <> ''",
            name="ck_leave_requests_calendar_version_nonempty",
        ),
        CheckConstraint(
            "btrim(ruleset_version) <> ''",
            name="ck_leave_requests_ruleset_version_nonempty",
        ),
        ForeignKeyConstraint(
            ["source_action_id", "source_action_revision"],
            ["action_revisions.action_id", "action_revisions.revision"],
            name="fk_leave_requests_source_revision",
            ondelete="RESTRICT",
        ),
        Index("ix_leave_requests_employee_dates", "employee_id", "start_date", "end_date"),
        Index("ix_leave_requests_status", "status"),
    )

    leave_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    employee_id: Mapped[str] = mapped_column(Text, nullable=False)
    leave_type: Mapped[str] = mapped_column(Text, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    requested_hours: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    business_request_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_action_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_action_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    calendar_version: Mapped[str] = mapped_column(Text, nullable=False)
    ruleset_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

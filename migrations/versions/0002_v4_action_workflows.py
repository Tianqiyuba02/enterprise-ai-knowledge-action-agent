"""Create the V4 action-workflow persistence schema.

Revision ID: 0002_v4_action_workflows
Revises: 0001_v2_knowledge
Create Date: 2026-08-27
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.workflow.calendar import (
    V4_CALENDAR_JURISDICTION,
    V4_CALENDAR_VERSION,
    VIC_2026_STATEWIDE_HOLIDAYS,
)
from app.workflow.domain import (
    V4_REVISION,
    ActionType,
    ActorType,
    ChallengeStatus,
    ExecutionLedgerStatus,
    ExecutionOperation,
    LeaveRequestStatus,
    LeaveType,
    OutboxEventType,
    WorkflowState,
    sql_in_clause,
)

revision: str = "0002_v4_action_workflows"
down_revision: str | Sequence[str] | None = "0001_v2_knowledge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"


def upgrade() -> None:
    op.create_table(
        "public_holidays",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "jurisdiction",
            sa.Text(),
            server_default=sa.text(f"'{V4_CALENDAR_JURISDICTION}'"),
            nullable=False,
        ),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("holiday_name", sa.Text(), nullable=False),
        sa.Column(
            "calendar_version",
            sa.Text(),
            server_default=sa.text(f"'{V4_CALENDAR_VERSION}'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(jurisdiction) <> ''",
            name="ck_public_holidays_jurisdiction_nonempty",
        ),
        sa.CheckConstraint("btrim(holiday_name) <> ''", name="ck_public_holidays_name_nonempty"),
        sa.CheckConstraint(
            "btrim(calendar_version) <> ''",
            name="ck_public_holidays_calendar_version_nonempty",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "jurisdiction",
            "holiday_date",
            "calendar_version",
            name="uq_public_holidays_jurisdiction_date_version",
        ),
    )
    op.create_index(
        "ix_public_holidays_jurisdiction_date",
        "public_holidays",
        ["jurisdiction", "holiday_date"],
    )
    op.create_index("ix_public_holidays_calendar_version", "public_holidays", ["calendar_version"])

    holidays = sa.table(
        "public_holidays",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("jurisdiction", sa.Text()),
        sa.column("holiday_date", sa.Date()),
        sa.column("holiday_name", sa.Text()),
        sa.column("calendar_version", sa.Text()),
    )
    op.bulk_insert(
        holidays,
        [
            {
                "id": uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"holiday:{V4_CALENDAR_JURISDICTION}:{V4_CALENDAR_VERSION}:{holiday_date.isoformat()}",
                ),
                "jurisdiction": V4_CALENDAR_JURISDICTION,
                "holiday_date": holiday_date,
                "holiday_name": holiday_name,
                "calendar_version": V4_CALENDAR_VERSION,
            }
            for holiday_date, holiday_name in VIC_2026_STATEWIDE_HOLIDAYS
        ],
    )

    op.create_table(
        "action_workflows",
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_subject_id", sa.Text(), nullable=False),
        sa.Column("owner_employee_id", sa.Text(), nullable=False),
        sa.Column("jurisdiction", sa.Text(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column(
            "current_revision",
            sa.Integer(),
            server_default=sa.text(str(V4_REVISION)),
            nullable=False,
        ),
        sa.Column("langgraph_thread_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"current_revision = {V4_REVISION}",
            name="ck_action_workflows_current_revision",
        ),
        sa.CheckConstraint(
            f"action_type IN ({sql_in_clause(ActionType)})",
            name="ck_action_workflows_action_type",
        ),
        sa.CheckConstraint(
            "btrim(owner_subject_id) <> ''",
            name="ck_action_workflows_owner_subject_id_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(owner_employee_id) <> ''",
            name="ck_action_workflows_owner_employee_id_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(jurisdiction) <> ''",
            name="ck_action_workflows_jurisdiction_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(langgraph_thread_id) <> ''",
            name="ck_action_workflows_langgraph_thread_id_nonempty",
        ),
        sa.PrimaryKeyConstraint("action_id"),
        sa.UniqueConstraint("langgraph_thread_id", name="uq_action_workflows_langgraph_thread_id"),
    )
    op.create_index(
        "ix_action_workflows_owner_subject_id",
        "action_workflows",
        ["owner_subject_id"],
    )
    op.create_index(
        "ix_action_workflows_owner_employee_id",
        "action_workflows",
        ["owner_employee_id"],
    )

    op.create_table(
        "action_revisions",
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "revision",
            sa.Integer(),
            server_default=sa.text(str(V4_REVISION)),
            nullable=False,
        ),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("draft_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("draft_hash", sa.Text(), nullable=False),
        sa.Column("authority_snapshot_hash", sa.Text(), nullable=False),
        sa.Column("business_request_key", sa.Text(), nullable=False),
        sa.Column("ruleset_version", sa.Text(), nullable=False),
        sa.Column("calendar_version", sa.Text(), nullable=False),
        sa.Column("action_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "manual_review_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(f"revision = {V4_REVISION}", name="ck_action_revisions_revision"),
        sa.CheckConstraint(
            f"state IN ({sql_in_clause(WorkflowState)})",
            name="ck_action_revisions_state",
        ),
        sa.CheckConstraint(
            f"draft_hash ~ '{SHA256_HEX_PATTERN}'",
            name="ck_action_revisions_draft_hash_sha256",
        ),
        sa.CheckConstraint(
            f"authority_snapshot_hash ~ '{SHA256_HEX_PATTERN}'",
            name="ck_action_revisions_authority_snapshot_hash_sha256",
        ),
        sa.CheckConstraint(
            "btrim(business_request_key) <> ''",
            name="ck_action_revisions_business_request_key_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(ruleset_version) <> ''",
            name="ck_action_revisions_ruleset_version_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(calendar_version) <> ''",
            name="ck_action_revisions_calendar_version_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["action_workflows.action_id"],
            name="fk_action_revisions_action_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("revision_id"),
        sa.UniqueConstraint("action_id", "revision", name="uq_action_revisions_action_revision"),
    )
    op.create_index("ix_action_revisions_state", "action_revisions", ["state"])
    op.create_index(
        "ix_action_revisions_business_request_key",
        "action_revisions",
        ["business_request_key"],
    )

    op.create_table(
        "confirmation_challenges",
        sa.Column("challenge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("owner_subject_id", sa.Text(), nullable=False),
        sa.Column("confirmation_session_id", sa.Text(), nullable=False),
        sa.Column("draft_hash", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"status IN ({sql_in_clause(ChallengeStatus)})",
            name="ck_confirmation_challenges_status",
        ),
        sa.CheckConstraint(
            f"draft_hash ~ '{SHA256_HEX_PATTERN}'",
            name="ck_confirmation_challenges_draft_hash_sha256",
        ),
        sa.CheckConstraint(
            f"token_hash ~ '{SHA256_HEX_PATTERN}'",
            name="ck_confirmation_challenges_token_hash_sha256",
        ),
        sa.CheckConstraint(
            "btrim(owner_subject_id) <> ''",
            name="ck_confirmation_challenges_owner_subject_id_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(confirmation_session_id) <> ''",
            name="ck_confirmation_challenges_session_id_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["action_id", "revision"],
            ["action_revisions.action_id", "action_revisions.revision"],
            name="fk_confirmation_challenges_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("challenge_id"),
    )
    op.create_index(
        "uq_confirmation_challenges_one_active",
        "confirmation_challenges",
        ["action_id", "revision"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_confirmation_challenges_action_revision",
        "confirmation_challenges",
        ["action_id", "revision"],
    )
    op.create_index("ix_confirmation_challenges_status", "confirmation_challenges", ["status"])

    op.create_table(
        "workflow_outbox",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_kind", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"event_type IN ({sql_in_clause(OutboxEventType)})",
            name="ck_workflow_outbox_event_type",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_workflow_outbox_attempt_count"),
        sa.CheckConstraint("btrim(event_key) <> ''", name="ck_workflow_outbox_event_key_nonempty"),
        sa.ForeignKeyConstraint(
            ["action_id", "revision"],
            ["action_revisions.action_id", "action_revisions.revision"],
            name="fk_workflow_outbox_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("event_key", name="uq_workflow_outbox_event_key"),
    )
    op.create_index(
        "ix_workflow_outbox_claimable",
        "workflow_outbox",
        ["available_at", "locked_until"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )
    op.create_index(
        "ix_workflow_outbox_action_revision",
        "workflow_outbox",
        ["action_id", "revision"],
    )

    op.create_table(
        "action_execution_ledger",
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("execution_key", sa.Text(), nullable=False),
        sa.Column("lease_owner_id", sa.Text(), nullable=True),
        sa.Column("lease_generation", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "reconciliation_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "manual_review_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_kind", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"operation IN ({sql_in_clause(ExecutionOperation)})",
            name="ck_action_execution_ledger_operation",
        ),
        sa.CheckConstraint(
            f"status IN ({sql_in_clause(ExecutionLedgerStatus)})",
            name="ck_action_execution_ledger_status",
        ),
        sa.CheckConstraint(
            "lease_generation >= 1",
            name="ck_action_execution_ledger_lease_generation",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_action_execution_ledger_attempt_count"),
        sa.CheckConstraint(
            "reconciliation_attempt_count >= 0",
            name="ck_action_execution_ledger_reconciliation_attempt_count",
        ),
        sa.CheckConstraint(
            "btrim(execution_key) <> ''",
            name="ck_action_execution_ledger_execution_key_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["action_id", "revision"],
            ["action_revisions.action_id", "action_revisions.revision"],
            name="fk_action_execution_ledger_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("execution_id"),
        sa.UniqueConstraint(
            "action_id",
            "revision",
            "operation",
            name="uq_action_execution_ledger_reservation",
        ),
        sa.UniqueConstraint("execution_key", name="uq_action_execution_ledger_execution_key"),
    )
    op.create_index("ix_action_execution_ledger_status", "action_execution_ledger", ["status"])

    op.create_table(
        "action_audit_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_subject_id", sa.Text(), nullable=True),
        sa.Column("from_state", sa.Text(), nullable=True),
        sa.Column("to_state", sa.Text(), nullable=True),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"actor_type IN ({sql_in_clause(ActorType)})",
            name="ck_action_audit_events_actor_type",
        ),
        sa.CheckConstraint(
            "btrim(event_type) <> ''",
            name="ck_action_audit_events_event_type_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["action_id", "revision"],
            ["action_revisions.action_id", "action_revisions.revision"],
            name="fk_action_audit_events_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_action_audit_events_action_revision",
        "action_audit_events",
        ["action_id", "revision"],
    )
    op.create_index("ix_action_audit_events_created_at", "action_audit_events", ["created_at"])

    op.create_table(
        "leave_requests",
        sa.Column("leave_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", sa.Text(), nullable=False),
        sa.Column("leave_type", sa.Text(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("requested_hours", sa.Numeric(10, 2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_key", sa.Text(), nullable=False),
        sa.Column("business_request_key", sa.Text(), nullable=False),
        sa.Column("source_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_action_revision", sa.Integer(), nullable=False),
        sa.Column("calendar_version", sa.Text(), nullable=False),
        sa.Column("ruleset_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"leave_type IN ({sql_in_clause(LeaveType)})",
            name="ck_leave_requests_leave_type",
        ),
        sa.CheckConstraint(
            f"status IN ({sql_in_clause(LeaveRequestStatus)})",
            name="ck_leave_requests_status",
        ),
        sa.CheckConstraint(
            "requested_hours > 0",
            name="ck_leave_requests_requested_hours_positive",
        ),
        sa.CheckConstraint("end_date >= start_date", name="ck_leave_requests_date_order"),
        sa.CheckConstraint(
            "btrim(employee_id) <> ''",
            name="ck_leave_requests_employee_id_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(execution_key) <> ''",
            name="ck_leave_requests_execution_key_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(business_request_key) <> ''",
            name="ck_leave_requests_business_request_key_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(calendar_version) <> ''",
            name="ck_leave_requests_calendar_version_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(ruleset_version) <> ''",
            name="ck_leave_requests_ruleset_version_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["source_action_id", "source_action_revision"],
            ["action_revisions.action_id", "action_revisions.revision"],
            name="fk_leave_requests_source_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("leave_request_id"),
        sa.UniqueConstraint("execution_key", name="uq_leave_requests_execution_key"),
        sa.UniqueConstraint("business_request_key", name="uq_leave_requests_business_request_key"),
    )
    op.create_index(
        "ix_leave_requests_employee_dates",
        "leave_requests",
        ["employee_id", "start_date", "end_date"],
    )
    op.create_index("ix_leave_requests_status", "leave_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_leave_requests_status", table_name="leave_requests")
    op.drop_index("ix_leave_requests_employee_dates", table_name="leave_requests")
    op.drop_table("leave_requests")
    op.drop_index("ix_action_audit_events_created_at", table_name="action_audit_events")
    op.drop_index("ix_action_audit_events_action_revision", table_name="action_audit_events")
    op.drop_table("action_audit_events")
    op.drop_index("ix_action_execution_ledger_status", table_name="action_execution_ledger")
    op.drop_table("action_execution_ledger")
    op.drop_index("ix_workflow_outbox_action_revision", table_name="workflow_outbox")
    op.drop_index("ix_workflow_outbox_claimable", table_name="workflow_outbox")
    op.drop_table("workflow_outbox")
    op.drop_index("ix_confirmation_challenges_status", table_name="confirmation_challenges")
    op.drop_index(
        "ix_confirmation_challenges_action_revision",
        table_name="confirmation_challenges",
    )
    op.drop_index("uq_confirmation_challenges_one_active", table_name="confirmation_challenges")
    op.drop_table("confirmation_challenges")
    op.drop_index("ix_action_revisions_business_request_key", table_name="action_revisions")
    op.drop_index("ix_action_revisions_state", table_name="action_revisions")
    op.drop_table("action_revisions")
    op.drop_index("ix_action_workflows_owner_employee_id", table_name="action_workflows")
    op.drop_index("ix_action_workflows_owner_subject_id", table_name="action_workflows")
    op.drop_table("action_workflows")
    op.drop_index("ix_public_holidays_calendar_version", table_name="public_holidays")
    op.drop_index("ix_public_holidays_jurisdiction_date", table_name="public_holidays")
    op.drop_table("public_holidays")

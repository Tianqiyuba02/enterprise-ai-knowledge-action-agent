"""Remove retired LangGraph, outbox, ledger, and fencing persistence.

Revision ID: 0006_v4_remove_legacy_execution
Revises: 0005_v4_execution_cutover
Create Date: 2026-09-01

Drops infrastructure that no longer has execution authority after the
simplified CONFIRMED poller cutover. Does not change the seven persisted
action states or the final occupancy / leave uniqueness constraints.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_v4_remove_legacy_execution"
down_revision: str | Sequence[str] | None = "0005_v4_execution_cutover"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("checkpoint_writes_thread_id_idx", table_name="checkpoint_writes")
    op.drop_index("checkpoint_blobs_thread_id_idx", table_name="checkpoint_blobs")
    op.drop_index("checkpoints_thread_id_idx", table_name="checkpoints")
    op.drop_table("checkpoint_writes")
    op.drop_table("checkpoint_blobs")
    op.drop_table("checkpoints")
    op.drop_table("checkpoint_migrations")

    op.drop_index("ix_workflow_outbox_action_revision", table_name="workflow_outbox")
    op.drop_index("ix_workflow_outbox_claimable", table_name="workflow_outbox")
    op.drop_table("workflow_outbox")

    op.drop_index("ix_action_execution_ledger_status", table_name="action_execution_ledger")
    op.drop_table("action_execution_ledger")

    op.drop_constraint(
        "ck_action_workflows_langgraph_thread_id_nonempty",
        "action_workflows",
        type_="check",
    )
    op.drop_constraint(
        "uq_action_workflows_langgraph_thread_id",
        "action_workflows",
        type_="unique",
    )
    op.drop_column("action_workflows", "langgraph_thread_id")

    op.drop_constraint(
        "ck_leave_requests_execution_key_nonempty",
        "leave_requests",
        type_="check",
    )
    op.drop_constraint("uq_leave_requests_execution_key", "leave_requests", type_="unique")
    op.drop_column("leave_requests", "execution_key")


def downgrade() -> None:
    op.add_column(
        "leave_requests",
        sa.Column("execution_key", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_leave_requests_execution_key_nonempty",
        "leave_requests",
        "execution_key IS NULL OR btrim(execution_key) <> ''",
    )
    op.create_unique_constraint(
        "uq_leave_requests_execution_key",
        "leave_requests",
        ["execution_key"],
    )

    op.add_column(
        "action_workflows",
        sa.Column("langgraph_thread_id", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_action_workflows_langgraph_thread_id_nonempty",
        "action_workflows",
        "langgraph_thread_id IS NULL OR btrim(langgraph_thread_id) <> ''",
    )
    op.create_unique_constraint(
        "uq_action_workflows_langgraph_thread_id",
        "action_workflows",
        ["langgraph_thread_id"],
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
            "operation IN ('submit_annual_leave')",
            name="ck_action_execution_ledger_operation",
        ),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'LEASED', 'COMPLETED', 'FAILED', 'UNKNOWN', 'RECONCILING')",
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
            "event_type IN ('confirmation_committed', 'reconcile_requested')",
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
        "checkpoint_migrations",
        sa.Column("v", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("v"),
    )
    op.create_table(
        "checkpoints",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), server_default="", nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("checkpoint", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),
    )
    op.create_table(
        "checkpoint_blobs",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), server_default="", nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("blob", postgresql.BYTEA(), nullable=True),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "channel", "version"),
    )
    op.create_table(
        "checkpoint_writes",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), server_default="", nullable=False),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("blob", postgresql.BYTEA(), nullable=False),
        sa.Column("task_path", sa.Text(), server_default="", nullable=False),
        sa.PrimaryKeyConstraint(
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "idx",
        ),
    )
    op.create_index("checkpoints_thread_id_idx", "checkpoints", ["thread_id"])
    op.create_index("checkpoint_blobs_thread_id_idx", "checkpoint_blobs", ["thread_id"])
    op.create_index("checkpoint_writes_thread_id_idx", "checkpoint_writes", ["thread_id"])
    op.bulk_insert(
        sa.table("checkpoint_migrations", sa.column("v", sa.Integer())),
        [{"v": version} for version in range(10)],
    )

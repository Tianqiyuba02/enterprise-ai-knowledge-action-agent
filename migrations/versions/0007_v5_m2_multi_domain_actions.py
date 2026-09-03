"""Add M2 multi-domain actions, immutable revisions, and durable IT tickets.

Revision ID: 0007_v5_m2_multi_domain_actions
Revises: 0006_v4_remove_legacy_execution
Create Date: 2026-09-02
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_v5_m2_multi_domain_actions"
down_revision: str | Sequence[str] | None = "0006_v4_remove_legacy_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_action_workflows_current_revision", "action_workflows", type_="check")
    op.create_check_constraint(
        "ck_action_workflows_current_revision",
        "action_workflows",
        "current_revision >= 1",
    )
    op.drop_constraint("ck_action_workflows_action_type", "action_workflows", type_="check")
    op.create_check_constraint(
        "ck_action_workflows_action_type",
        "action_workflows",
        "action_type IN ('submit_annual_leave', 'create_it_support_ticket')",
    )
    op.drop_constraint("ck_action_revisions_revision", "action_revisions", type_="check")
    op.create_check_constraint(
        "ck_action_revisions_revision",
        "action_revisions",
        "revision >= 1",
    )
    op.drop_constraint("ck_action_revisions_state", "action_revisions", type_="check")
    op.create_check_constraint(
        "ck_action_revisions_state",
        "action_revisions",
        "state IN ('AWAITING_CONFIRMATION', 'CONFIRMED', 'SUCCEEDED', "
        "'EXECUTION_FAILED', 'STALE', 'CANCELLED', 'EXPIRED', 'SUPERSEDED')",
    )

    ticket_sequence = sa.Sequence("it_ticket_number_seq", start=3001)
    op.execute(sa.schema.CreateSequence(ticket_sequence))
    op.create_table(
        "it_tickets",
        sa.Column(
            "ticket_number",
            sa.BigInteger(),
            server_default=sa.text("nextval('it_ticket_number_seq')"),
            nullable=False,
        ),
        sa.Column("ticket_id", sa.Text(), nullable=False),
        sa.Column("employee_id", sa.Text(), nullable=False),
        sa.Column("owner_subject_id", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("urgency", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source_action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_action_revision", sa.Integer(), nullable=True),
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
            "category IN ('access', 'hardware', 'software', 'network')",
            name="ck_it_tickets_category",
        ),
        sa.CheckConstraint(
            "urgency IN ('low', 'medium', 'high')",
            name="ck_it_tickets_urgency",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved')",
            name="ck_it_tickets_status",
        ),
        sa.CheckConstraint("ticket_id ~ '^TKT-[0-9]+$'", name="ck_it_tickets_ticket_id"),
        sa.CheckConstraint(
            "btrim(employee_id) <> ''",
            name="ck_it_tickets_employee_id_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(owner_subject_id) <> ''",
            name="ck_it_tickets_owner_subject_id_nonempty",
        ),
        sa.CheckConstraint("btrim(summary) <> ''", name="ck_it_tickets_summary_nonempty"),
        sa.CheckConstraint(
            "btrim(description) <> ''",
            name="ck_it_tickets_description_nonempty",
        ),
        sa.CheckConstraint(
            "(source_action_id IS NULL AND source_action_revision IS NULL) OR "
            "(source_action_id IS NOT NULL AND source_action_revision IS NOT NULL)",
            name="ck_it_tickets_source_action_pair",
        ),
        sa.ForeignKeyConstraint(
            ["source_action_id", "source_action_revision"],
            ["action_revisions.action_id", "action_revisions.revision"],
            name="fk_it_tickets_source_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("ticket_number"),
        sa.UniqueConstraint("ticket_id", name="uq_it_tickets_ticket_id"),
        sa.UniqueConstraint("source_action_id", name="uq_it_tickets_source_action_id"),
    )
    op.create_index(
        "ix_it_tickets_owner",
        "it_tickets",
        ["owner_subject_id", "employee_id", "created_at"],
    )
    op.create_index("ix_it_tickets_status", "it_tickets", ["status"])

    tickets = sa.table(
        "it_tickets",
        sa.column("ticket_number", sa.BigInteger()),
        sa.column("ticket_id", sa.Text()),
        sa.column("employee_id", sa.Text()),
        sa.column("owner_subject_id", sa.Text()),
        sa.column("category", sa.Text()),
        sa.column("summary", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("urgency", sa.Text()),
        sa.column("status", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        tickets,
        [
            {
                "ticket_number": 1001,
                "ticket_id": "TKT-1001",
                "employee_id": "EMP-1001",
                "owner_subject_id": "subj_9f2c4e81a6b047d3",
                "category": "access",
                "summary": "Payroll portal access",
                "description": "Unable to sign in to the synthetic payroll portal.",
                "urgency": "medium",
                "status": "open",
                "created_at": datetime.fromisoformat("2026-08-20T09:30:00+10:00"),
                "updated_at": datetime.fromisoformat("2026-08-20T11:15:00+10:00"),
            },
            {
                "ticket_number": 1002,
                "ticket_id": "TKT-1002",
                "employee_id": "EMP-1001",
                "owner_subject_id": "subj_9f2c4e81a6b047d3",
                "category": "hardware",
                "summary": "External monitor flicker",
                "description": "Synthetic workstation monitor flickers after waking.",
                "urgency": "low",
                "status": "resolved",
                "created_at": datetime.fromisoformat("2026-08-10T14:00:00+10:00"),
                "updated_at": datetime.fromisoformat("2026-08-12T16:40:00+10:00"),
            },
            {
                "ticket_number": 2001,
                "ticket_id": "TKT-2001",
                "employee_id": "EMP-1002",
                "owner_subject_id": "subj_1a8e5c03d7f249b6",
                "category": "software",
                "summary": "Video meeting update",
                "description": "Synthetic meeting application requires an update.",
                "urgency": "low",
                "status": "in_progress",
                "created_at": datetime.fromisoformat("2026-08-22T10:10:00+10:00"),
                "updated_at": datetime.fromisoformat("2026-08-23T08:45:00+10:00"),
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_it_tickets_status", table_name="it_tickets")
    op.drop_index("ix_it_tickets_owner", table_name="it_tickets")
    op.drop_table("it_tickets")
    op.execute(sa.schema.DropSequence(sa.Sequence("it_ticket_number_seq")))

    op.drop_constraint("ck_action_revisions_state", "action_revisions", type_="check")
    op.create_check_constraint(
        "ck_action_revisions_state",
        "action_revisions",
        "state IN ('AWAITING_CONFIRMATION', 'CONFIRMED', 'SUCCEEDED', "
        "'EXECUTION_FAILED', 'STALE', 'CANCELLED', 'EXPIRED')",
    )
    op.drop_constraint("ck_action_revisions_revision", "action_revisions", type_="check")
    op.create_check_constraint("ck_action_revisions_revision", "action_revisions", "revision = 1")
    op.drop_constraint("ck_action_workflows_action_type", "action_workflows", type_="check")
    op.create_check_constraint(
        "ck_action_workflows_action_type",
        "action_workflows",
        "action_type IN ('submit_annual_leave')",
    )
    op.drop_constraint("ck_action_workflows_current_revision", "action_workflows", type_="check")
    op.create_check_constraint(
        "ck_action_workflows_current_revision",
        "action_workflows",
        "current_revision = 1",
    )

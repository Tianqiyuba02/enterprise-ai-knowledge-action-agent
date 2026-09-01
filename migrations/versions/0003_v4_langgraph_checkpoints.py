"""Create the LangGraph checkpoint schema required by PostgresSaver 3.1.2.

Revision ID: 0003_v4_langgraph_checkpoints
Revises: 0002_v4_action_workflows
Create Date: 2026-08-27

PostgresSaver 3.1.2 has no schema argument and emits unqualified table names.
A dedicated PostgreSQL schema would require unsupported monkeypatching or an
implicit search_path that the saver does not set. Checkpoint tables therefore
live in public and match the pinned package MIGRATIONS list exactly.

CREATE INDEX CONCURRENTLY is replaced with transactional CREATE INDEX because
Alembic runs inside a transaction. Index names and columns are unchanged.

checkpoint_migrations versions 0-9 are seeded so an accidental setup() call
is a no-op against this already-applied schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_v4_langgraph_checkpoints"
down_revision: str | Sequence[str] | None = "0002_v4_action_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKPOINT_MIGRATION_VERSIONS = range(10)


def upgrade() -> None:
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
        [{"v": version} for version in CHECKPOINT_MIGRATION_VERSIONS],
    )


def downgrade() -> None:
    op.drop_index("checkpoint_writes_thread_id_idx", table_name="checkpoint_writes")
    op.drop_index("checkpoint_blobs_thread_id_idx", table_name="checkpoint_blobs")
    op.drop_index("checkpoints_thread_id_idx", table_name="checkpoints")
    op.drop_table("checkpoint_writes")
    op.drop_table("checkpoint_blobs")
    op.drop_table("checkpoints")
    op.drop_table("checkpoint_migrations")

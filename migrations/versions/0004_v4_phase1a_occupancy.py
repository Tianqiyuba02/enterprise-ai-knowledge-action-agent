"""Add Phase 1A transitional occupancy and source_action_id uniqueness.

Revision ID: 0004_v4_phase1a_occupancy
Revises: 0003_v4_langgraph_checkpoints
Create Date: 2026-08-31

Additive safety only. Does not drop LangGraph, outbox, ledger, legacy states,
execution_key, or langgraph_thread_id. Does not create the final three-state
occupancy index.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.workflow.occupancy import (
    OCCUPANCY_UNIQUE_INDEX,
    SOURCE_ACTION_UNIQUE_CONSTRAINT,
    assert_phase1a_invariants,
    occupancy_where_sql,
)

revision: str = "0004_v4_phase1a_occupancy"
down_revision: str | Sequence[str] | None = "0003_v4_langgraph_checkpoints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    assert_phase1a_invariants(op.get_bind())
    op.create_unique_constraint(
        SOURCE_ACTION_UNIQUE_CONSTRAINT,
        "leave_requests",
        ["source_action_id"],
    )
    op.create_index(
        OCCUPANCY_UNIQUE_INDEX,
        "action_revisions",
        ["business_request_key"],
        unique=True,
        postgresql_where=sa.text(occupancy_where_sql()),
    )


def downgrade() -> None:
    op.drop_index(OCCUPANCY_UNIQUE_INDEX, table_name="action_revisions")
    op.drop_constraint(SOURCE_ACTION_UNIQUE_CONSTRAINT, "leave_requests", type_="unique")

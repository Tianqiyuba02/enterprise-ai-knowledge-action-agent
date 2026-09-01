"""Cut over occupancy and CHECK to the simplified seven-state execution model.

Revision ID: 0005_v4_execution_cutover
Revises: 0004_v4_phase1a_occupancy
Create Date: 2026-08-31

Maintenance-mode only. Do not attempt an online old-binary/new-binary hot
cutover. The supported procedure is: stop every old application/worker
process, prevent automatic restart, confirm no old execution transaction
remains, enter a no-write window, run this preflight + migration, then start
ONLY the new binary. Process quiescence is a deployment precondition; this
revision does not treat pg_stat_activity SQL-text matching as proof.

Does not drop LangGraph, checkpoint, outbox, ledger, lease, or fencing tables.
Does not drop execution_key or langgraph_thread_id columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.workflow.cutover import run_execution_cutover_preflight
from app.workflow.occupancy import (
    FINAL_OCCUPANCY_UNIQUE_INDEX,
    OCCUPANCY_UNIQUE_INDEX,
    final_occupancy_where_sql,
)

revision: str = "0005_v4_execution_cutover"
down_revision: str | Sequence[str] | None = "0004_v4_phase1a_occupancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FINAL_STATE_SQL = (
    "state IN ('AWAITING_CONFIRMATION', 'CONFIRMED', 'SUCCEEDED', "
    "'EXECUTION_FAILED', 'STALE', 'CANCELLED', 'EXPIRED')"
)


def upgrade() -> None:
    connection = op.get_bind()
    run_execution_cutover_preflight(connection)
    op.drop_index(OCCUPANCY_UNIQUE_INDEX, table_name="action_revisions")
    op.create_index(
        FINAL_OCCUPANCY_UNIQUE_INDEX,
        "action_revisions",
        ["business_request_key"],
        unique=True,
        postgresql_where=sa.text(final_occupancy_where_sql()),
    )
    op.drop_constraint("ck_action_revisions_state", "action_revisions", type_="check")
    op.create_check_constraint(
        "ck_action_revisions_state",
        "action_revisions",
        FINAL_STATE_SQL,
    )
    op.alter_column(
        "action_workflows",
        "langgraph_thread_id",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.drop_constraint(
        "ck_action_workflows_langgraph_thread_id_nonempty",
        "action_workflows",
        type_="check",
    )
    op.create_check_constraint(
        "ck_action_workflows_langgraph_thread_id_nonempty",
        "action_workflows",
        "langgraph_thread_id IS NULL OR btrim(langgraph_thread_id) <> ''",
    )
    op.alter_column("leave_requests", "execution_key", existing_type=sa.Text(), nullable=True)
    op.drop_constraint("ck_leave_requests_execution_key_nonempty", "leave_requests", type_="check")
    op.create_check_constraint(
        "ck_leave_requests_execution_key_nonempty",
        "leave_requests",
        "execution_key IS NULL OR btrim(execution_key) <> ''",
    )


def downgrade() -> None:
    op.drop_constraint("ck_leave_requests_execution_key_nonempty", "leave_requests", type_="check")
    op.create_check_constraint(
        "ck_leave_requests_execution_key_nonempty",
        "leave_requests",
        "btrim(execution_key) <> ''",
    )
    op.alter_column("leave_requests", "execution_key", existing_type=sa.Text(), nullable=False)
    op.drop_constraint(
        "ck_action_workflows_langgraph_thread_id_nonempty",
        "action_workflows",
        type_="check",
    )
    op.create_check_constraint(
        "ck_action_workflows_langgraph_thread_id_nonempty",
        "action_workflows",
        "btrim(langgraph_thread_id) <> ''",
    )
    op.alter_column(
        "action_workflows",
        "langgraph_thread_id",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.drop_constraint("ck_action_revisions_state", "action_revisions", type_="check")
    op.create_check_constraint(
        "ck_action_revisions_state",
        "action_revisions",
        "state IN ('AWAITING_CONFIRMATION', 'CONFIRMED', 'EXECUTING', 'UNKNOWN_OUTCOME', "
        "'RECONCILING', 'SUCCEEDED', 'EXECUTION_FAILED', 'CANCELLED', 'EXPIRED', 'STALE')",
    )
    op.drop_index(FINAL_OCCUPANCY_UNIQUE_INDEX, table_name="action_revisions")
    op.create_index(
        OCCUPANCY_UNIQUE_INDEX,
        "action_revisions",
        ["business_request_key"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('AWAITING_CONFIRMATION', 'CONFIRMED', 'EXECUTING', "
            "'UNKNOWN_OUTCOME', 'RECONCILING', 'SUCCEEDED')"
        ),
    )

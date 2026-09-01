from datetime import UTC, datetime

from sqlalchemy import DateTime, Numeric, inspect
from sqlalchemy.dialects.postgresql import JSONB

from app.db import Base
from app.db.workflow_models import (
    ActionAuditEvent,
    ActionRevision,
    ActionWorkflow,
    ConfirmationChallenge,
    LeaveRequest,
    PublicHoliday,
)
from app.workflow.domain import (
    ALL_WORKFLOW_STATES,
    FINAL_TARGET_WORKFLOW_STATES,
    NON_TERMINAL_WORKFLOW_STATES,
    TERMINAL_WORKFLOW_STATES,
    V4_REVISION,
    ChallengeStatus,
    WorkflowState,
)

V4_TABLES = {
    "public_holidays",
    "action_workflows",
    "action_revisions",
    "confirmation_challenges",
    "action_audit_events",
    "leave_requests",
}
REMOVED_TABLES = {
    "workflow_outbox",
    "action_execution_ledger",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
    "action_thread_map",
    "reconciliation_probes",
}
PLAINTEXT_TOKEN_COLUMNS = {"token", "plaintext_token", "confirmation_token"}


def _constraint_sql(table, name: str) -> str:
    constraint = next(item for item in table.constraints if item.name == name)
    return str(constraint.sqltext)


def test_v4_tables_are_registered_and_obsolete_tables_are_absent() -> None:
    table_names = set(Base.metadata.tables)
    assert table_names >= V4_TABLES
    assert REMOVED_TABLES.isdisjoint(table_names)
    assert "documents" in table_names
    assert "document_chunks" in table_names


def test_workflow_state_machine_is_the_final_seven_states() -> None:
    assert {state.value for state in WorkflowState} == {
        "AWAITING_CONFIRMATION",
        "CONFIRMED",
        "SUCCEEDED",
        "EXECUTION_FAILED",
        "CANCELLED",
        "EXPIRED",
        "STALE",
    }
    assert {state.value for state in FINAL_TARGET_WORKFLOW_STATES} == {
        state.value for state in WorkflowState
    }
    assert "EXECUTING" not in {state.value for state in WorkflowState}
    assert "UNKNOWN_OUTCOME" not in {state.value for state in WorkflowState}
    assert "RECONCILING" not in {state.value for state in WorkflowState}
    assert not hasattr(WorkflowState, "EXECUTING")
    assert not hasattr(WorkflowState, "UNKNOWN_OUTCOME")
    assert not hasattr(WorkflowState, "RECONCILING")
    assert NON_TERMINAL_WORKFLOW_STATES.isdisjoint(TERMINAL_WORKFLOW_STATES)
    assert ALL_WORKFLOW_STATES == NON_TERMINAL_WORKFLOW_STATES | TERMINAL_WORKFLOW_STATES
    assert {status.value for status in ChallengeStatus} == {
        "ACTIVE",
        "CONSUMED",
        "SUPERSEDED",
        "EXPIRED",
    }


def test_revision_one_is_enforced_on_workflow_and_revision_tables() -> None:
    assert V4_REVISION == 1
    workflow_sql = _constraint_sql(ActionWorkflow.__table__, "ck_action_workflows_current_revision")
    revision_sql = _constraint_sql(ActionRevision.__table__, "ck_action_revisions_revision")
    assert "current_revision = 1" in workflow_sql
    assert "revision = 1" in revision_sql


def test_revision_state_constraint_covers_final_target_states_only() -> None:
    state_sql = _constraint_sql(ActionRevision.__table__, "ck_action_revisions_state")
    for state in FINAL_TARGET_WORKFLOW_STATES:
        assert f"'{state.value}'" in state_sql
    for state in ("EXECUTING", "UNKNOWN_OUTCOME", "RECONCILING"):
        assert f"'{state}'" not in state_sql


def test_action_workflows_have_no_langgraph_thread_id() -> None:
    columns = set(ActionWorkflow.__table__.c.keys())
    constraint_names = {constraint.name for constraint in ActionWorkflow.__table__.constraints}
    assert "langgraph_thread_id" not in columns
    assert "uq_action_workflows_langgraph_thread_id" not in constraint_names
    assert ActionWorkflow.__table__.c.created_at.type.timezone is True


def test_action_revisions_use_jsonb_and_timezone_aware_timestamps() -> None:
    table = ActionRevision.__table__
    constraint_names = {constraint.name for constraint in table.constraints}
    assert isinstance(table.c.draft_payload.type, JSONB)
    assert table.c.action_expires_at.type.timezone is True
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True
    assert "uq_action_revisions_action_revision" in constraint_names
    assert "fk_action_revisions_action_id" in constraint_names


def test_confirmation_challenges_have_no_plaintext_token_column() -> None:
    columns = set(ConfirmationChallenge.__table__.c.keys())
    assert "token_hash" in columns
    assert PLAINTEXT_TOKEN_COLUMNS.isdisjoint(columns)
    assert "challenge_id" in columns
    assert "superseded_at" in columns


def test_active_challenge_uniqueness_uses_partial_unique_index() -> None:
    indexes = {index.name: index for index in ConfirmationChallenge.__table__.indexes}
    active = indexes["uq_confirmation_challenges_one_active"]
    assert active.unique is True
    assert [column.name for column in active.columns] == ["action_id", "revision"]
    assert str(active.dialect_options["postgresql"]["where"]) == "status = 'ACTIVE'"


def test_leave_request_key_uniqueness_and_decimal_hours() -> None:
    table = LeaveRequest.__table__
    columns = set(table.c.keys())
    constraint_names = {constraint.name for constraint in table.constraints}
    assert "execution_key" not in columns
    assert "uq_leave_requests_execution_key" not in constraint_names
    assert "uq_leave_requests_business_request_key" in constraint_names
    assert "uq_leave_requests_source_action_id" in constraint_names
    hours_type = table.c.requested_hours.type
    assert isinstance(hours_type, Numeric)
    assert hours_type.precision == 10
    assert hours_type.scale == 2
    assert not isinstance(hours_type, DateTime)


def test_audit_events_do_not_declare_update_or_delete_hooks() -> None:
    mapper = inspect(ActionAuditEvent)
    assert mapper.primary_key[0].name == "event_id"
    assert "safe_metadata" in ActionAuditEvent.__table__.c
    assert ActionAuditEvent.__table__.c.created_at.type.timezone is True


def test_public_holidays_and_audit_have_required_columns() -> None:
    holiday_columns = set(PublicHoliday.__table__.c.keys())
    assert {"jurisdiction", "holiday_date", "holiday_name", "calendar_version"} <= holiday_columns
    now = datetime.now(UTC)
    assert now.tzinfo is not None

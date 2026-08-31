from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from app.workflow.audit_repository import AuditRepository
from app.workflow.challenge_repository import ChallengeRepository
from app.workflow.execution_repository import ExecutionLedgerRepository
from app.workflow.leave_command_repository import LeaveCommandRepository
from app.workflow.leave_query_repository import LeaveQueryRepository
from app.workflow.outbox_repository import OutboxRepository
from app.workflow.workflow_repository import WorkflowRepository


def test_audit_repository_is_insert_only() -> None:
    methods = {name for name in dir(AuditRepository) if not name.startswith("_")}
    assert "insert" in methods
    assert {"update", "delete", "remove"}.isdisjoint(methods)


def test_leave_query_repository_has_no_submission_path() -> None:
    methods = {name for name in dir(LeaveQueryRepository) if not name.startswith("_")}
    assert {
        "find_by_execution_key",
        "find_by_source_action_id",
        "find_by_business_request_key",
        "sum_active_submitted_hours",
        "overlapping_active_annual_leave",
    } <= methods
    assert {"insert", "create", "submit", "add", "save", "persist"}.isdisjoint(methods)
    assert "persist" in {name for name in dir(LeaveCommandRepository) if not name.startswith("_")}


def test_challenge_repository_has_no_token_issuance() -> None:
    methods = {name for name in dir(ChallengeRepository) if not name.startswith("_")}
    assert "persist" in methods
    assert "get_active_challenge" in methods
    assert {"issue", "issue_token", "consume", "confirm"}.isdisjoint(methods)


def test_outbox_claim_statement_is_skip_locked() -> None:
    compiled = str(
        OutboxRepository()
        .claimable_statement(now=datetime.now(UTC))
        .compile(dialect=postgresql.dialect())
    ).upper()
    assert "FOR UPDATE" in compiled
    assert "SKIP LOCKED" in compiled


def test_execution_and_revision_lock_statements_use_for_update() -> None:
    from uuid import uuid4

    action_id = uuid4()
    ledger_sql = str(
        ExecutionLedgerRepository()
        .lock_reservation_statement(action_id=action_id)
        .compile(dialect=postgresql.dialect())
    ).upper()
    revision_sql = str(
        WorkflowRepository()
        .lock_revision_statement(action_id=action_id)
        .compile(dialect=postgresql.dialect())
    ).upper()
    assert "FOR UPDATE" in ledger_sql
    assert "FOR UPDATE" in revision_sql
    assert "SKIP LOCKED" not in ledger_sql

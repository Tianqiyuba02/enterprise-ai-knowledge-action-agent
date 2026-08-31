import os
import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.workflow.confirmation import ConfirmationService
from app.workflow.domain import ActionType, OutboxEventType, WorkflowState
from app.workflow.executable_preparation import V4ExecutablePreparationService
from app.workflow.executor import BusinessOutcome, ExecutorResult, LeaveSubmissionExecutor
from app.workflow.orchestration import WorkflowOrchestrationService
from app.workflow.time import database_now
from app.workflow.worker import WorkflowWorker
from app.workflow.workflow_repository import NewWorkflowRevision, WorkflowRepository

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.skip(
        reason="retired after simplified execution cutover; see test_v4_atomic_execution.py"
    ),
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]

ALEX = DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"]
WORKER_A = "wake-owner-a"
WORKER_B = "wake-foreign-b"


def _replace_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[KnowledgeSettings]:
    live = load_knowledge_settings()
    admin_url = live.database_url.get_secret_value()
    database_name = f"knowledge_agent_v4_rw_{uuid.uuid4().hex[:12]}"
    isolated_url = _replace_database(admin_url, database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        monkeypatch.setenv("APP_DATABASE_URL", isolated_url)
        command.upgrade(AlembicConfig("alembic.ini"), "head")
        yield load_knowledge_settings()
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database_name AND pid <> pg_backend_pid()
                    """
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


@pytest.fixture
def engine(isolated_settings: KnowledgeSettings) -> Iterator[Engine]:
    engine = create_knowledge_engine(isolated_settings)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_knowledge_session_factory(engine)


def _create_action(session: Session, start: date) -> uuid.UUID:
    prepared = V4ExecutablePreparationService().prepare(
        session,
        context=ALEX,
        start_date=start,
        end_date=start,
        reason="Family visit",
    )
    now = database_now(session)
    workflow, _revision = WorkflowRepository().create_workflow_and_revision(
        session,
        NewWorkflowRevision(
            owner_subject_id=ALEX.subject_id or "",
            owner_employee_id=ALEX.employee_id,
            jurisdiction=ALEX.jurisdiction or "AU-VIC",
            action_type=ActionType.SUBMIT_ANNUAL_LEAVE,
            state=WorkflowState.AWAITING_CONFIRMATION,
            draft_payload=prepared.payload(),
            draft_hash=prepared.draft.fingerprint(),
            authority_snapshot_hash=prepared.snapshot.fingerprint(),
            business_request_key=prepared.business_request_key,
            ruleset_version=prepared.draft.ruleset_version,
            calendar_version=prepared.draft.calendar_version,
            action_expires_at=now + timedelta(hours=1),
        ),
    )
    return workflow.action_id


def _confirm_ready(
    *,
    session_factory: sessionmaker[Session],
    settings: KnowledgeSettings,
    start: date,
) -> uuid.UUID:
    with session_factory() as session:
        action_id = _create_action(session, start)
        session.commit()
    WorkflowOrchestrationService(session_factory).start(
        action_id=action_id,
        owner_subject_id=ALEX.subject_id or "",
        settings=settings,
    )
    confirmation = ConfirmationService(session_factory, settings)
    issued = confirmation.issue_challenge(action_id=action_id, context=ALEX)
    confirmation.confirm(
        action_id=action_id,
        challenge_id=issued.challenge_id,
        confirmation_token=issued.confirmation_token,
        context=ALEX,
    )
    return action_id


def _make_undelivered_claimable(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE workflow_outbox
                SET available_at = clock_timestamp() - interval '1 second',
                    locked_until = NULL,
                    locked_by = NULL
                WHERE delivered_at IS NULL
                """
            )
        )
        session.commit()


def _recon_outbox(session_factory: sessionmaker[Session]):
    with session_factory() as session:
        return session.execute(
            text(
                """
                SELECT event_id, delivered_at, last_failure_kind, attempt_count
                FROM workflow_outbox
                WHERE event_type = :event_type
                """
            ),
            {"event_type": OutboxEventType.RECONCILE_REQUESTED.value},
        ).one()


def _ledger_recon(session_factory: sessionmaker[Session], action_id):
    with session_factory() as session:
        return session.execute(
            text(
                """
                SELECT reconciliation_attempt_count, manual_review_required, execution_key
                FROM action_execution_ledger
                WHERE action_id = :action_id
                """
            ),
            {"action_id": action_id},
        ).one()


def test_foreign_worker_reconcile_claim_does_not_strand_unknown(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = LeaveSubmissionExecutor.submit

    def unknown_after_apply(self, permit):
        result = original(self, permit)
        if result.outcome is BusinessOutcome.APPLIED:
            return ExecutorResult(
                BusinessOutcome.OUTCOME_UNKNOWN,
                leave_request_id=result.leave_request_id,
                execution_key=result.execution_key,
                business_request_key=result.business_request_key,
                resolution=result.resolution,
            )
        return result

    monkeypatch.setattr(LeaveSubmissionExecutor, "submit", unknown_after_apply)
    action_id = _confirm_ready(
        session_factory=session_factory,
        settings=isolated_settings,
        start=date(2026, 10, 21),
    )
    owner = WorkflowWorker(session_factory, isolated_settings, worker_id=WORKER_A)
    first = owner.run_once()
    assert first is not None
    assert first.observed_state == WorkflowState.UNKNOWN_OUTCOME.value
    assert first.delivered is True
    with engine.connect() as connection:
        leave_count = connection.execute(text("SELECT count(*) FROM leave_requests")).scalar_one()
    assert leave_count == 1
    before = _ledger_recon(session_factory, action_id)
    assert before.reconciliation_attempt_count == 1
    assert before.manual_review_required is False
    original_key = before.execution_key

    _make_undelivered_claimable(session_factory)
    foreign = WorkflowWorker(session_factory, isolated_settings, worker_id=WORKER_B)
    claimed = foreign.claim_one()
    assert claimed is not None
    assert claimed.event_type == OutboxEventType.RECONCILE_REQUESTED.value
    blocked = foreign.deliver(claimed, mark_delivered=True)
    assert blocked.observed_state == WorkflowState.UNKNOWN_OUTCOME.value
    assert blocked.delivered is False
    after_block = _ledger_recon(session_factory, action_id)
    assert after_block.reconciliation_attempt_count == before.reconciliation_attempt_count
    assert after_block.manual_review_required is False
    assert after_block.execution_key == original_key
    outbox = _recon_outbox(session_factory)
    assert outbox.delivered_at is None
    assert outbox.last_failure_kind == "pending_reconciliation"
    assert outbox.attempt_count >= 1
    with session_factory() as session:
        lease_owner = session.execute(
            text("SELECT lease_owner_id FROM action_execution_ledger WHERE action_id = :action_id"),
            {"action_id": action_id},
        ).scalar_one()
    assert lease_owner == WORKER_A
    with session_factory() as session:
        state = WorkflowRepository().get_revision(session, action_id).state
    assert state == WorkflowState.UNKNOWN_OUTCOME.value

    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_execution_ledger
                SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE action_id = :action_id
                """
            ),
            {"action_id": action_id},
        )
        session.commit()
    _make_undelivered_claimable(session_factory)
    reclaimed = foreign.claim_one()
    assert reclaimed is not None
    assert reclaimed.event_id == claimed.event_id
    settled = foreign.deliver(reclaimed, mark_delivered=True)
    assert settled.observed_state == WorkflowState.SUCCEEDED.value
    assert settled.delivered is True
    final = _ledger_recon(session_factory, action_id)
    assert final.execution_key == original_key
    assert _recon_outbox(session_factory).delivered_at is not None
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM leave_requests")).scalar_one() == 1
        assert (
            connection.execute(
                text("SELECT state FROM action_revisions WHERE action_id = :action_id"),
                {"action_id": action_id},
            ).scalar_one()
            == WorkflowState.SUCCEEDED.value
        )

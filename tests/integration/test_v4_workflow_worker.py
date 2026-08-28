import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from langgraph.checkpoint.base import empty_checkpoint
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.db.workflow_models import WorkflowOutbox
from app.workflow.checkpointing import open_postgres_checkpointer
from app.workflow.confirmation import ConfirmationService
from app.workflow.domain import ActionType, OutboxEventType, WorkflowState
from app.workflow.errors import OrchestrationAuthorityError, WorkflowInvariantError
from app.workflow.executable_preparation import V4ExecutablePreparationService
from app.workflow.orchestration import WorkflowOrchestrationService
from app.workflow.outbox_repository import NewOutboxEvent, OutboxRepository
from app.workflow.worker import WorkflowWorker
from app.workflow.workflow_repository import NewWorkflowRevision, WorkflowRepository

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]

ALEX = DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"]


def _replace_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[KnowledgeSettings]:
    live = load_knowledge_settings()
    admin_url = live.database_url.get_secret_value()
    database_name = f"knowledge_agent_v4_wk_{uuid.uuid4().hex[:12]}"
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
    workflow, _revision = WorkflowRepository().create_workflow_and_revision(
        session,
        NewWorkflowRevision(
            owner_subject_id=ALEX.subject_id or "",
            owner_employee_id="EMP-1001",
            jurisdiction="AU-VIC",
            action_type=ActionType.SUBMIT_ANNUAL_LEAVE,
            state=WorkflowState.AWAITING_CONFIRMATION,
            draft_payload=prepared.payload(),
            draft_hash=prepared.draft.fingerprint(),
            authority_snapshot_hash=prepared.snapshot.fingerprint(),
            business_request_key=prepared.business_request_key,
            ruleset_version=prepared.draft.ruleset_version,
            calendar_version=prepared.draft.calendar_version,
            action_expires_at=datetime.now(UTC) + timedelta(hours=1),
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


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def test_worker_delivers_confirmed_barrier_and_duplicate_is_harmless(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _confirm_ready(
        session_factory=session_factory,
        settings=isolated_settings,
        start=date(2026, 10, 1),
    )
    with session_factory() as session:
        thread_id = WorkflowRepository().get_workflow(session, action_id).langgraph_thread_id
    worker = WorkflowWorker(session_factory, isolated_settings, worker_id="worker-a")
    first = worker.run_once()
    second = worker.run_once()
    assert first is not None
    assert first.observed_state == WorkflowState.SUCCEEDED.value
    assert first.delivered is True
    assert second is None
    with session_factory() as session:
        outbox = session.execute(text("SELECT delivered_at, event_key FROM workflow_outbox")).one()
        state = WorkflowRepository().get_revision(session, action_id).state
    assert outbox.delivered_at is not None
    assert state == WorkflowState.SUCCEEDED.value
    with open_postgres_checkpointer(isolated_settings) as checkpointer:
        loaded = checkpointer.get({"configurable": {"thread_id": thread_id}})
    assert loaded is not None
    assert _count(engine, "leave_requests") == 1
    assert _count(engine, "action_execution_ledger") == 1


def test_crash_after_resume_before_mark_can_be_reclaimed(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    action_id = _confirm_ready(
        session_factory=session_factory,
        settings=isolated_settings,
        start=date(2026, 10, 2),
    )
    first = WorkflowWorker(session_factory, isolated_settings, worker_id="worker-crash")
    claimed = first.claim_one()
    assert claimed is not None
    crashed = first.deliver(claimed, mark_delivered=False)
    assert crashed.delivered is False
    with session_factory() as session:
        row = session.get(WorkflowOutbox, claimed.event_id)
        assert row is not None
        row.locked_until = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
        state_after_crash = WorkflowRepository().get_revision(session, action_id).state
    assert state_after_crash == WorkflowState.SUCCEEDED.value
    recovered = WorkflowWorker(session_factory, isolated_settings, worker_id="worker-recover")
    result = recovered.run_once()
    assert result is not None
    assert result.delivered is True
    assert result.observed_state == WorkflowState.SUCCEEDED.value


def test_cancel_and_expiry_win_over_old_confirm_event(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    cancel_id = _confirm_ready(
        session_factory=session_factory,
        settings=isolated_settings,
        start=date(2026, 10, 3),
    )
    expire_id = _confirm_ready(
        session_factory=session_factory,
        settings=isolated_settings,
        start=date(2026, 10, 4),
    )
    ConfirmationService(session_factory, isolated_settings).cancel(
        action_id=cancel_id, context=ALEX
    )
    with session_factory() as session:
        revision = WorkflowRepository().get_revision(session, expire_id)
        assert revision is not None
        revision.confirmed_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()
    worker = WorkflowWorker(session_factory, isolated_settings, worker_id="worker-close")
    first = worker.run_once()
    second = worker.run_once()
    assert first is not None
    assert second is not None
    assert {first.observed_state, second.observed_state} == {
        WorkflowState.CANCELLED.value,
        WorkflowState.EXPIRED.value,
    }
    with session_factory() as session:
        cancel_state = WorkflowRepository().get_revision(session, cancel_id).state
        expire_state = WorkflowRepository().get_revision(session, expire_id).state
        delivered = session.execute(
            text("SELECT count(*) FROM workflow_outbox WHERE delivered_at IS NOT NULL")
        ).scalar_one()
    assert cancel_state == WorkflowState.CANCELLED.value
    assert expire_state == WorkflowState.EXPIRED.value
    assert delivered == 2


def test_checkpoint_failure_and_awaiting_invariant_do_not_change_business_state(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        missing_id = _create_action(session, date(2026, 10, 5))
        session.commit()
    confirmation = ConfirmationService(session_factory, isolated_settings)
    issued = confirmation.issue_challenge(action_id=missing_id, context=ALEX)
    confirmation.confirm(
        action_id=missing_id,
        challenge_id=issued.challenge_id,
        confirmation_token=issued.confirmation_token,
        context=ALEX,
    )
    with session_factory() as session:
        thread_id = session.execute(
            text("SELECT langgraph_thread_id FROM action_workflows WHERE action_id = :action_id"),
            {"action_id": missing_id},
        ).scalar_one()
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            session.execute(
                text(f"DELETE FROM {table} WHERE thread_id = :thread_id"),
                {"thread_id": thread_id},
            )
        session.commit()
    worker = WorkflowWorker(session_factory, isolated_settings, worker_id="worker-fail")
    with pytest.raises(OrchestrationAuthorityError):
        worker.run_once()
    with session_factory() as session:
        missing_state = WorkflowRepository().get_revision(session, missing_id).state
        missing_outbox = session.execute(
            text("SELECT delivered_at, last_failure_kind FROM workflow_outbox")
        ).one()
    assert missing_state == WorkflowState.CONFIRMED.value
    assert missing_outbox.delivered_at is None
    assert missing_outbox.last_failure_kind == "checkpoint_failure"

    awaiting_id = _confirm_ready(
        session_factory=session_factory,
        settings=isolated_settings,
        start=date(2026, 10, 6),
    )
    with session_factory() as session:
        WorkflowRepository().apply_revision_state(
            session, action_id=awaiting_id, state=WorkflowState.AWAITING_CONFIRMATION
        )
        session.commit()
    with pytest.raises(WorkflowInvariantError):
        worker.run_once()
    with session_factory() as session:
        awaiting_state = WorkflowRepository().get_revision(session, awaiting_id).state
        awaiting_outbox = session.execute(
            text(
                """
                SELECT delivered_at, last_failure_kind FROM workflow_outbox
                WHERE action_id = :action_id
                """
            ),
            {"action_id": awaiting_id},
        ).one()
    assert awaiting_state == WorkflowState.AWAITING_CONFIRMATION.value
    assert awaiting_outbox.delivered_at is None
    assert awaiting_outbox.last_failure_kind == "invariant_failure"


def test_competing_workers_and_expired_lock_reclaim(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    action_id = _confirm_ready(
        session_factory=session_factory,
        settings=isolated_settings,
        start=date(2026, 10, 7),
    )
    owner = WorkflowWorker(session_factory, isolated_settings, worker_id="owner")
    claimed = owner.claim_one()
    assert claimed is not None
    competitor = WorkflowWorker(session_factory, isolated_settings, worker_id="competitor")
    assert competitor.claim_one() is None
    with session_factory() as session:
        row = session.get(WorkflowOutbox, claimed.event_id)
        assert row is not None
        row.locked_until = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    reclaimed = competitor.claim_one()
    assert reclaimed is not None
    assert reclaimed.event_id == claimed.event_id
    result = competitor.deliver(reclaimed, mark_delivered=True)
    assert result.observed_state == WorkflowState.SUCCEEDED.value
    with session_factory() as session:
        state = WorkflowRepository().get_revision(session, action_id).state
    assert state == WorkflowState.SUCCEEDED.value


def test_corrupt_checkpoint_is_released_for_retry(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    action_id = _confirm_ready(
        session_factory=session_factory,
        settings=isolated_settings,
        start=date(2026, 10, 8),
    )
    with session_factory() as session:
        thread_id = WorkflowRepository().get_workflow(session, action_id).langgraph_thread_id
    with open_postgres_checkpointer(isolated_settings) as checkpointer:
        checkpointer.put(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            empty_checkpoint(),
            {},
            {},
        )
    worker = WorkflowWorker(session_factory, isolated_settings, worker_id="worker-corrupt")
    with pytest.raises(OrchestrationAuthorityError):
        worker.run_once()
    with session_factory() as session:
        state = WorkflowRepository().get_revision(session, action_id).state
        outbox = session.execute(
            text("SELECT delivered_at, last_failure_kind FROM workflow_outbox")
        ).one()
    assert state == WorkflowState.CONFIRMED.value
    assert outbox.delivered_at is None
    assert outbox.last_failure_kind == "checkpoint_failure"


def test_manual_outbox_without_confirm_is_not_used_to_authorize(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        action_id = _create_action(session, date(2026, 10, 9))
        session.commit()
    WorkflowOrchestrationService(session_factory).start(
        action_id=action_id,
        owner_subject_id=ALEX.subject_id or "",
        settings=isolated_settings,
    )
    with session_factory() as session:
        OutboxRepository().enqueue(
            session,
            NewOutboxEvent(
                event_key=f"confirmation_committed:{action_id}:1",
                action_id=action_id,
                event_type=OutboxEventType.CONFIRMATION_COMMITTED,
                available_at=datetime.now(UTC),
            ),
        )
        session.commit()
    worker = WorkflowWorker(session_factory, isolated_settings, worker_id="worker-fake")
    with pytest.raises(WorkflowInvariantError):
        worker.run_once()
    with session_factory() as session:
        state = WorkflowRepository().get_revision(session, action_id).state
    assert state == WorkflowState.AWAITING_CONFIRMATION.value

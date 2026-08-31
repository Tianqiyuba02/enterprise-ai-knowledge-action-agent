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
from app.workflow.domain import ActionType, WorkflowState
from app.workflow.executable_preparation import V4ExecutablePreparationService
from app.workflow.execution import ExecutionReservationService
from app.workflow.executor import LeaveSubmissionExecutor
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


def _replace_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[KnowledgeSettings]:
    live = load_knowledge_settings()
    admin_url = live.database_url.get_secret_value()
    database_name = f"knowledge_agent_v4_gr_{uuid.uuid4().hex[:12]}"
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


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def _workflow_state(engine: Engine, action_id) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT state FROM action_revisions WHERE action_id = :action_id"),
            {"action_id": action_id},
        ).scalar_one()


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


class _SubmitCounter:
    def __init__(self) -> None:
        self.calls = 0

    def track(self, original):
        def wrapper(executor, permit):
            self.calls += 1
            return original(executor, permit)

        return wrapper


def test_duplicate_confirmation_wake_does_not_create_a_second_submit(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _SubmitCounter()
    monkeypatch.setattr(
        LeaveSubmissionExecutor,
        "submit",
        counter.track(LeaveSubmissionExecutor.submit),
    )
    action_id = _confirm_ready(
        session_factory=session_factory,
        settings=isolated_settings,
        start=date(2026, 10, 13),
    )
    worker = WorkflowWorker(session_factory, isolated_settings, worker_id="graph-worker-a")
    first = worker.run_once()
    assert first is not None
    assert first.observed_state == WorkflowState.SUCCEEDED.value
    assert first.delivered is True
    assert counter.calls == 1
    assert _count(engine, "leave_requests") == 1
    second = worker.run_once()
    assert second is None
    replay = WorkflowOrchestrationService(session_factory).resume_internal(
        action_id=action_id,
        settings=isolated_settings,
        worker_id="graph-worker-a",
    )
    assert replay["observed_state"] == WorkflowState.SUCCEEDED.value
    assert counter.calls == 1
    assert _count(engine, "leave_requests") == 1


def test_succeeded_graph_then_worker_bookkeeping_does_not_submit(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _SubmitCounter()
    monkeypatch.setattr(
        LeaveSubmissionExecutor,
        "submit",
        counter.track(LeaveSubmissionExecutor.submit),
    )
    action_id = _confirm_ready(
        session_factory=session_factory,
        settings=isolated_settings,
        start=date(2026, 10, 14),
    )
    worker = WorkflowWorker(session_factory, isolated_settings, worker_id="graph-worker-b")
    claimed = worker.claim_one()
    assert claimed is not None
    result = worker.deliver(claimed, mark_delivered=True)
    assert result.observed_state == WorkflowState.SUCCEEDED.value
    assert counter.calls == 1
    bookkeeping = WorkflowOrchestrationService(session_factory).resume_internal(
        action_id=action_id,
        settings=isolated_settings,
        worker_id="graph-worker-b",
    )
    assert bookkeeping["observed_state"] == WorkflowState.SUCCEEDED.value
    assert counter.calls == 1
    assert _count(engine, "leave_requests") == 1


def test_ended_checkpoint_and_executing_db_recovers_without_submit(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _SubmitCounter()
    monkeypatch.setattr(
        LeaveSubmissionExecutor,
        "submit",
        counter.track(LeaveSubmissionExecutor.submit),
    )
    action_id = _confirm_ready(
        session_factory=session_factory,
        settings=isolated_settings,
        start=date(2026, 10, 15),
    )
    WorkflowOrchestrationService(session_factory).resume(
        action_id=action_id,
        owner_subject_id=ALEX.subject_id or "",
        settings=isolated_settings,
    )
    assert _workflow_state(engine, action_id) == WorkflowState.CONFIRMED.value
    assert _count(engine, "leave_requests") == 0
    reserved = ExecutionReservationService(session_factory, isolated_settings).reserve(
        action_id=action_id,
        revision=1,
        worker_id="graph-worker-c",
    )
    assert reserved.permit is not None
    assert _workflow_state(engine, action_id) == WorkflowState.EXECUTING.value
    recovered = WorkflowOrchestrationService(session_factory).resume_internal(
        action_id=action_id,
        settings=isolated_settings,
        worker_id="graph-worker-c",
    )
    assert recovered["observed_state"] == WorkflowState.EXECUTION_FAILED.value
    assert counter.calls == 0
    assert _count(engine, "leave_requests") == 0


def test_employee_start_and_resume_cannot_submit(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _SubmitCounter()
    monkeypatch.setattr(
        LeaveSubmissionExecutor,
        "submit",
        counter.track(LeaveSubmissionExecutor.submit),
    )
    with session_factory() as session:
        action_id = _create_action(session, date(2026, 10, 16))
        session.commit()
    orchestration = WorkflowOrchestrationService(session_factory)
    started = orchestration.start(
        action_id=action_id,
        owner_subject_id=ALEX.subject_id or "",
        settings=isolated_settings,
    )
    assert started.get("__interrupt__")
    confirmation = ConfirmationService(session_factory, isolated_settings)
    issued = confirmation.issue_challenge(action_id=action_id, context=ALEX)
    confirmation.confirm(
        action_id=action_id,
        challenge_id=issued.challenge_id,
        confirmation_token=issued.confirmation_token,
        context=ALEX,
    )
    resumed = orchestration.resume(
        action_id=action_id,
        owner_subject_id=ALEX.subject_id or "",
        settings=isolated_settings,
    )
    assert resumed["observed_state"] == WorkflowState.CONFIRMED.value
    assert counter.calls == 0
    assert _count(engine, "leave_requests") == 0
    assert _count(engine, "action_execution_ledger") == 0


def test_terminal_checkpoint_replay_never_enters_submit(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _SubmitCounter()
    monkeypatch.setattr(
        LeaveSubmissionExecutor,
        "submit",
        counter.track(LeaveSubmissionExecutor.submit),
    )
    action_id = _confirm_ready(
        session_factory=session_factory,
        settings=isolated_settings,
        start=date(2026, 10, 19),
    )
    worker = WorkflowWorker(session_factory, isolated_settings, worker_id="graph-worker-d")
    assert worker.run_once() is not None
    assert counter.calls == 1
    for _ in range(2):
        replay = WorkflowOrchestrationService(session_factory).resume_internal(
            action_id=action_id,
            settings=isolated_settings,
            worker_id="graph-worker-d",
        )
        assert replay["observed_state"] == WorkflowState.SUCCEEDED.value
    assert counter.calls == 1
    assert _count(engine, "leave_requests") == 1


def test_executing_crash_recovery_is_reconcile_only_once_checkpoint_ended(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _SubmitCounter()
    monkeypatch.setattr(
        LeaveSubmissionExecutor,
        "submit",
        counter.track(LeaveSubmissionExecutor.submit),
    )
    action_id = _confirm_ready(
        session_factory=session_factory,
        settings=isolated_settings,
        start=date(2026, 10, 20),
    )
    WorkflowOrchestrationService(session_factory).resume(
        action_id=action_id,
        owner_subject_id=ALEX.subject_id or "",
        settings=isolated_settings,
    )
    ExecutionReservationService(session_factory, isolated_settings).reserve(
        action_id=action_id,
        revision=1,
        worker_id="graph-worker-e",
    )
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
    recovered = WorkflowOrchestrationService(session_factory).resume_internal(
        action_id=action_id,
        settings=isolated_settings,
        worker_id="graph-worker-f",
    )
    assert recovered["observed_state"] == WorkflowState.EXECUTION_FAILED.value
    assert counter.calls == 0
    assert _count(engine, "leave_requests") == 0

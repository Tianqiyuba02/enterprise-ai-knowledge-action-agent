import os
import threading
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
from app.workflow.domain import ActionType, WorkflowState
from app.workflow.executable_preparation import V4ExecutablePreparationService
from app.workflow.execution import ExecutionReservationService, ReservationOutcome
from app.workflow.executor import (
    BusinessOutcome,
    ExecutorFailpoints,
    LeaveSubmissionExecutor,
)
from app.workflow.finalization import (
    MAX_AUTOMATIC_RECONCILIATION_ATTEMPTS,
    ExecutionFinalizationService,
)
from app.workflow.runtime import WorkflowExecutionRuntime
from app.workflow.time import database_now
from app.workflow.workflow_repository import NewWorkflowRevision, WorkflowRepository

ALEX = DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"]

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]

WORKER_A = "workflow-worker:recover-a"
WORKER_B = "workflow-worker:recover-b"


def _replace_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[KnowledgeSettings]:
    live = load_knowledge_settings()
    admin_url = live.database_url.get_secret_value()
    database_name = f"knowledge_agent_v4_rc_{uuid.uuid4().hex[:12]}"
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


def _persist_action(session: Session, *, start: date) -> uuid.UUID:
    prepared = V4ExecutablePreparationService().prepare(
        session,
        context=ALEX,
        start_date=start,
        end_date=start,
        reason="Family visit",
    )
    now = database_now(session)
    workflow, revision = WorkflowRepository().create_workflow_and_revision(
        session,
        NewWorkflowRevision(
            owner_subject_id=ALEX.subject_id or "",
            owner_employee_id=ALEX.employee_id,
            jurisdiction=ALEX.jurisdiction or "AU-VIC",
            action_type=ActionType.SUBMIT_ANNUAL_LEAVE,
            state=WorkflowState.CONFIRMED,
            draft_payload=prepared.payload(),
            draft_hash=prepared.draft.fingerprint(),
            authority_snapshot_hash=prepared.snapshot.fingerprint(),
            business_request_key=prepared.business_request_key,
            ruleset_version=prepared.draft.ruleset_version,
            calendar_version=prepared.draft.calendar_version,
            action_expires_at=now + timedelta(hours=1),
        ),
    )
    revision.confirmed_at = now
    revision.confirmed_expires_at = now + timedelta(hours=1)
    return workflow.action_id


def test_crash_before_business_then_takeover_reconcile(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    with session_factory() as session:
        action_id = _persist_action(session, start=date(2026, 9, 14))
        session.commit()
    reserved = reservation.reserve(action_id=action_id, revision=1, worker_id=WORKER_A)
    assert reserved.outcome is ReservationOutcome.RESERVED
    assert _count(engine, "leave_requests") == 0
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
    runtime = WorkflowExecutionRuntime(session_factory, isolated_settings, worker_id=WORKER_B)
    state = runtime.reconcile(str(action_id), 1)
    assert state == WorkflowState.EXECUTION_FAILED.value
    assert _count(engine, "leave_requests") == 0
    stale = LeaveSubmissionExecutor(session_factory, isolated_settings).submit(reserved.permit)
    assert stale.outcome is BusinessOutcome.EXECUTION_AUTHORITY_LOST
    assert stale.failure_kind == "stale_generation"
    assert _count(engine, "leave_requests") == 0


def test_business_commit_then_finalize_recovers_succeeded(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    with session_factory() as session:
        action_id = _persist_action(session, start=date(2026, 9, 15))
        session.commit()
    reserved = reservation.reserve(action_id=action_id, revision=1, worker_id=WORKER_A)
    assert reserved.permit is not None
    unknown = LeaveSubmissionExecutor(
        session_factory,
        isolated_settings,
        failpoints=ExecutorFailpoints(report_unknown_after_commit=True),
    ).submit(reserved.permit)
    assert unknown.outcome is BusinessOutcome.OUTCOME_UNKNOWN
    assert _count(engine, "leave_requests") == 1
    runtime = WorkflowExecutionRuntime(session_factory, isolated_settings, worker_id=WORKER_A)
    state = runtime.reconcile(str(action_id), 1)
    assert state == WorkflowState.SUCCEEDED.value
    assert _count(engine, "leave_requests") == 1


def test_unknown_schedules_bounded_reconciliation(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    finalizer = ExecutionFinalizationService(session_factory, isolated_settings)
    with session_factory() as session:
        action_id = _persist_action(session, start=date(2026, 9, 16))
        session.commit()
    reserved = reservation.reserve(action_id=action_id, revision=1, worker_id=WORKER_A)
    assert reserved.permit is not None
    state = finalizer.finalize(
        reserved.permit,
        unknown_result(reserved.permit.execution_key),
    )
    assert state == WorkflowState.UNKNOWN_OUTCOME.value
    assert _count(engine, "workflow_outbox") == 1

    for _ in range(MAX_AUTOMATIC_RECONCILIATION_ATTEMPTS):
        state = finalizer.finalize(
            reserved.permit,
            unknown_result(reserved.permit.execution_key),
        )
    assert state == WorkflowState.UNKNOWN_OUTCOME.value
    with session_factory() as session:
        revision = WorkflowRepository().get_revision(session, action_id)
        ledger_count = session.execute(
            text(
                """
                SELECT reconciliation_attempt_count, manual_review_required
                FROM action_execution_ledger
                WHERE action_id = :action_id
                """
            ),
            {"action_id": action_id},
        ).one()
    assert revision is not None
    assert revision.manual_review_required is True
    assert ledger_count.reconciliation_attempt_count == MAX_AUTOMATIC_RECONCILIATION_ATTEMPTS
    assert ledger_count.manual_review_required is True
    assert _count(engine, "workflow_outbox") == MAX_AUTOMATIC_RECONCILIATION_ATTEMPTS
    assert _count(engine, "leave_requests") == 0


def test_second_identical_action_blocked_while_unknown(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    finalizer = ExecutionFinalizationService(session_factory, isolated_settings)
    with session_factory() as session:
        first_id = _persist_action(session, start=date(2026, 9, 17))
        second_id = _persist_action(session, start=date(2026, 9, 17))
        session.commit()
    first = reservation.reserve(action_id=first_id, revision=1, worker_id=WORKER_A)
    assert first.permit is not None
    finalizer.finalize(first.permit, unknown_result(first.permit.execution_key))
    second = reservation.reserve(action_id=second_id, revision=1, worker_id=WORKER_B)
    assert second.outcome is ReservationOutcome.BLOCKED_UNRESOLVED
    assert second.retryable is True


def test_competing_takeover_has_one_generation_winner(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    reservation = ExecutionReservationService(session_factory, isolated_settings)
    with session_factory() as session:
        action_id = _persist_action(session, start=date(2026, 9, 18))
        session.commit()
    reserved = reservation.reserve(action_id=action_id, revision=1, worker_id=WORKER_A)
    assert reserved.permit is not None
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
    barrier = threading.Barrier(2)
    generations: list[int] = []
    errors: list[str] = []

    def takeover(worker_id: str) -> None:
        barrier.wait()
        try:
            permit = reservation.takeover_expired_lease(
                action_id=action_id, revision=1, worker_id=worker_id
            )
            generations.append(permit.lease_generation)
        except Exception as exc:
            errors.append(type(exc).__name__)

    workers = [
        threading.Thread(target=takeover, args=(WORKER_A,)),
        threading.Thread(target=takeover, args=(WORKER_B,)),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert generations.count(2) == 1
    assert len(generations) + len(errors) == 2
    with session_factory() as session:
        generation = session.execute(
            text(
                "SELECT lease_generation FROM action_execution_ledger WHERE action_id = :action_id"
            ),
            {"action_id": action_id},
        ).scalar_one()
    assert generation == 2


def unknown_result(execution_key: str):
    from app.workflow.executor import ExecutorResult

    return ExecutorResult(BusinessOutcome.OUTCOME_UNKNOWN, execution_key=execution_key)

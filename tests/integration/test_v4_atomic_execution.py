"""Replacement crash/race/security proofs for simplified CONFIRMED execution."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from isolated_postgres import isolated_settings_for_engine, isolated_test_engine
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.agent.leave_models import LeavePreparationStatus, LeaveRequestDraft
from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.config import KnowledgeSettings
from app.db.session import create_knowledge_session_factory
from app.errors import ActionConflictError
from app.identity import AuthenticatedEmployeeContext
from app.workflow.action_creation import ActionCreationService
from app.workflow.atomic_execution import (
    AtomicConfirmedExecutor,
    AtomicExecutionFailpoints,
    AtomicOutcome,
    FailureScope,
)
from app.workflow.confirmation import ConfirmationService
from app.workflow.confirmed_poller import ConfirmedActionPoller
from app.workflow.domain import LeaveType, WorkflowState
from app.workflow.leave_command_repository import LeaveCommandRepository, NewLeaveRequest
from app.workflow.locks import acquire_employee_lock
from app.workflow.time import database_now

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]

ALEX = DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"]
SAM = DEMO_IDENTITY_BINDINGS["demo-v1-3b8e6d50"]
FORBIDDEN_WITH_LEAVE = frozenset(
    {
        WorkflowState.EXECUTION_FAILED.value,
        WorkflowState.STALE.value,
        WorkflowState.EXPIRED.value,
        WorkflowState.CANCELLED.value,
    }
)


@pytest.fixture
def isolated_settings() -> Iterator[KnowledgeSettings]:
    with isolated_test_engine(prefix="knowledge_agent_v4_ax") as engine:
        yield isolated_settings_for_engine(engine)


@pytest.fixture
def engine(isolated_settings: KnowledgeSettings) -> Iterator[Engine]:
    from app.db.session import create_knowledge_engine

    engine = create_knowledge_engine(isolated_settings)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_knowledge_session_factory(engine)


def _draft(*, start: date, end: date | None = None) -> LeaveRequestDraft:
    return LeaveRequestDraft(
        leave_type="annual",
        start_date=start,
        end_date=end or start,
        scheduled_work_days=1,
        requested_hours=Decimal("8.00"),
        current_balance_hours=Decimal("76.00"),
        projected_balance_hours=Decimal("68.00"),
        preparation_status=LeavePreparationStatus.READY,
        reason="Family visit",
        public_holiday_check_required=True,
        non_executing=True,
    )


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def _state(engine: Engine, action_id: UUID) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT state FROM action_revisions WHERE action_id = :action_id"),
            {"action_id": action_id},
        ).scalar_one()


def _leave_count_for(engine: Engine, action_id: UUID) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT count(*) FROM leave_requests WHERE source_action_id = :action_id"),
            {"action_id": action_id},
        ).scalar_one()


def _prepare_confirmed(
    session_factory: sessionmaker[Session],
    settings: KnowledgeSettings,
    context: AuthenticatedEmployeeContext,
    start: date,
    end: date | None = None,
) -> UUID:
    created = ActionCreationService(session_factory, settings).create_or_reuse(
        context, _draft(start=start, end=end)
    )
    assert created.action_id is not None
    confirmation = ConfirmationService(session_factory, settings)
    issued = confirmation.issue_challenge(action_id=created.action_id, context=context)
    confirmed = confirmation.confirm(
        action_id=created.action_id,
        challenge_id=issued.challenge_id,
        confirmation_token=issued.confirmation_token,
        context=context,
    )
    assert confirmed.state == WorkflowState.CONFIRMED.value
    return created.action_id


def _executor(
    session_factory: sessionmaker[Session],
    settings: KnowledgeSettings,
    failpoints: AtomicExecutionFailpoints | None = None,
) -> AtomicConfirmedExecutor:
    return AtomicConfirmedExecutor(session_factory, settings, failpoints=failpoints)


def _revision_payload(session_factory: sessionmaker[Session], action_id: UUID) -> dict:
    with session_factory() as session:
        return dict(
            session.execute(
                text(
                    """
                    SELECT business_request_key, calendar_version, ruleset_version, draft_payload
                    FROM action_revisions WHERE action_id = :action_id
                    """
                ),
                {"action_id": action_id},
            )
            .mappings()
            .one()
        )


def _mutate_stable_authority(
    session_factory: sessionmaker[Session],
    action_id: UUID,
    **fields: object,
) -> None:
    with session_factory() as session:
        payload = dict(
            session.execute(
                text("SELECT draft_payload FROM action_revisions WHERE action_id = :action_id"),
                {"action_id": action_id},
            ).scalar_one()
        )
        stable = dict(payload["stable_authority"])
        stable.update(fields)
        payload["stable_authority"] = stable
        session.execute(
            text(
                """
                UPDATE action_revisions
                SET draft_payload = CAST(:payload AS jsonb)
                WHERE action_id = :action_id
                """
            ),
            {"action_id": action_id, "payload": json.dumps(payload)},
        )
        session.commit()


def _assert_no_forbidden_leave_pairs(engine: Engine) -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT ar.action_id, ar.state
                FROM action_revisions ar
                JOIN leave_requests lr ON lr.source_action_id = ar.action_id
                WHERE ar.state = ANY(:states)
                """
            ),
            {"states": list(FORBIDDEN_WITH_LEAVE)},
        ).all()
    assert rows == []


def test_confirmed_action_executes_exactly_once(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 10, 21))
    first = _executor(session_factory, isolated_settings).execute_one()
    second = _executor(session_factory, isolated_settings).execute_one()
    assert first.outcome is AtomicOutcome.SUCCEEDED
    assert first.action_id == action_id
    assert first.adopted is False
    assert second.outcome is AtomicOutcome.IDLE
    assert _state(engine, action_id) == WorkflowState.SUCCEEDED.value
    assert _leave_count_for(engine, action_id) == 1
    _assert_no_forbidden_leave_pairs(engine)


def test_two_workers_same_action(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 10, 22))
    held = threading.Event()
    release = threading.Event()
    second_done = threading.Event()
    results: dict[str, AtomicOutcome] = {}

    def hold() -> None:
        held.set()
        assert release.wait(timeout=5)

    def first() -> None:
        results["holder"] = (
            _executor(
                session_factory,
                isolated_settings,
                AtomicExecutionFailpoints(hold_after_action_lock=hold),
            )
            .execute_action(action_id)
            .outcome
        )

    def second() -> None:
        assert held.wait(timeout=5)
        results["waiter"] = (
            _executor(session_factory, isolated_settings).execute_action(action_id).outcome
        )
        second_done.set()

    workers = [threading.Thread(target=first), threading.Thread(target=second)]
    for worker in workers:
        worker.start()
    assert second_done.wait(timeout=5)
    assert results["waiter"] is AtomicOutcome.IDLE
    release.set()
    for worker in workers:
        worker.join()
    assert results["holder"] is AtomicOutcome.SUCCEEDED
    assert AtomicOutcome.EXECUTION_FAILED not in results.values()
    assert _leave_count_for(engine, action_id) == 1
    assert _state(engine, action_id) == WorkflowState.SUCCEEDED.value


def test_worker_dies_before_leave_insert(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 10, 23))
    failing = _executor(
        session_factory,
        isolated_settings,
        AtomicExecutionFailpoints(raise_before_leave_insert=RuntimeError("die-before-insert")),
    )
    died = failing.execute_action(action_id)
    assert died.outcome is AtomicOutcome.TRANSIENT
    assert _state(engine, action_id) == WorkflowState.CONFIRMED.value
    assert _leave_count_for(engine, action_id) == 0
    retry = _executor(session_factory, isolated_settings).execute_action(action_id)
    assert retry.outcome is AtomicOutcome.SUCCEEDED
    assert _leave_count_for(engine, action_id) == 1


@pytest.mark.parametrize(
    "failpoint",
    [
        "raise_after_leave_insert",
        "raise_after_succeeded_update",
        "raise_on_audit",
    ],
)
def test_mid_transaction_failure_rolls_back_all(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
    failpoint: str,
) -> None:
    starts = {
        "raise_after_leave_insert": date(2026, 10, 26),
        "raise_after_succeeded_update": date(2026, 10, 27),
        "raise_on_audit": date(2026, 10, 28),
    }
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, starts[failpoint])
    failing = _executor(
        session_factory,
        isolated_settings,
        AtomicExecutionFailpoints(**{failpoint: RuntimeError(failpoint)}),
    )
    died = failing.execute_action(action_id)
    assert died.outcome is AtomicOutcome.TRANSIENT
    assert _state(engine, action_id) == WorkflowState.CONFIRMED.value
    assert _leave_count_for(engine, action_id) == 0
    retry = _executor(session_factory, isolated_settings).execute_action(action_id)
    assert retry.outcome is AtomicOutcome.SUCCEEDED
    assert _leave_count_for(engine, action_id) == 1


def test_lost_commit_acknowledgement(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 10, 29))
    result = _executor(
        session_factory,
        isolated_settings,
        AtomicExecutionFailpoints(discard_after_commit=True),
    ).execute_action(action_id)
    assert result.outcome is AtomicOutcome.SUCCEEDED
    assert result.observed_state == WorkflowState.SUCCEEDED.value
    assert _state(engine, action_id) == WorkflowState.SUCCEEDED.value
    assert _leave_count_for(engine, action_id) == 1
    replay = _executor(session_factory, isolated_settings).execute_action(action_id)
    assert replay.outcome is AtomicOutcome.IDLE
    assert _leave_count_for(engine, action_id) == 1


def test_same_action_replay_and_same_business_key(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 10, 30))
    first = _executor(session_factory, isolated_settings).execute_action(action_id)
    replay = _executor(session_factory, isolated_settings).execute_action(action_id)
    assert first.outcome is AtomicOutcome.SUCCEEDED
    assert replay.outcome is AtomicOutcome.IDLE
    again = ActionCreationService(session_factory, isolated_settings).create_or_reuse(
        ALEX, _draft(start=date(2026, 10, 30))
    )
    assert again.action_id == action_id
    assert again.state == WorkflowState.SUCCEEDED.value
    assert _count(engine, "leave_requests") == 1


def test_two_different_same_employee_requests_jointly_exceed_balance(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    first_id = _prepare_confirmed(
        session_factory, isolated_settings, ALEX, date(2026, 11, 2), date(2026, 11, 10)
    )
    second_id = _prepare_confirmed(
        session_factory, isolated_settings, ALEX, date(2026, 11, 16), date(2026, 11, 24)
    )
    entered = threading.Barrier(2)
    results: dict[UUID, str] = {}

    def hold() -> None:
        entered.wait(timeout=5)

    def run(action_id: UUID) -> None:
        result = _executor(
            session_factory,
            isolated_settings,
            AtomicExecutionFailpoints(hold_before_employee_lock=hold),
        ).execute_action(action_id)
        results[action_id] = result.observed_state or result.outcome.value

    workers = [
        threading.Thread(target=run, args=(first_id,)),
        threading.Thread(target=run, args=(second_id,)),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    states = set(results.values())
    assert WorkflowState.SUCCEEDED.value in states
    assert WorkflowState.EXECUTION_FAILED.value in states
    assert _count(engine, "leave_requests") == 1
    _assert_no_forbidden_leave_pairs(engine)


def test_two_different_same_employee_requests_overlap(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    first_id = _prepare_confirmed(
        session_factory, isolated_settings, ALEX, date(2026, 11, 16), date(2026, 11, 18)
    )
    second_id = _prepare_confirmed(
        session_factory, isolated_settings, ALEX, date(2026, 11, 18), date(2026, 11, 19)
    )
    entered = threading.Barrier(2)
    results: dict[UUID, str] = {}

    def hold() -> None:
        entered.wait(timeout=5)

    def run(action_id: UUID) -> None:
        result = _executor(
            session_factory,
            isolated_settings,
            AtomicExecutionFailpoints(hold_before_employee_lock=hold),
        ).execute_action(action_id)
        results[action_id] = result.observed_state or result.outcome.value

    workers = [
        threading.Thread(target=run, args=(first_id,)),
        threading.Thread(target=run, args=(second_id,)),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert set(results.values()) == {
        WorkflowState.SUCCEEDED.value,
        WorkflowState.EXECUTION_FAILED.value,
    }
    assert _count(engine, "leave_requests") == 1


def test_valid_adoption(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 12, 1))
    with session_factory() as session:
        revision = (
            session.execute(
                text(
                    """
                SELECT business_request_key, calendar_version, ruleset_version, draft_payload
                FROM action_revisions WHERE action_id = :action_id
                """
                ),
                {"action_id": action_id},
            )
            .mappings()
            .one()
        )
        payload = revision["draft_payload"]
        LeaveCommandRepository().persist(
            session,
            NewLeaveRequest(
                employee_id=ALEX.employee_id,
                leave_type=LeaveType.ANNUAL,
                start_date=date.fromisoformat(payload["start_date"]),
                end_date=date.fromisoformat(payload["end_date"]),
                requested_hours=Decimal(payload["requested_hours"]),
                reason=payload["reason"],
                submitted_at=database_now(session),
                business_request_key=revision["business_request_key"],
                source_action_id=action_id,
                calendar_version=revision["calendar_version"],
                ruleset_version=revision["ruleset_version"],
            ),
        )
        session.commit()
    result = _executor(session_factory, isolated_settings).execute_action(action_id)
    assert result.outcome is AtomicOutcome.SUCCEEDED
    assert result.adopted is True
    assert _leave_count_for(engine, action_id) == 1


def test_mismatched_adoption_fails_closed(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 12, 2))
    with session_factory() as session:
        revision = session.execute(
            text("SELECT business_request_key FROM action_revisions WHERE action_id = :action_id"),
            {"action_id": action_id},
        ).one()
        LeaveCommandRepository().persist(
            session,
            NewLeaveRequest(
                employee_id=SAM.employee_id,
                leave_type=LeaveType.ANNUAL,
                start_date=date(2026, 12, 2),
                end_date=date(2026, 12, 2),
                requested_hours=Decimal("8.00"),
                reason="mismatch",
                submitted_at=database_now(session),
                business_request_key=revision.business_request_key,
                source_action_id=action_id,
                calendar_version="AU-VIC-2026-v1",
                ruleset_version="v4-annual-leave-1",
            ),
        )
        session.commit()
    result = _executor(session_factory, isolated_settings).execute_action(action_id)
    assert result.outcome is AtomicOutcome.EXECUTION_FAILED
    assert result.failure_kind == "ADOPTION_MISMATCH"
    assert _state(engine, action_id) == WorkflowState.EXECUTION_FAILED.value
    assert _leave_count_for(engine, action_id) == 1
    with engine.connect() as connection:
        employee = connection.execute(
            text("SELECT employee_id FROM leave_requests WHERE source_action_id = :action_id"),
            {"action_id": action_id},
        ).scalar_one()
    assert employee == SAM.employee_id
    assert _state(engine, action_id) != WorkflowState.SUCCEEDED.value


def test_expired_confirmed_is_claimable_and_normalized(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 12, 3))
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_revisions
                SET confirmed_expires_at = clock_timestamp() - interval '1 second'
                WHERE action_id = :action_id
                """
            ),
            {"action_id": action_id},
        )
        session.commit()
    result = _executor(session_factory, isolated_settings).execute_one()
    assert result.action_id == action_id
    assert result.outcome is AtomicOutcome.EXPIRED
    assert _state(engine, action_id) == WorkflowState.EXPIRED.value
    assert _leave_count_for(engine, action_id) == 0


def test_ttl_expires_while_waiting_for_employee_lock(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    short = isolated_settings.model_copy(update={"v4_confirmed_ttl_seconds": 1})
    action_id = _prepare_confirmed(session_factory, short, ALEX, date(2026, 12, 4))
    held = threading.Event()
    waiting = threading.Event()
    release = threading.Event()

    def hold_employee_lock() -> None:
        with session_factory() as session:
            acquire_employee_lock(session, ALEX.employee_id)
            held.set()
            release.wait(timeout=5)
            session.rollback()

    def execute() -> None:
        waiting.set()
        result.append(_executor(session_factory, short).execute_action(action_id).outcome)

    result: list[AtomicOutcome] = []
    holder = threading.Thread(target=hold_employee_lock)
    holder.start()
    assert held.wait(timeout=2)
    worker = threading.Thread(target=execute)
    worker.start()
    assert waiting.wait(timeout=2)
    worker.join(timeout=1.2)
    release.set()
    holder.join(timeout=2)
    worker.join(timeout=5)
    assert result == [AtomicOutcome.EXPIRED]
    assert _state(engine, action_id) == WorkflowState.EXPIRED.value
    assert _leave_count_for(engine, action_id) == 0


def test_insufficient_balance_after_confirmation(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    balance_id = _prepare_confirmed(
        session_factory, isolated_settings, ALEX, date(2026, 12, 7), date(2026, 12, 11)
    )
    prior_id = _prepare_confirmed(
        session_factory, isolated_settings, ALEX, date(2026, 9, 7), date(2026, 9, 18)
    )
    prior = _executor(session_factory, isolated_settings).execute_action(prior_id)
    assert prior.outcome is AtomicOutcome.SUCCEEDED
    balance = _executor(session_factory, isolated_settings).execute_action(balance_id)
    assert balance.outcome is AtomicOutcome.EXECUTION_FAILED
    assert balance.failure_kind == "INSUFFICIENT_BALANCE"
    assert _leave_count_for(engine, balance_id) == 0
    _assert_no_forbidden_leave_pairs(engine)


def test_overlap_after_confirmation(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    overlap_id = _prepare_confirmed(
        session_factory, isolated_settings, ALEX, date(2026, 9, 10), date(2026, 9, 10)
    )
    prior_id = _prepare_confirmed(
        session_factory, isolated_settings, ALEX, date(2026, 9, 9), date(2026, 9, 11)
    )
    prior = _executor(session_factory, isolated_settings).execute_action(prior_id)
    assert prior.outcome is AtomicOutcome.SUCCEEDED
    overlap = _executor(session_factory, isolated_settings).execute_action(overlap_id)
    assert overlap.outcome is AtomicOutcome.EXECUTION_FAILED
    assert overlap.failure_kind == "OVERLAP"
    assert _leave_count_for(engine, overlap_id) == 0
    _assert_no_forbidden_leave_pairs(engine)


def test_stable_authority_becomes_stale(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 12, 14))
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_revisions
                SET calendar_version = 'AU-VIC-CHANGED'
                WHERE action_id = :action_id
                """
            ),
            {"action_id": action_id},
        )
        session.commit()
    result = _executor(session_factory, isolated_settings).execute_action(action_id)
    assert result.outcome is AtomicOutcome.STALE
    assert result.failure_kind == "AUTHORITY_CHANGED"
    assert _state(engine, action_id) == WorkflowState.STALE.value
    assert _leave_count_for(engine, action_id) == 0


def test_transient_db_failure_leaves_confirmed(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 12, 15))
    executor = _executor(
        session_factory,
        isolated_settings,
        AtomicExecutionFailpoints(
            raise_transient_before_commit=OperationalError("SELECT 1", {}, Exception("db-down"))
        ),
    )
    result = executor.execute_action(action_id)
    assert result.outcome is AtomicOutcome.TRANSIENT
    assert result.failure_kind == FailureScope.INFRASTRUCTURE.value
    assert executor.outage_backoff_active()
    assert not executor.action_cooldown_active(action_id)
    assert _state(engine, action_id) == WorkflowState.CONFIRMED.value
    assert _leave_count_for(engine, action_id) == 0
    gated = executor.execute_action(action_id)
    assert gated.outcome is AtomicOutcome.TRANSIENT
    assert gated.action_id is None
    retry = _executor(session_factory, isolated_settings).execute_action(action_id)
    assert retry.outcome is AtomicOutcome.SUCCEEDED


def test_cancel_versus_worker_race(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 12, 16))
    confirmation = ConfirmationService(session_factory, isolated_settings)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def execute() -> None:
        barrier.wait()
        result = _executor(session_factory, isolated_settings).execute_action(action_id)
        outcomes.append(result.observed_state or result.outcome.value)

    def cancel() -> None:
        barrier.wait()
        try:
            view = confirmation.cancel(action_id=action_id, context=ALEX)
            outcomes.append(view.state)
        except ActionConflictError:
            outcomes.append("rejected")

    workers = [threading.Thread(target=execute), threading.Thread(target=cancel)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    final = _state(engine, action_id)
    assert final in {WorkflowState.SUCCEEDED.value, WorkflowState.CANCELLED.value}
    if final == WorkflowState.CANCELLED.value:
        assert _leave_count_for(engine, action_id) == 0
    else:
        assert _leave_count_for(engine, action_id) == 1
    _assert_no_forbidden_leave_pairs(engine)


def test_no_chat_confirmation_or_public_execute_surface() -> None:
    from fastapi.testclient import TestClient

    from app.agent.contracts import V3_TOOL_ALLOWLIST, V3ToolName
    from app.api.application import create_app

    app = create_app()
    paths = set(app.openapi()["paths"])
    assert "/api/v1/actions/{action_id}/confirm" in paths
    assert not any("execute" in path.lower() for path in paths)
    names = {name.value for name in V3_TOOL_ALLOWLIST}
    assert V3ToolName.PREPARE_LEAVE_REQUEST.value in names
    assert all("confirm" not in name and "execute" not in name for name in names)
    with TestClient(app, raise_server_exceptions=False) as client:
        missing = client.post(
            "/api/v1/actions/11111111-1111-1111-1111-111111111111/execute",
            json={"token": "nope"},
        )
        assert missing.status_code == 404


def test_atomic_commit_invariant_holds_after_failures(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    expired_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 12, 17))
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_revisions
                SET confirmed_expires_at = clock_timestamp() - interval '1 second'
                WHERE action_id = :action_id
                """
            ),
            {"action_id": expired_id},
        )
        session.commit()
    _executor(session_factory, isolated_settings).execute_action(expired_id)
    stale_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 12, 18))
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_revisions
                SET calendar_version = 'AU-VIC-CHANGED'
                WHERE action_id = :id
                """
            ),
            {"id": stale_id},
        )
        session.commit()
    _executor(session_factory, isolated_settings).execute_action(stale_id)
    _assert_no_forbidden_leave_pairs(engine)
    with engine.connect() as connection:
        leftover = connection.execute(
            text(
                """
                SELECT count(*)
                FROM action_revisions
                WHERE state IN ('EXECUTING', 'UNKNOWN_OUTCOME', 'RECONCILING')
                """
            )
        ).scalar_one()
    assert leftover == 0


def test_reason_mismatch_cannot_adopt(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 12, 21))
    revision = _revision_payload(session_factory, action_id)
    payload = revision["draft_payload"]
    with session_factory() as session:
        LeaveCommandRepository().persist(
            session,
            NewLeaveRequest(
                employee_id=ALEX.employee_id,
                leave_type=LeaveType.ANNUAL,
                start_date=date.fromisoformat(payload["start_date"]),
                end_date=date.fromisoformat(payload["end_date"]),
                requested_hours=Decimal(payload["requested_hours"]),
                reason="tampered-reason",
                submitted_at=database_now(session),
                business_request_key=revision["business_request_key"],
                source_action_id=action_id,
                calendar_version=revision["calendar_version"],
                ruleset_version=revision["ruleset_version"],
            ),
        )
        session.commit()
    result = _executor(session_factory, isolated_settings).execute_action(action_id)
    assert result.outcome is AtomicOutcome.EXECUTION_FAILED
    assert result.failure_kind == "ADOPTION_MISMATCH"
    assert _state(engine, action_id) == WorkflowState.EXECUTION_FAILED.value
    assert _state(engine, action_id) != WorkflowState.SUCCEEDED.value


def test_work_days_drift_is_stale(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 12, 22))
    _mutate_stable_authority(
        session_factory,
        action_id,
        work_days=["monday", "tuesday", "wednesday", "thursday"],
    )
    result = _executor(session_factory, isolated_settings).execute_action(action_id)
    assert result.outcome is AtomicOutcome.STALE
    assert result.failure_kind == "AUTHORITY_CHANGED"
    assert _leave_count_for(engine, action_id) == 0


def test_hours_per_day_drift_is_stale(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 12, 23))
    _mutate_stable_authority(session_factory, action_id, hours_per_day="6.00")
    result = _executor(session_factory, isolated_settings).execute_action(action_id)
    assert result.outcome is AtomicOutcome.STALE
    assert result.failure_kind == "AUTHORITY_CHANGED"
    assert _leave_count_for(engine, action_id) == 0


def test_timezone_and_jurisdiction_drift_is_stale(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    timezone_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 12, 24))
    _mutate_stable_authority(session_factory, timezone_id, timezone="Australia/Sydney")
    timezone_result = _executor(session_factory, isolated_settings).execute_action(timezone_id)
    assert timezone_result.outcome is AtomicOutcome.STALE
    jurisdiction_id = _prepare_confirmed(
        session_factory, isolated_settings, ALEX, date(2026, 12, 29)
    )
    _mutate_stable_authority(session_factory, jurisdiction_id, jurisdiction="AU-NSW")
    jurisdiction_result = _executor(session_factory, isolated_settings).execute_action(
        jurisdiction_id
    )
    assert jurisdiction_result.outcome is AtomicOutcome.STALE
    assert _leave_count_for(engine, timezone_id) == 0
    assert _leave_count_for(engine, jurisdiction_id) == 0


def test_sufficient_balance_drift_is_not_stale(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    prior_id = _prepare_confirmed(
        session_factory, isolated_settings, ALEX, date(2026, 9, 21), date(2026, 9, 22)
    )
    later_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 12, 30))
    prior = _executor(session_factory, isolated_settings).execute_action(prior_id)
    assert prior.outcome is AtomicOutcome.SUCCEEDED
    later = _executor(session_factory, isolated_settings).execute_action(later_id)
    assert later.outcome is AtomicOutcome.SUCCEEDED
    assert later.outcome is not AtomicOutcome.STALE
    assert _leave_count_for(engine, later_id) == 1


def test_poison_action_does_not_kill_poller(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    older = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 10, 5))
    younger = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 10, 6))

    def poison(action_id: UUID) -> BaseException | None:
        if action_id == older:
            return RuntimeError("poison-action")
        return None

    poller = ConfirmedActionPoller(
        session_factory,
        isolated_settings,
        executor=_executor(
            session_factory,
            isolated_settings,
            AtomicExecutionFailpoints(raise_after_claim=poison),
        ),
    )
    first = poller.run_once()
    second = poller.run_once()
    poller.run_loop(once=True)
    assert first is not None
    assert first.action_id == older
    assert first.outcome is AtomicOutcome.TRANSIENT
    assert second is not None
    assert second.action_id == younger
    assert second.outcome is AtomicOutcome.SUCCEEDED
    assert _state(engine, older) == WorkflowState.CONFIRMED.value
    assert _state(engine, younger) == WorkflowState.SUCCEEDED.value


def test_action_specific_transient_enters_process_local_cooldown(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    cooling = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 10, 7))
    other = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 10, 8))

    def transient(action_id: UUID) -> BaseException | None:
        if action_id == cooling:
            return RuntimeError("action-specific-transient")
        return None

    executor = _executor(
        session_factory,
        isolated_settings,
        AtomicExecutionFailpoints(raise_after_claim=transient),
    )
    first = executor.execute_action(cooling)
    skipped = executor.execute_action(cooling)
    progressed = executor.execute_one()
    assert first.outcome is AtomicOutcome.TRANSIENT
    assert first.failure_kind == FailureScope.ACTION.value
    assert executor.action_cooldown_active(cooling)
    assert not executor.outage_backoff_active()
    assert skipped.outcome is AtomicOutcome.SKIPPED
    assert progressed.action_id == other
    assert progressed.outcome is AtomicOutcome.SUCCEEDED
    assert _state(engine, cooling) == WorkflowState.CONFIRMED.value


def test_lost_ack_uses_fresh_locked_observation(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 10, 9))
    held = threading.Event()
    release = threading.Event()
    blocked = threading.Event()
    results: list[AtomicOutcome] = []

    def after_lock() -> None:
        held.set()
        assert release.wait(timeout=5)

    def observe() -> None:
        results.append(
            _executor(
                session_factory,
                isolated_settings,
                AtomicExecutionFailpoints(
                    discard_after_commit=True,
                    hold_after_lost_ack_lock=after_lock,
                ),
            )
            .execute_action(action_id)
            .outcome
        )

    worker = threading.Thread(target=observe)
    worker.start()
    assert held.wait(timeout=5)

    def contend() -> None:
        with session_factory() as session:
            session.execute(text("SET LOCAL lock_timeout = '1s'"))
            try:
                session.execute(
                    text("SELECT state FROM action_revisions WHERE action_id = :id FOR UPDATE"),
                    {"id": action_id},
                )
            except OperationalError:
                blocked.set()
                session.rollback()
                return
            session.rollback()

    contender = threading.Thread(target=contend)
    contender.start()
    contender.join(timeout=5)
    assert blocked.is_set()
    release.set()
    worker.join(timeout=5)
    assert results == [AtomicOutcome.SUCCEEDED]
    assert _state(engine, action_id) == WorkflowState.SUCCEEDED.value
    assert _leave_count_for(engine, action_id) == 1


def test_uniqueness_conflict_recovers_via_fresh_locked_probe(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 10, 12))
    revision = _revision_payload(session_factory, action_id)
    payload = revision["draft_payload"]
    held = threading.Event()
    inserted = threading.Event()
    results: list = []

    def hold_insert() -> None:
        held.set()
        assert inserted.wait(timeout=5)

    def insert_equivalent() -> None:
        assert held.wait(timeout=5)
        with session_factory() as session:
            LeaveCommandRepository().persist(
                session,
                NewLeaveRequest(
                    employee_id=ALEX.employee_id,
                    leave_type=LeaveType.ANNUAL,
                    start_date=date.fromisoformat(payload["start_date"]),
                    end_date=date.fromisoformat(payload["end_date"]),
                    requested_hours=Decimal(payload["requested_hours"]),
                    reason=payload["reason"],
                    submitted_at=database_now(session),
                    business_request_key=revision["business_request_key"],
                    source_action_id=action_id,
                    calendar_version=revision["calendar_version"],
                    ruleset_version=revision["ruleset_version"],
                ),
            )
            session.commit()
        inserted.set()

    def execute() -> None:
        results.append(
            _executor(
                session_factory,
                isolated_settings,
                AtomicExecutionFailpoints(hold_before_leave_insert=hold_insert),
            ).execute_action(action_id)
        )

    inserter = threading.Thread(target=insert_equivalent)
    worker = threading.Thread(target=execute)
    inserter.start()
    worker.start()
    inserter.join(timeout=5)
    worker.join(timeout=5)
    assert results[0].outcome is AtomicOutcome.SUCCEEDED
    assert results[0].adopted is True
    assert _leave_count_for(engine, action_id) == 1
    assert _state(engine, action_id) == WorkflowState.SUCCEEDED.value


def test_db_wide_failure_after_claim_uses_process_backoff(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 11, 2))
    executor = _executor(
        session_factory,
        isolated_settings,
        AtomicExecutionFailpoints(
            raise_after_claim=lambda _action_id: OperationalError(
                "SELECT 1", {}, Exception("database-unavailable")
            )
        ),
    )
    result = executor.execute_action(action_id)
    assert result.outcome is AtomicOutcome.TRANSIENT
    assert result.failure_kind == FailureScope.INFRASTRUCTURE.value
    assert result.observed_state == WorkflowState.CONFIRMED.value
    assert executor.outage_backoff_active()
    assert not executor.action_cooldown_active(action_id)
    assert _state(engine, action_id) == WorkflowState.CONFIRMED.value
    assert _leave_count_for(engine, action_id) == 0


def test_action_specific_failure_after_claim_uses_action_cooldown(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 11, 16))
    younger = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 11, 17))

    def poison(claimed: UUID) -> BaseException | None:
        if claimed == action_id:
            return RuntimeError("poison-after-claim")
        return None

    executor = _executor(
        session_factory,
        isolated_settings,
        AtomicExecutionFailpoints(raise_after_claim=poison),
    )
    result = executor.execute_action(action_id)
    assert result.outcome is AtomicOutcome.TRANSIENT
    assert result.failure_kind == FailureScope.ACTION.value
    assert executor.action_cooldown_active(action_id)
    assert not executor.outage_backoff_active()
    skipped = executor.execute_action(action_id)
    progressed = executor.execute_one()
    assert skipped.outcome is AtomicOutcome.SKIPPED
    assert progressed.action_id == younger
    assert progressed.outcome is AtomicOutcome.SUCCEEDED
    assert _state(engine, action_id) == WorkflowState.CONFIRMED.value


def test_poller_survives_infrastructure_and_action_failures(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    outage_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 11, 5))
    outage_executor = _executor(
        session_factory,
        isolated_settings,
        AtomicExecutionFailpoints(
            raise_after_claim=lambda _action_id: OperationalError(
                "SELECT 1", {}, Exception("db-outage")
            )
        ),
    )
    outage_poller = ConfirmedActionPoller(
        session_factory,
        isolated_settings,
        executor=outage_executor,
    )
    outage = outage_poller.run_once()
    outage_poller.run_loop(once=True)
    assert outage is not None
    assert outage.outcome is AtomicOutcome.TRANSIENT
    assert outage.failure_kind == FailureScope.INFRASTRUCTURE.value
    assert outage_executor.outage_backoff_active()
    assert not outage_executor.action_cooldown_active(outage_id)
    assert _state(engine, outage_id) == WorkflowState.CONFIRMED.value
    recovered = _executor(session_factory, isolated_settings).execute_action(outage_id)
    assert recovered.outcome is AtomicOutcome.SUCCEEDED

    poison_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 11, 6))
    younger = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 11, 9))

    def poison(action_id: UUID) -> BaseException | None:
        if action_id == poison_id:
            return RuntimeError("poison-action")
        return None

    action_executor = _executor(
        session_factory,
        isolated_settings,
        AtomicExecutionFailpoints(raise_after_claim=poison),
    )
    action_poller = ConfirmedActionPoller(
        session_factory,
        isolated_settings,
        executor=action_executor,
    )
    first = action_poller.run_once()
    second = action_poller.run_once()
    action_poller.run_loop(once=True)
    assert first is not None
    assert first.action_id == poison_id
    assert first.outcome is AtomicOutcome.TRANSIENT
    assert first.failure_kind == FailureScope.ACTION.value
    assert action_executor.action_cooldown_active(poison_id)
    assert not action_executor.outage_backoff_active()
    assert second is not None
    assert second.action_id == younger
    assert second.outcome is AtomicOutcome.SUCCEEDED
    assert _state(engine, poison_id) == WorkflowState.CONFIRMED.value
    assert _state(engine, younger) == WorkflowState.SUCCEEDED.value


def test_lost_ack_observation_uses_fresh_physical_connection(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 11, 10))
    executor = _executor(
        session_factory,
        isolated_settings,
        AtomicExecutionFailpoints(discard_after_commit=True),
    )
    result = executor.execute_action(action_id)
    assert result.outcome is AtomicOutcome.SUCCEEDED
    assert result.observed_state == WorkflowState.SUCCEEDED.value
    assert executor.last_uncertain_backend_pid is not None
    assert executor.last_observation_backend_pid is not None
    assert executor.last_uncertain_backend_pid != executor.last_observation_backend_pid
    assert _state(engine, action_id) == WorkflowState.SUCCEEDED.value
    assert _leave_count_for(engine, action_id) == 1

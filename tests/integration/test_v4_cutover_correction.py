"""Maintenance-mode cutover contract and leftover-authority refusal proofs."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from isolated_postgres import isolated_settings_for_engine, isolated_test_engine
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.agent.leave_models import LeavePreparationStatus, LeaveRequestDraft
from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.config import KnowledgeSettings
from app.db.session import create_knowledge_session_factory
from app.identity import AuthenticatedEmployeeContext
from app.workflow.action_creation import ActionCreationService
from app.workflow.atomic_execution import AtomicConfirmedExecutor, AtomicOutcome
from app.workflow.confirmation import ConfirmationService
from app.workflow.cutover import (
    CutoverHaltError,
    normalize_legacy_execution_states,
    refuse_legacy_execution_scheduling,
    run_execution_cutover_preflight,
)
from app.workflow.domain import ChallengeStatus, LeaveType, WorkflowState
from app.workflow.leave_command_repository import LeaveCommandRepository, NewLeaveRequest
from app.workflow.occupancy import Phase1AInvariantError, assert_cutover_invariants
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


@pytest.fixture
def isolated_settings() -> Iterator[KnowledgeSettings]:
    with isolated_test_engine(prefix="knowledge_agent_v4_co") as engine:
        yield isolated_settings_for_engine(engine)


@pytest.fixture
def engine(isolated_settings: KnowledgeSettings) -> Iterator[Engine]:
    from app.db.session import create_knowledge_engine

    created = create_knowledge_engine(isolated_settings)
    try:
        yield created
    finally:
        created.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_knowledge_session_factory(engine)


def _draft(*, start: date) -> LeaveRequestDraft:
    return LeaveRequestDraft(
        leave_type="annual",
        start_date=start,
        end_date=start,
        scheduled_work_days=1,
        requested_hours=Decimal("7.60"),
        current_balance_hours=Decimal("76.00"),
        projected_balance_hours=Decimal("68.40"),
        preparation_status=LeavePreparationStatus.READY,
        reason="Family visit",
        public_holiday_check_required=True,
        non_executing=True,
    )


def _prepare_awaiting(
    session_factory: sessionmaker[Session],
    settings: KnowledgeSettings,
    context: AuthenticatedEmployeeContext,
    start: date,
) -> UUID:
    created = ActionCreationService(session_factory, settings).create_or_reuse(
        context, _draft(start=start)
    )
    assert created.action_id is not None
    return created.action_id


def _prepare_confirmed(
    session_factory: sessionmaker[Session],
    settings: KnowledgeSettings,
    context: AuthenticatedEmployeeContext,
    start: date,
) -> UUID:
    action_id = _prepare_awaiting(session_factory, settings, context, start)
    confirmation = ConfirmationService(session_factory, settings)
    issued = confirmation.issue_challenge(action_id=action_id, context=context)
    confirmed = confirmation.confirm(
        action_id=action_id,
        challenge_id=issued.challenge_id,
        confirmation_token=issued.confirmation_token,
        context=context,
    )
    assert confirmed.state == WorkflowState.CONFIRMED.value
    return action_id


def test_maintenance_cutover_halts_when_awaiting_has_leave(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_awaiting(session_factory, isolated_settings, ALEX, date(2026, 10, 13))
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
    with (
        engine.connect() as connection,
        pytest.raises(Phase1AInvariantError, match="committed leave"),
    ):
        run_execution_cutover_preflight(connection)
        connection.commit()


def test_new_binary_legacy_execution_entrypoints_are_absent() -> None:
    import importlib

    for name in (
        "app.workflow.execution",
        "app.workflow.executor",
        "app.workflow.finalization",
        "app.workflow.runtime",
        "app.workflow.worker",
        "app.workflow.orchestration",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)
    with pytest.raises(RuntimeError, match="legacy execution scheduling is quiesced"):
        refuse_legacy_execution_scheduling()


def test_awaiting_expiry_normalization_supersedes_active_challenge(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_awaiting(session_factory, isolated_settings, ALEX, date(2026, 10, 14))
    issued = ConfirmationService(session_factory, isolated_settings).issue_challenge(
        action_id=action_id, context=ALEX
    )
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_revisions
                SET action_expires_at = clock_timestamp() - interval '1 second'
                WHERE action_id = :action_id
                """
            ),
            {"action_id": action_id},
        )
        session.commit()
    with engine.connect() as connection:
        changed = normalize_legacy_execution_states(connection)
        assert_cutover_invariants(connection)
        connection.commit()
    assert changed == 1
    with engine.connect() as connection:
        state = connection.execute(
            text("SELECT state FROM action_revisions WHERE action_id = :action_id"),
            {"action_id": action_id},
        ).scalar_one()
        challenge = connection.execute(
            text(
                """
                SELECT status, superseded_at
                FROM confirmation_challenges
                WHERE challenge_id = :challenge_id
                """
            ),
            {"challenge_id": issued.challenge_id},
        ).one()
    assert state == WorkflowState.EXPIRED.value
    assert challenge.status == ChallengeStatus.SUPERSEDED.value
    assert challenge.superseded_at is not None


def test_historical_succeeded_equivalence_mismatch_halts(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    action_id = _prepare_confirmed(session_factory, isolated_settings, ALEX, date(2026, 10, 15))
    result = AtomicConfirmedExecutor(session_factory, isolated_settings).execute_action(action_id)
    assert result.outcome is AtomicOutcome.SUCCEEDED
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE leave_requests
                SET reason = 'tampered-historical-reason'
                WHERE source_action_id = :action_id
                """
            ),
            {"action_id": action_id},
        )
        session.commit()
    with (
        engine.connect() as connection,
        pytest.raises(CutoverHaltError, match="no valid corresponding leave result"),
    ):
        run_execution_cutover_preflight(connection)
        connection.commit()

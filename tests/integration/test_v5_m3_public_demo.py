"""PostgreSQL proofs for M3 quotas, reset, readiness state, and calendar seed."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from isolated_postgres import isolated_settings_for_engine, isolated_test_engine
from pydantic import SecretStr
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.agent.leave_models import LeavePreparationStatus, LeaveRequestDraft
from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.api.routes.demo import guided_scenarios
from app.config import KnowledgeSettings, PublicDemoSettings
from app.db.demo_models import DemoRuntimeState, DemoUsageBucket
from app.db.session import create_knowledge_session_factory
from app.db.workflow_models import ActionWorkflow, ITTicket
from app.demo.calendar import M3_DEMO_CALENDAR_VERSION
from app.demo.leave_execution import M3AtomicConfirmedExecutor, M3ExecutablePreparationService
from app.demo.service import DemoControlService
from app.errors import DemoCapacityReachedError, DemoMaintenanceError
from app.it.domain import ITTicketCategory, ITTicketUrgency, PreparedITSupportTicket
from app.repositories.demo import DemoRepository
from app.workflow.action_creation import ActionCreationDisposition, ActionCreationService
from app.workflow.confirmation import ConfirmationService
from app.workflow.domain import WorkflowState
from app.workflow.it_action_creation import ITActionCreationService

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the PostgreSQL container running",
    ),
]


@pytest.fixture
def m3_engine() -> Iterator[Engine]:
    with isolated_test_engine(prefix="knowledge_agent_v5_m3") as engine:
        yield engine


@pytest.fixture
def m3_settings(m3_engine: Engine) -> KnowledgeSettings:
    return isolated_settings_for_engine(m3_engine)


@pytest.fixture
def m3_factory(m3_engine: Engine) -> sessionmaker[Session]:
    return create_knowledge_session_factory(m3_engine)


def _demo_settings(**updates: object) -> PublicDemoSettings:
    return PublicDemoSettings(
        enabled=True,
        internal_portal_key=SecretStr("m3-test-internal-key-32-characters"),
        _env_file=None,
        **updates,
    )


def test_m3_migration_adds_operational_state_and_extended_calendar(m3_engine: Engine) -> None:
    with m3_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0008_v5_m3_public_demo"
        )
        state = connection.execute(
            text("SELECT maintenance_mode FROM demo_runtime_state WHERE singleton_id = 1")
        ).scalar_one()
        holidays = connection.execute(
            text("SELECT count(*) FROM public_holidays WHERE calendar_version = :version"),
            {"version": M3_DEMO_CALENDAR_VERSION},
        ).scalar_one()
    assert state is False
    assert holidays == 41


def test_guided_leave_date_is_preflighted_for_alex_and_sam(
    m3_factory: sessionmaker[Session],
) -> None:
    repository = DemoRepository()
    with patch(
        "app.api.routes.demo.MelbourneClock.today",
        return_value=datetime(2026, 9, 3).date(),
    ):
        alex = guided_scenarios(
            DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"],
            repository,
            m3_factory,
        )
        sam = guided_scenarios(
            DEMO_IDENTITY_BINDINGS["demo-v1-3b8e6d50"],
            repository,
            m3_factory,
        )
    assert [item.label for item in alex.items] == [
        "Carry over leave",
        "Book next Friday",
        "Broken laptop",
    ]
    assert alex.items[1].prompt == "Prepare annual leave for 2026-09-04."
    assert alex.items[1].note is None
    assert sam.items[1].prompt == "Prepare annual leave for 2026-09-07."
    assert sam.items[1].note is not None


def test_m3_calendar_executes_known_2027_leave_without_changing_v4_rules(
    m3_factory: sessionmaker[Session],
    m3_settings: KnowledgeSettings,
) -> None:
    context = DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"]
    created = ActionCreationService(
        m3_factory,
        m3_settings,
        preparation=M3ExecutablePreparationService(),
    ).create_or_reuse(
        context,
        LeaveRequestDraft(
            leave_type="annual",
            start_date=datetime(2027, 1, 4).date(),
            end_date=datetime(2027, 1, 4).date(),
            scheduled_work_days=1,
            requested_hours=Decimal("7.60"),
            current_balance_hours=Decimal("76.00"),
            projected_balance_hours=Decimal("68.40"),
            preparation_status=LeavePreparationStatus.READY,
            reason="Synthetic longevity proof",
            public_holiday_check_required=True,
            non_executing=True,
        ),
    )
    assert created.disposition is ActionCreationDisposition.CREATED
    assert created.action_id is not None
    assert created.draft is not None
    assert created.draft["calendar_version"] == M3_DEMO_CALENDAR_VERSION
    confirmation = ConfirmationService(m3_factory, m3_settings)
    issued = confirmation.issue_challenge(action_id=created.action_id, context=context)
    confirmation.confirm(
        action_id=created.action_id,
        context=context,
        challenge_id=issued.challenge_id,
        confirmation_token=issued.confirmation_token,
    )
    result = M3AtomicConfirmedExecutor(m3_factory, m3_settings).execute_action(created.action_id)
    assert result.outcome.value == WorkflowState.SUCCEEDED.value
    with m3_factory() as session:
        row = session.execute(
            text(
                "SELECT calendar_version, ruleset_version FROM leave_requests "
                "WHERE source_action_id = :action_id"
            ),
            {"action_id": created.action_id},
        ).one()
    assert row.calendar_version == M3_DEMO_CALENDAR_VERSION
    assert row.ruleset_version == "v4-annual-leave-1"


@pytest.mark.parametrize(
    "target",
    [datetime(2027, 9, 24).date(), datetime(2029, 1, 5).date()],
)
def test_m3_calendar_fails_closed_for_unresolved_or_unsupported_dates(
    m3_factory: sessionmaker[Session],
    m3_settings: KnowledgeSettings,
    target,
) -> None:
    context = DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"]
    result = ActionCreationService(
        m3_factory,
        m3_settings,
        preparation=M3ExecutablePreparationService(),
    ).create_or_reuse(
        context,
        LeaveRequestDraft(
            leave_type="annual",
            start_date=target,
            end_date=target,
            scheduled_work_days=1,
            requested_hours=Decimal("7.60"),
            current_balance_hours=Decimal("76.00"),
            projected_balance_hours=Decimal("68.40"),
            preparation_status=LeavePreparationStatus.READY,
            reason="Synthetic fail-closed proof",
            public_holiday_check_required=True,
            non_executing=True,
        ),
    )
    assert result.disposition is ActionCreationDisposition.NOT_CREATED
    assert result.ineligibility_reason == "calendar_uncovered"


def test_atomic_quota_allows_exactly_one_concurrent_winner(
    m3_factory: sessionmaker[Session],
) -> None:
    service = DemoControlService(
        m3_factory,
        _demo_settings(visitor_assistant_daily_limit=1),
    )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def consume() -> None:
        barrier.wait()
        try:
            service.consume(visitor_id="visitor-a", metric="assistant")
        except DemoCapacityReachedError:
            outcomes.append("rejected")
        else:
            outcomes.append("accepted")

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(outcomes) == ["accepted", "rejected"]
    with m3_factory() as session:
        visitor_count = session.scalar(
            select(DemoUsageBucket.usage_count).where(
                DemoUsageBucket.scope == "visitor",
                DemoUsageBucket.scope_key == "visitor-a",
                DemoUsageBucket.metric == "assistant",
            )
        )
    assert visitor_count == 1


def test_maintenance_fails_closed_and_worker_heartbeat_is_durable(
    m3_factory: sessionmaker[Session],
) -> None:
    service = DemoControlService(m3_factory, _demo_settings())
    with m3_factory() as session:
        state = session.get(DemoRuntimeState, 1)
        assert state is not None
        state.maintenance_mode = True
        session.commit()
    with pytest.raises(DemoMaintenanceError):
        service.consume(visitor_id="visitor-a", metric="assistant")
    with m3_factory() as session:
        state = session.get(DemoRuntimeState, 1)
        assert state is not None
        state.maintenance_mode = False
        session.commit()
    service.heartbeat()
    readiness = service.readiness()
    assert readiness.database is True
    assert readiness.worker is True
    with m3_factory() as session:
        state = session.get(DemoRuntimeState, 1)
        assert state is not None
        state.worker_heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
        session.commit()
    assert service.readiness().worker is False


def test_reset_waits_for_an_active_mutation_lock(
    m3_factory: sessionmaker[Session],
) -> None:
    service = DemoControlService(m3_factory, _demo_settings())
    from app.demo.service import MUTATION_LOCK_ID

    errors: list[BaseException] = []
    with m3_factory() as active_mutation:
        active_mutation.execute(
            text("SELECT pg_advisory_xact_lock_shared(:lock_id)"),
            {"lock_id": MUTATION_LOCK_ID},
        )

        def perform_reset() -> None:
            try:
                service.reset()
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        reset_thread = threading.Thread(target=perform_reset)
        reset_thread.start()
        reset_thread.join(timeout=0.5)
        assert reset_thread.is_alive()
        with m3_factory() as observer:
            state = observer.get(DemoRuntimeState, 1)
            assert state is not None
            assert state.maintenance_mode is True
        active_mutation.rollback()
        reset_thread.join(timeout=10)
    assert reset_thread.is_alive() is False
    assert errors == []


def test_failed_reset_leaves_maintenance_enabled(
    m3_factory: sessionmaker[Session],
) -> None:
    with m3_factory() as session:
        seed = session.scalar(select(ITTicket).where(ITTicket.source_action_id.is_(None)))
        assert seed is not None
        session.delete(seed)
        session.commit()
    service = DemoControlService(m3_factory, _demo_settings())
    with pytest.raises(RuntimeError, match="baseline verification failed"):
        service.reset()
    with m3_factory() as session:
        state = session.get(DemoRuntimeState, 1)
        assert state is not None
        assert state.maintenance_mode is True
        assert state.last_successful_reset_at is None


def test_reset_removes_visitor_actions_and_preserves_three_seed_tickets(
    m3_factory: sessionmaker[Session],
    m3_settings: KnowledgeSettings,
) -> None:
    created = ITActionCreationService(m3_factory, m3_settings).create_or_reuse(
        DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"],
        PreparedITSupportTicket(
            category=ITTicketCategory.HARDWARE,
            summary="Synthetic reset proof",
            description="A deterministic synthetic ticket draft for reset verification.",
            urgency=ITTicketUrgency.LOW,
        ),
        uuid4(),
    )
    assert created.action_id is not None
    service = DemoControlService(m3_factory, _demo_settings())
    service.reset()
    with m3_factory() as session:
        assert session.scalar(select(func.count()).select_from(ActionWorkflow)) == 0
        assert session.scalar(select(func.count()).select_from(ITTicket)) == 3
        state = session.get(DemoRuntimeState, 1)
        assert state is not None
        assert state.maintenance_mode is False
        assert state.last_successful_reset_at is not None
        next_ticket = session.execute(text("SELECT nextval('it_ticket_number_seq')")).scalar_one()
        session.rollback()
    assert next_ticket == 3001

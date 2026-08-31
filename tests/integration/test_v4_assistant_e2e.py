import os
import threading
import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.agent.leave_models import LeavePreparationStatus, LeaveRequestDraft
from app.agent.loop_models import AgentRunResult, AgentRunStatus
from app.api.application import create_app
from app.api.assistant_application import AssistantApplicationService
from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.workflow.action_creation import ActionCreationDisposition, ActionCreationService
from app.workflow.confirmation import ConfirmationService
from app.workflow.confirmed_poller import ConfirmedActionPoller
from app.workflow.domain import WorkflowState
from app.workflow.orchestration import WorkflowOrchestrationService

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]

ALEX = DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"]
JORDAN = DEMO_IDENTITY_BINDINGS["demo-v1-3b8e6d50"]
ALEX_HEADERS = {"X-Demo-Session": "demo-v1-7f4c2a91"}
JORDAN_HEADERS = {"X-Demo-Session": "demo-v1-3b8e6d50"}


def _replace_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[KnowledgeSettings]:
    live = load_knowledge_settings()
    admin_url = live.database_url.get_secret_value()
    database_name = f"knowledge_agent_v4_ae_{uuid.uuid4().hex[:12]}"
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


class ScriptedAgent:
    def __init__(self, result: AgentRunResult) -> None:
        self.result = result
        self.messages: list[str] = []

    def run(self, message: str, context) -> AgentRunResult:
        self.messages.append(message)
        return self.result


def _draft(
    *,
    start: date = date(2026, 10, 21),
    end: date | None = None,
    reason: str = "Family visit",
    requested_hours: Decimal = Decimal("7.60"),
    scheduled_work_days: int = 1,
) -> LeaveRequestDraft:
    return LeaveRequestDraft(
        leave_type="annual",
        start_date=start,
        end_date=end or start,
        scheduled_work_days=scheduled_work_days,
        requested_hours=requested_hours,
        current_balance_hours=Decimal("76.00"),
        projected_balance_hours=Decimal("68.40"),
        preparation_status=LeavePreparationStatus.READY,
        reason=reason,
        public_holiday_check_required=True,
        non_executing=True,
    )


def _completed(*, draft: LeaveRequestDraft | None, answer: str = "Prepared.") -> AgentRunResult:
    return AgentRunResult(
        status=AgentRunStatus.COMPLETED,
        answer=answer,
        citations=(),
        prepared_leave_request=draft,
        tool_calls_attempted=1 if draft is not None else 0,
        model_rounds=1,
    )


def _client(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    agent: ScriptedAgent,
    *,
    orchestration: WorkflowOrchestrationService | None = None,
    action_creation=None,
) -> TestClient:
    app = create_app(agent_service=agent)  # type: ignore[arg-type]
    app.state.workflow_settings = isolated_settings
    app.state.workflow_session_factory = session_factory
    actions = action_creation or ActionCreationService(session_factory, isolated_settings)
    app.state.action_creation_service = actions
    app.state.confirmation_service = ConfirmationService(session_factory, isolated_settings)
    if orchestration is not None or action_creation is not None:
        app.state.assistant_application_service = AssistantApplicationService(
            agent,  # type: ignore[arg-type]
            actions,
            session_factory=session_factory,
            settings=isolated_settings,
            orchestration=orchestration,
        )
    return TestClient(app, raise_server_exceptions=False)


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def test_prepared_leave_creates_action_matching_get_draft(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    agent = ScriptedAgent(_completed(draft=_draft(), answer="I prepared 80 hours."))
    client = _client(isolated_settings, session_factory, agent)
    response = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare annual leave for 21 October."},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["prepared_action"]["requested_hours"] == 7.6
    assert payload["prepared_action"]["authority"] == "preview"
    assert payload["action_status"] == "created"
    assert payload["action_not_created_reason"] is None
    action = payload["action"]
    assert action["revision"] == 1
    assert action["state"] == WorkflowState.AWAITING_CONFIRMATION.value
    assert action["confirmation_required"] is True
    assert action["authority"] == "authoritative"
    assert action["draft"]["requested_hours"] == "7.60"
    assert action["action_id"] not in payload["answer"]
    fetched = client.get(f"/api/v1/actions/{action['action_id']}", headers=ALEX_HEADERS)
    assert fetched.status_code == 200
    assert fetched.json()["draft"] == action["draft"]
    assert fetched.json()["action_id"] == action["action_id"]
    UUID(action["action_id"])
    assert _count(engine, "action_workflows") == 1
    assert _count(engine, "confirmation_challenges") == 0
    assert "confirmation_token" not in response.text
    assert "execution_key" not in response.text


def test_readonly_unsupported_and_chat_yes_create_no_action(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    agent = ScriptedAgent(_completed(draft=None, answer="Twenty days of annual leave."))
    client = _client(isolated_settings, session_factory, agent)
    read_only = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "What is the annual leave policy?"},
    )
    yes = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Yes, submit it."},
    )
    assert read_only.json()["action"] is None
    assert read_only.json()["action_status"] is None
    assert read_only.json()["action_not_created_reason"] is None
    assert yes.json()["action"] is None
    assert yes.json()["action_status"] is None
    assert _count(engine, "action_workflows") == 0
    assert _count(engine, "leave_requests") == 0


def test_non_executable_holiday_prepare_creates_no_action(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    agent = ScriptedAgent(_completed(draft=_draft(start=date(2026, 9, 25))))
    client = _client(isolated_settings, session_factory, agent)
    response = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare leave on the AFL Friday."},
    )
    assert response.status_code == 200
    assert response.json()["prepared_action"] is not None
    assert response.json()["prepared_action"]["authority"] == "preview"
    assert response.json()["action"] is None
    assert response.json()["action_status"] == "not_created"
    assert response.json()["action_not_created_reason"] == "no_scheduled_work"
    assert _count(engine, "action_workflows") == 0


def test_repeated_and_concurrent_prepare_reuse_one_live_action(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    agent = ScriptedAgent(_completed(draft=_draft(start=date(2026, 11, 5))))
    client = _client(isolated_settings, session_factory, agent)
    first = client.post(
        "/api/v1/assistant/query", headers=ALEX_HEADERS, json={"message": "Prepare 5 Nov."}
    )
    second = client.post(
        "/api/v1/assistant/query", headers=ALEX_HEADERS, json={"message": "Prepare 5 Nov again."}
    )
    assert first.json()["action"]["action_id"] == second.json()["action"]["action_id"]
    assert second.json()["action_status"] == "reused"
    results: list[str] = []
    start = threading.Barrier(2)

    def worker() -> None:
        start.wait()
        created = ActionCreationService(session_factory, isolated_settings).create_or_reuse(
            ALEX, _draft(start=date(2026, 11, 5))
        )
        assert created.action_id is not None
        results.append(str(created.action_id))

    threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert set(results) == {first.json()["action"]["action_id"]}
    assert _count(engine, "action_workflows") == 1


def test_wrong_employee_cannot_reuse_or_confirm(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    agent = ScriptedAgent(_completed(draft=_draft(start=date(2026, 11, 5))))
    client = _client(isolated_settings, session_factory, agent)
    alex = client.post(
        "/api/v1/assistant/query", headers=ALEX_HEADERS, json={"message": "Prepare 5 Nov."}
    )
    jordan = client.post(
        "/api/v1/assistant/query", headers=JORDAN_HEADERS, json={"message": "Prepare 5 Nov."}
    )
    assert jordan.json()["action"]["action_id"] != alex.json()["action"]["action_id"]
    action_id = alex.json()["action"]["action_id"]
    forbidden = client.get(f"/api/v1/actions/{action_id}", headers=JORDAN_HEADERS)
    assert forbidden.status_code == 404
    challenge = client.post(
        f"/api/v1/actions/{action_id}/confirmation-challenges",
        headers=JORDAN_HEADERS,
    )
    assert challenge.status_code == 404


def test_full_offline_prepare_confirm_execute_and_replay(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    agent = ScriptedAgent(_completed(draft=_draft(start=date(2026, 10, 21))))
    client = _client(isolated_settings, session_factory, agent)
    prepared = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare annual leave for 21 October."},
    )
    action_id = prepared.json()["action"]["action_id"]
    issued = client.post(
        f"/api/v1/actions/{action_id}/confirmation-challenges",
        headers=ALEX_HEADERS,
    )
    assert issued.status_code == 200
    token = issued.json()["confirmation_token"]
    challenge_id = issued.json()["challenge_id"]
    confirmed = client.post(
        f"/api/v1/actions/{action_id}/confirm",
        headers=ALEX_HEADERS,
        json={"challenge_id": challenge_id, "confirmation_token": token},
    )
    assert confirmed.json()["state"] == WorkflowState.CONFIRMED.value
    worker = ConfirmedActionPoller(session_factory, isolated_settings, worker_id="e2e-worker")
    first = worker.run_once()
    assert first is not None
    assert first.observed_state == WorkflowState.SUCCEEDED.value
    assert _count(engine, "action_workflows") == 1
    assert _count(engine, "action_execution_ledger") == 0
    assert _count(engine, "leave_requests") == 1
    replay_confirm = client.post(
        f"/api/v1/actions/{action_id}/confirm",
        headers=ALEX_HEADERS,
        json={"challenge_id": challenge_id, "confirmation_token": token},
    )
    assert replay_confirm.json()["state"] == WorkflowState.SUCCEEDED.value
    assert worker.run_once() is None
    again = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare annual leave for 21 October again."},
    )
    assert again.json()["action"]["action_id"] == action_id
    assert again.json()["action_status"] == "reused"
    assert _count(engine, "leave_requests") == 1


def test_create_without_checkpoint_reuses_action_and_init_is_retryable(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    created = ActionCreationService(session_factory, isolated_settings).create_or_reuse(
        ALEX, _draft(start=date(2026, 11, 9))
    )
    assert created.disposition is ActionCreationDisposition.CREATED
    assert _count(engine, "checkpoints") == 0
    reused = ActionCreationService(session_factory, isolated_settings).create_or_reuse(
        ALEX, _draft(start=date(2026, 11, 9))
    )
    assert reused.action_id == created.action_id
    assert reused.draft == created.draft
    assert _count(engine, "action_workflows") == 1
    assert _count(engine, "checkpoints") == 0
    confirmation = ConfirmationService(session_factory, isolated_settings)
    issued = confirmation.issue_challenge(action_id=created.action_id, context=ALEX)
    confirmation.confirm(
        action_id=created.action_id,
        challenge_id=issued.challenge_id,
        confirmation_token=issued.confirmation_token,
        context=ALEX,
    )
    result = ConfirmedActionPoller(
        session_factory, isolated_settings, worker_id="retry-init"
    ).run_once()
    assert result is not None
    assert result.observed_state == WorkflowState.SUCCEEDED.value
    assert _count(engine, "leave_requests") == 1


def test_expired_and_cancelled_generated_actions_cannot_execute(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    agent = ScriptedAgent(_completed(draft=_draft(start=date(2026, 11, 10))))
    client = _client(isolated_settings, session_factory, agent)
    expire_id = client.post(
        "/api/v1/assistant/query", headers=ALEX_HEADERS, json={"message": "Prepare 10 Nov."}
    ).json()["action"]["action_id"]
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_revisions
                SET action_expires_at = clock_timestamp() - interval '1 second'
                WHERE action_id = :action_id
                """
            ),
            {"action_id": expire_id},
        )
        session.commit()
    expired_challenge = client.post(
        f"/api/v1/actions/{expire_id}/confirmation-challenges",
        headers=ALEX_HEADERS,
    )
    assert expired_challenge.status_code == 409
    cancel_agent = ScriptedAgent(_completed(draft=_draft(start=date(2026, 11, 11))))
    cancel_client = _client(isolated_settings, session_factory, cancel_agent)
    cancel_id = cancel_client.post(
        "/api/v1/assistant/query", headers=ALEX_HEADERS, json={"message": "Prepare 11 Nov."}
    ).json()["action"]["action_id"]
    cancelled = cancel_client.post(f"/api/v1/actions/{cancel_id}/cancel", headers=ALEX_HEADERS)
    assert cancelled.json()["state"] == WorkflowState.CANCELLED.value
    assert (
        cancel_client.post(
            f"/api/v1/actions/{cancel_id}/confirmation-challenges",
            headers=ALEX_HEADERS,
        ).status_code
        == 409
    )
    assert _count(engine, "leave_requests") == 0


@pytest.mark.skip(reason="retired after simplified execution cutover")
def test_unknown_blocks_replacement_and_calendar_drift_stales(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    unknown = ActionCreationService(session_factory, isolated_settings).create_or_reuse(
        ALEX, _draft(start=date(2026, 11, 12))
    )
    with session_factory() as session:
        session.execute(
            text("UPDATE action_revisions SET state = :state WHERE action_id = :action_id"),
            {"state": WorkflowState.UNKNOWN_OUTCOME.value, "action_id": unknown.action_id},
        )
        session.commit()
    blocked = ActionCreationService(session_factory, isolated_settings).create_or_reuse(
        ALEX, _draft(start=date(2026, 11, 12))
    )
    assert blocked.action_id == unknown.action_id
    assert blocked.disposition is ActionCreationDisposition.RETURNED_IN_FLIGHT
    assert _count(engine, "action_workflows") == 1

    drift = ActionCreationService(session_factory, isolated_settings).create_or_reuse(
        ALEX, _draft(start=date(2026, 11, 13))
    )
    WorkflowOrchestrationService(session_factory).ensure_started(
        action_id=drift.action_id,
        owner_subject_id=ALEX.subject_id or "",
        settings=isolated_settings,
    )
    with session_factory() as session:
        session.execute(
            text(
                """
                UPDATE action_revisions
                SET calendar_version = 'AU-VIC-2025-v1'
                WHERE action_id = :action_id
                """
            ),
            {"action_id": drift.action_id},
        )
        session.commit()
    confirmation = ConfirmationService(session_factory, isolated_settings)
    issued = confirmation.issue_challenge(action_id=drift.action_id, context=ALEX)
    confirmation.confirm(
        action_id=drift.action_id,
        challenge_id=issued.challenge_id,
        confirmation_token=issued.confirmation_token,
        context=ALEX,
    )
    worker = ConfirmedActionPoller(
        session_factory, isolated_settings, worker_id="drift-worker"
    ).run_once()
    assert worker is not None
    assert worker.observed_state == WorkflowState.STALE.value
    assert _count(engine, "leave_requests") == 0


def test_succeeded_balance_and_overlap_affect_later_actions(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    first = ActionCreationService(session_factory, isolated_settings).create_or_reuse(
        ALEX, _draft(start=date(2026, 10, 21))
    )
    confirmation = ConfirmationService(session_factory, isolated_settings)
    issued = confirmation.issue_challenge(action_id=first.action_id, context=ALEX)
    confirmation.confirm(
        action_id=first.action_id,
        challenge_id=issued.challenge_id,
        confirmation_token=issued.confirmation_token,
        context=ALEX,
    )
    done = ConfirmedActionPoller(
        session_factory, isolated_settings, worker_id="bal-worker"
    ).run_once()
    assert done is not None
    assert done.observed_state == WorkflowState.SUCCEEDED.value
    long = ActionCreationService(session_factory, isolated_settings).create_or_reuse(
        ALEX, _draft(start=date(2026, 10, 26), end=date(2026, 11, 9))
    )
    assert long.disposition is ActionCreationDisposition.NOT_CREATED
    assert long.ineligibility_reason == "insufficient_balance"
    overlap = ActionCreationService(session_factory, isolated_settings).create_or_reuse(
        ALEX, _draft(start=date(2026, 10, 20), end=date(2026, 10, 22))
    )
    assert overlap.disposition is ActionCreationDisposition.CREATED
    overlap_issued = confirmation.issue_challenge(action_id=overlap.action_id, context=ALEX)
    confirmation.confirm(
        action_id=overlap.action_id,
        challenge_id=overlap_issued.challenge_id,
        confirmation_token=overlap_issued.confirmation_token,
        context=ALEX,
    )
    overlap_result = ConfirmedActionPoller(
        session_factory, isolated_settings, worker_id="overlap-worker"
    ).run_once()
    assert overlap_result is not None
    assert overlap_result.observed_state == WorkflowState.EXECUTION_FAILED.value
    assert _count(engine, "leave_requests") == 1
    assert _count(engine, "action_workflows") == 2


def _assert_no_confirmation_or_execution(engine: Engine) -> None:
    assert _count(engine, "confirmation_challenges") == 0
    assert _count(engine, "workflow_outbox") == 0
    assert _count(engine, "action_execution_ledger") == 0
    assert _count(engine, "leave_requests") == 0


def test_reused_action_keeps_persisted_reason_not_new_preview(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    first_agent = ScriptedAgent(
        _completed(draft=_draft(start=date(2026, 11, 5), reason="appointment"))
    )
    client = _client(isolated_settings, session_factory, first_agent)
    first = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare 5 Nov for an appointment."},
    )
    action_id = first.json()["action"]["action_id"]
    assert first.json()["action"]["draft"]["reason"] == "appointment"
    first_agent.result = _completed(
        draft=_draft(start=date(2026, 11, 5), reason="family trip"),
        answer="Prepared again for a family trip.",
    )
    second = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare 5 Nov for a family trip."},
    )
    payload = second.json()
    assert payload["prepared_action"]["reason"] == "family trip"
    assert payload["prepared_action"]["authority"] == "preview"
    assert payload["action"]["action_id"] == action_id
    assert payload["action"]["draft"]["reason"] == "appointment"
    assert payload["action"]["authority"] == "authoritative"
    assert payload["action_status"] == "reused"
    fetched = client.get(f"/api/v1/actions/{action_id}", headers=ALEX_HEADERS)
    assert fetched.json()["draft"]["reason"] == "appointment"
    assert fetched.json()["draft"] == payload["action"]["draft"]
    assert _count(engine, "action_workflows") == 1
    _assert_no_confirmation_or_execution(engine)


def test_reused_action_keeps_persisted_hours_not_preview_hours(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    first_agent = ScriptedAgent(
        _completed(
            draft=_draft(
                start=date(2026, 9, 24),
                end=date(2026, 9, 25),
                reason="appointment",
                requested_hours=Decimal("15.20"),
                scheduled_work_days=2,
            )
        )
    )
    client = _client(isolated_settings, session_factory, first_agent)
    first = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare 24-25 Sep."},
    )
    action_id = first.json()["action"]["action_id"]
    assert first.json()["action"]["draft"]["requested_hours"] == "7.60"
    first_agent.result = _completed(
        draft=_draft(
            start=date(2026, 9, 24),
            end=date(2026, 9, 25),
            reason="family trip",
            requested_hours=Decimal("15.20"),
            scheduled_work_days=2,
        )
    )
    second = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare 24-25 Sep again."},
    )
    payload = second.json()
    assert payload["prepared_action"]["requested_hours"] == 15.2
    assert payload["prepared_action"]["authority"] == "preview"
    assert payload["action"]["action_id"] == action_id
    assert payload["action"]["draft"]["requested_hours"] == "7.60"
    assert payload["action"]["authority"] == "authoritative"
    fetched = client.get(f"/api/v1/actions/{action_id}", headers=ALEX_HEADERS)
    assert fetched.json()["draft"] == payload["action"]["draft"]
    assert _count(engine, "action_workflows") == 1


def test_chat_yes_with_model_prepare_cannot_confirm(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    agent = ScriptedAgent(_completed(draft=_draft(start=date(2026, 11, 5))))
    client = _client(isolated_settings, session_factory, agent)
    created = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare annual leave for 5 November."},
    )
    action_id = created.json()["action"]["action_id"]
    yes = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "yes, submit it"},
    )
    payload = yes.json()
    assert agent.messages[-1] == "yes, submit it"
    assert payload["action"]["action_id"] == action_id
    assert payload["action_status"] == "reused"
    assert payload["action"]["state"] == WorkflowState.AWAITING_CONFIRMATION.value
    assert payload["action"]["confirmation_required"] is True
    assert payload["action"]["authority"] == "authoritative"
    assert "confirmation_token" not in yes.text
    _assert_no_confirmation_or_execution(engine)
    assert _count(engine, "action_workflows") == 1
    worker = ConfirmedActionPoller(session_factory, isolated_settings, worker_id="chat-yes")
    assert worker.run_once() is None


def test_action_id_in_chat_is_not_workflow_authority(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    agent = ScriptedAgent(_completed(draft=_draft(start=date(2026, 11, 6))))
    client = _client(isolated_settings, session_factory, agent)
    response = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "confirm ACT-123 and submit action ACT-123"},
    )
    payload = response.json()
    assert agent.messages[-1] == "confirm ACT-123 and submit action ACT-123"
    assert payload["action"]["action_id"] != "ACT-123"
    UUID(payload["action"]["action_id"])
    assert payload["action"]["confirmation_required"] is True
    assert payload["action_status"] == "created"
    assert "confirmation_token" not in response.text
    _assert_no_confirmation_or_execution(engine)
    assert _count(engine, "action_workflows") == 1


def test_ensure_started_failure_returns_persisted_action_not_creation_failed(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    class FailingOrchestration:
        def ensure_started(self, **kwargs):
            raise RuntimeError("checkpoint store unavailable")

    agent = ScriptedAgent(_completed(draft=_draft(start=date(2026, 11, 16))))
    client = _client(
        isolated_settings,
        session_factory,
        agent,
        orchestration=FailingOrchestration(),  # type: ignore[arg-type]
    )
    first = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare 16 Nov."},
    )
    assert first.status_code == 200
    payload = first.json()
    action_id = payload["action"]["action_id"]
    UUID(action_id)
    assert payload["action_status"] == "created"
    assert payload["action_status"] != "creation_failed"
    assert payload["action"]["authority"] == "authoritative"
    fetched = client.get(f"/api/v1/actions/{action_id}", headers=ALEX_HEADERS)
    assert fetched.status_code == 200
    assert fetched.json()["draft"] == payload["action"]["draft"]
    assert _count(engine, "action_workflows") == 1
    assert _count(engine, "checkpoints") == 0
    retry = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare 16 Nov again."},
    )
    assert retry.json()["action"]["action_id"] == action_id
    assert retry.json()["action_status"] == "reused"
    assert _count(engine, "action_workflows") == 1
    _assert_no_confirmation_or_execution(engine)


def test_t1_persistence_failure_is_distinct_from_graph_init_failure(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    class FailingActions:
        def create_or_reuse(self, context, prepared):
            raise SQLAlchemyError("disk full")

    agent = ScriptedAgent(_completed(draft=_draft(start=date(2026, 11, 17))))
    client = _client(
        isolated_settings,
        session_factory,
        agent,
        action_creation=FailingActions(),
    )
    response = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare 17 Nov."},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["prepared_action"] is not None
    assert payload["action"] is None
    assert payload["action_status"] == "creation_failed"
    assert payload["action_not_created_reason"] is None
    assert _count(engine, "action_workflows") == 0
    assert _count(engine, "action_revisions") == 0


def test_lost_response_retry_reuses_live_action_without_side_effects(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    agent = ScriptedAgent(_completed(draft=_draft(start=date(2026, 11, 18))))
    client = _client(isolated_settings, session_factory, agent)
    first = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare annual leave for 18 November."},
    )
    action_id = first.json()["action"]["action_id"]
    retry = client.post(
        "/api/v1/assistant/query",
        headers=ALEX_HEADERS,
        json={"message": "Prepare annual leave for 18 November."},
    )
    assert retry.json()["action"]["action_id"] == action_id
    assert retry.json()["action_status"] == "reused"
    assert retry.json()["action"]["confirmation_required"] is True
    assert retry.json()["action"]["draft"] == first.json()["action"]["draft"]
    assert _count(engine, "action_workflows") == 1
    _assert_no_confirmation_or_execution(engine)

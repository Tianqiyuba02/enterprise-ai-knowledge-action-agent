import os
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.application import create_app
from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.config import KnowledgeSettings, load_knowledge_settings
from app.db.session import create_knowledge_engine, create_knowledge_session_factory
from app.errors import ActionConflictError, ActionNotFoundError, ConfirmationInvalidError
from app.workflow.authority import AuthoritySnapshot, CanonicalDraft
from app.workflow.calendar import V4_CALENDAR_VERSION
from app.workflow.canonical import business_request_key
from app.workflow.challenge_repository import ChallengeRepository
from app.workflow.confirmation import ConfirmationService
from app.workflow.domain import ActionType, ChallengeStatus, LeaveType, WorkflowState
from app.workflow.orchestration import WorkflowOrchestrationService
from app.workflow.tokens import hash_confirmation_token
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
SAM = DEMO_IDENTITY_BINDINGS["demo-v1-3b8e6d50"]
ALEX_HEADERS = {"X-Demo-Session": "demo-v1-7f4c2a91"}
SAM_HEADERS = {"X-Demo-Session": "demo-v1-3b8e6d50"}


def _replace_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[KnowledgeSettings]:
    live = load_knowledge_settings()
    admin_url = live.database_url.get_secret_value()
    database_name = f"knowledge_agent_v4_cf_{uuid.uuid4().hex[:12]}"
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


@pytest.fixture
def service(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> ConfirmationService:
    return ConfirmationService(session_factory, isolated_settings)


@pytest.fixture
def client(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> Iterator[TestClient]:
    app = create_app()
    app.state.workflow_settings = isolated_settings
    app.state.workflow_session_factory = session_factory
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _create_action(
    session: Session,
    *,
    owner_subject_id: str = ALEX.subject_id or "",
    owner_employee_id: str = "EMP-1001",
    start: date = date(2026, 9, 1),
    expires_at: datetime | None = None,
) -> UUID:
    snapshot = AuthoritySnapshot(
        employee_id=owner_employee_id,
        jurisdiction="AU-VIC",
        work_days=("monday", "tuesday", "wednesday", "thursday", "friday"),
        hours_per_day=Decimal("7.60"),
        timezone="Australia/Melbourne",
        trusted_base_balance_hours=Decimal("76.00"),
        committed_submitted_hours=Decimal("0.00"),
        effective_available_hours=Decimal("76.00"),
        calendar_version=V4_CALENDAR_VERSION,
        ruleset_version="v4-annual-leave-1",
    )
    draft = CanonicalDraft(
        action_type=ActionType.SUBMIT_ANNUAL_LEAVE.value,
        leave_type=LeaveType.ANNUAL.value,
        start_date=start,
        end_date=start,
        requested_hours=Decimal("7.60"),
        projected_balance_hours=Decimal("68.40"),
        readiness="ready",
        reason="Family visit",
        calendar_version=V4_CALENDAR_VERSION,
        ruleset_version="v4-annual-leave-1",
        authority_snapshot_hash=snapshot.fingerprint(),
    )
    workflow, _revision = WorkflowRepository().create_workflow_and_revision(
        session,
        NewWorkflowRevision(
            owner_subject_id=owner_subject_id,
            owner_employee_id=owner_employee_id,
            jurisdiction="AU-VIC",
            action_type=ActionType.SUBMIT_ANNUAL_LEAVE,
            state=WorkflowState.AWAITING_CONFIRMATION,
            draft_payload={"leave_type": "annual", "reason": "Family visit"},
            draft_hash=draft.fingerprint(),
            authority_snapshot_hash=snapshot.fingerprint(),
            business_request_key=business_request_key(
                employee_id=owner_employee_id,
                leave_type="annual",
                start_date=start,
                end_date=start,
            ),
            ruleset_version="v4-annual-leave-1",
            calendar_version=V4_CALENDAR_VERSION,
            action_expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
            langgraph_thread_id=str(uuid.uuid4()),
        ),
    )
    return workflow.action_id


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


def test_owner_read_issue_confirm_replay_and_isolation(
    client: TestClient,
    service: ConfirmationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
    isolated_settings: KnowledgeSettings,
) -> None:
    with session_factory() as session:
        action_id = _create_action(session)
        session.commit()

    own = client.get(f"/api/v1/actions/{action_id}", headers=ALEX_HEADERS)
    other = client.get(f"/api/v1/actions/{action_id}", headers=SAM_HEADERS)
    missing = client.get(
        f"/api/v1/actions/{uuid.uuid4()}",
        headers=ALEX_HEADERS,
    )
    assert own.status_code == 200
    assert own.json()["draft"]["reason"] == "Family visit"
    assert own.json()["confirmation_required"] is True
    assert "token_hash" not in own.json()
    assert other.status_code == missing.status_code == 404
    assert other.json()["error_code"] == missing.json()["error_code"] == "action_not_found"

    first = client.post(
        f"/api/v1/actions/{action_id}/confirmation-challenges",
        headers=ALEX_HEADERS,
    )
    second = client.post(
        f"/api/v1/actions/{action_id}/confirmation-challenges",
        headers=ALEX_HEADERS,
    )
    assert first.status_code == second.status_code == 200
    token = second.json()["confirmation_token"]
    challenge_id = second.json()["challenge_id"]
    assert token != first.json()["confirmation_token"]
    with session_factory() as session:
        active = ChallengeRepository().get_active_challenge(session, action_id=action_id)
        rows = session.execute(text("SELECT token_hash, status FROM confirmation_challenges")).all()
    assert active is not None
    assert active.status == ChallengeStatus.ACTIVE.value
    assert sum(1 for row in rows if row.status == ChallengeStatus.ACTIVE.value) == 1
    assert all(token not in (row.token_hash or "") for row in rows)
    assert hash_confirmation_token(token) == active.token_hash

    sam_issue = client.post(
        f"/api/v1/actions/{action_id}/confirmation-challenges",
        headers=SAM_HEADERS,
    )
    sam_confirm = client.post(
        f"/api/v1/actions/{action_id}/confirm",
        headers=SAM_HEADERS,
        json={"challenge_id": challenge_id, "confirmation_token": token},
    )
    sam_cancel = client.post(f"/api/v1/actions/{action_id}/cancel", headers=SAM_HEADERS)
    assert {sam_issue.status_code, sam_confirm.status_code, sam_cancel.status_code} == {404}

    wrong_token = client.post(
        f"/api/v1/actions/{action_id}/confirm",
        headers=ALEX_HEADERS,
        json={"challenge_id": challenge_id, "confirmation_token": "0" * 64},
    )
    superseded = client.post(
        f"/api/v1/actions/{action_id}/confirm",
        headers=ALEX_HEADERS,
        json={
            "challenge_id": first.json()["challenge_id"],
            "confirmation_token": first.json()["confirmation_token"],
        },
    )
    assert wrong_token.status_code == superseded.status_code == 409
    assert wrong_token.json()["error_code"] == "confirmation_invalid"

    graph = WorkflowOrchestrationService(session_factory)
    graph.start(
        action_id=action_id,
        owner_subject_id=ALEX.subject_id or "",
        settings=isolated_settings,
    )
    graph.resume(
        action_id=action_id,
        owner_subject_id=ALEX.subject_id or "",
        resume_payload={"confirmed": True, "execute": True},
        settings=isolated_settings,
    )
    still = service.get_action(action_id=action_id, context=ALEX)
    assert still.state == WorkflowState.AWAITING_CONFIRMATION.value

    confirmed = client.post(
        f"/api/v1/actions/{action_id}/confirm",
        headers=ALEX_HEADERS,
        json={"challenge_id": challenge_id, "confirmation_token": token},
    )
    replay = client.post(
        f"/api/v1/actions/{action_id}/confirm",
        headers=ALEX_HEADERS,
        json={"challenge_id": challenge_id, "confirmation_token": token},
    )
    replay_wrong = client.post(
        f"/api/v1/actions/{action_id}/confirm",
        headers=ALEX_HEADERS,
        json={"challenge_id": challenge_id, "confirmation_token": "1" * 64},
    )
    assert confirmed.status_code == replay.status_code == 200
    assert confirmed.json()["state"] == replay.json()["state"] == WorkflowState.CONFIRMED.value
    assert confirmed.json()["confirmed_expires_at"] is not None
    assert replay_wrong.status_code == 409
    assert replay_wrong.json()["error_code"] == "confirmation_invalid"
    with session_factory() as session:
        consumed = ChallengeRepository().get(session, UUID(challenge_id))
        outbox = session.execute(text("SELECT event_key FROM workflow_outbox")).scalars().all()
        audits = session.execute(
            text("SELECT event_type, safe_metadata FROM action_audit_events")
        ).all()
    assert consumed is not None
    assert consumed.status == ChallengeStatus.CONSUMED.value
    assert outbox == []
    assert token not in str(audits)
    assert _count(engine, "leave_requests") == 0
    assert _count(engine, "action_execution_ledger") == 0
    assert _count(engine, "public_holidays") == 14


def test_session_mismatch_expired_challenge_and_expired_action(
    service: ConfirmationService,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        action_id = _create_action(session, start=date(2026, 9, 2))
        expired_action_id = _create_action(
            session,
            start=date(2026, 9, 3),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        session.commit()

    issued = service.issue_challenge(action_id=action_id, context=ALEX)
    with session_factory() as session:
        live = ChallengeRepository().get(session, issued.challenge_id)
        assert live is not None
        live.confirmation_session_id = "sess_not_current"
        session.commit()
    with pytest.raises(ConfirmationInvalidError):
        service.confirm(
            action_id=action_id,
            challenge_id=issued.challenge_id,
            confirmation_token=issued.confirmation_token,
            context=ALEX,
        )
    with session_factory() as session:
        live = ChallengeRepository().get(session, issued.challenge_id)
        assert live is not None
        live.confirmation_session_id = ALEX.session_id or ""
        live.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()
    with pytest.raises(ConfirmationInvalidError):
        service.confirm(
            action_id=action_id,
            challenge_id=issued.challenge_id,
            confirmation_token=issued.confirmation_token,
            context=ALEX,
        )
    replacement = service.issue_challenge(action_id=action_id, context=ALEX)
    assert replacement.challenge_id != issued.challenge_id

    expired = service.get_action(action_id=expired_action_id, context=ALEX)
    assert expired.state == WorkflowState.EXPIRED.value
    with pytest.raises(ActionConflictError):
        service.issue_challenge(action_id=expired_action_id, context=ALEX)

    with session_factory() as session:
        soon_id = _create_action(session, start=date(2026, 9, 11))
        session.commit()
    soon_issued = service.issue_challenge(action_id=soon_id, context=ALEX)
    with session_factory() as session:
        revision = WorkflowRepository().get_revision(session, soon_id)
        assert revision is not None
        revision.action_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()
    with pytest.raises(ActionConflictError):
        service.confirm(
            action_id=soon_id,
            challenge_id=soon_issued.challenge_id,
            confirmation_token=soon_issued.confirmation_token,
            context=ALEX,
        )


def test_challenge_ttl_does_not_exceed_action_expiry(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    settings = isolated_settings.model_copy(update={"v4_confirmation_challenge_ttl_seconds": 600})
    service = ConfirmationService(session_factory, settings)
    expires_at = datetime.now(UTC) + timedelta(seconds=30)
    with session_factory() as session:
        action_id = _create_action(session, start=date(2026, 9, 4), expires_at=expires_at)
        session.commit()
    issued = service.issue_challenge(action_id=action_id, context=ALEX)
    assert issued.expires_at <= expires_at


def test_cancel_before_and_after_confirm(
    service: ConfirmationService,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    with session_factory() as session:
        before_id = _create_action(session, start=date(2026, 9, 5))
        after_id = _create_action(session, start=date(2026, 9, 6))
        succeeded_id = _create_action(session, start=date(2026, 9, 7))
        WorkflowRepository().apply_revision_state(
            session, action_id=succeeded_id, state=WorkflowState.SUCCEEDED
        )
        session.commit()

    issued = service.issue_challenge(action_id=before_id, context=ALEX)
    cancelled = service.cancel(action_id=before_id, context=ALEX)
    again = service.cancel(action_id=before_id, context=ALEX)
    assert cancelled.state == again.state == WorkflowState.CANCELLED.value
    with pytest.raises(ActionConflictError):
        service.confirm(
            action_id=before_id,
            challenge_id=issued.challenge_id,
            confirmation_token=issued.confirmation_token,
            context=ALEX,
        )
    with session_factory() as session:
        active = ChallengeRepository().get_active_challenge(session, action_id=before_id)
    assert active is None

    later = service.issue_challenge(action_id=after_id, context=ALEX)
    confirmed = service.confirm(
        action_id=after_id,
        challenge_id=later.challenge_id,
        confirmation_token=later.confirmation_token,
        context=ALEX,
    )
    cancelled_after = service.cancel(action_id=after_id, context=ALEX)
    assert confirmed.state == WorkflowState.CONFIRMED.value
    assert cancelled_after.state == WorkflowState.CANCELLED.value
    with session_factory() as session:
        outbox = session.execute(text("SELECT count(*) FROM workflow_outbox")).scalar_one()
        consumed = ChallengeRepository().get(session, later.challenge_id)
    assert outbox == 0
    assert consumed is not None
    assert consumed.status == ChallengeStatus.CONSUMED.value
    with pytest.raises(ActionConflictError):
        service.cancel(action_id=succeeded_id, context=ALEX)
    assert _count(engine, "leave_requests") == 0
    assert _count(engine, "action_execution_ledger") == 0


def test_confirm_and_audit_are_atomic(
    service: ConfirmationService,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        action_id = _create_action(session, start=date(2026, 9, 8))
        session.commit()
    issued = service.issue_challenge(action_id=action_id, context=ALEX)
    with (
        patch.object(service._audits, "insert", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        service.confirm(
            action_id=action_id,
            challenge_id=issued.challenge_id,
            confirmation_token=issued.confirmation_token,
            context=ALEX,
        )
    with session_factory() as session:
        revision = WorkflowRepository().get_revision(session, action_id)
        outbox = session.execute(text("SELECT count(*) FROM workflow_outbox")).scalar_one()
        challenge = ChallengeRepository().get(session, issued.challenge_id)
    assert revision is not None
    assert revision.state == WorkflowState.AWAITING_CONFIRMATION.value
    assert outbox == 0
    assert challenge is not None
    assert challenge.status == ChallengeStatus.ACTIVE.value


def test_concurrent_challenge_issuance_keeps_one_active(
    service: ConfirmationService,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        action_id = _create_action(session, start=date(2026, 9, 9))
        session.commit()
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def issue() -> None:
        barrier.wait()
        try:
            service.issue_challenge(action_id=action_id, context=ALEX)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    workers = [threading.Thread(target=issue) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert errors == []
    with session_factory() as session:
        active_count = session.execute(
            text(
                """
                SELECT count(*) FROM confirmation_challenges
                WHERE action_id = :action_id AND status = 'ACTIVE'
                """
            ),
            {"action_id": action_id},
        ).scalar_one()
    assert active_count == 1


def test_confirm_cancel_race_has_one_winner(
    service: ConfirmationService,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        action_id = _create_action(session, start=date(2026, 9, 10))
        session.commit()
    issued = service.issue_challenge(action_id=action_id, context=ALEX)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def confirm() -> None:
        barrier.wait()
        try:
            view = service.confirm(
                action_id=action_id,
                challenge_id=issued.challenge_id,
                confirmation_token=issued.confirmation_token,
                context=ALEX,
            )
            outcomes.append(view.state)
        except (ActionConflictError, ConfirmationInvalidError, ActionNotFoundError):
            outcomes.append("rejected")

    def cancel() -> None:
        barrier.wait()
        try:
            view = service.cancel(action_id=action_id, context=ALEX)
            outcomes.append(view.state)
        except ActionConflictError:
            outcomes.append("rejected")

    workers = [threading.Thread(target=confirm), threading.Thread(target=cancel)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    final = service.get_action(action_id=action_id, context=ALEX)
    assert final.state in {
        WorkflowState.CONFIRMED.value,
        WorkflowState.CANCELLED.value,
    }
    assert WorkflowState.EXECUTING.value not in outcomes

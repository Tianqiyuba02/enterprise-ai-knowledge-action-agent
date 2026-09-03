"""M2 PostgreSQL proofs for IT actions, revisions, execution, and isolation."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from isolated_postgres import isolated_settings_for_engine, isolated_test_engine
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.agent.dispatcher import ToolDispatcher
from app.agent.leave_models import LeavePreparationStatus, LeaveRequestDraft
from app.agent.loop_models import AgentModelTurn, AgentRequestedToolCall
from app.agent.service import AgentService
from app.api.application import create_app
from app.api.dependencies import DEMO_IDENTITY_BINDINGS
from app.config import KnowledgeSettings
from app.db.session import create_knowledge_session_factory
from app.errors import ActionConflictError, ConfirmationInvalidError
from app.grounding.models import GroundedAnswerDraft
from app.ingestion.models import EmbeddingProfile, IngestionOutcome
from app.ingestion.repository import KnowledgeIngestionRepository
from app.ingestion.service import KnowledgeIngestionService
from app.it.domain import (
    ITTicketCategory,
    ITTicketUrgency,
    PreparedITSupportTicket,
    ReviseITSupportTicketRequest,
)
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.query_service import KnowledgeQueryService
from app.knowledge.repository import KnowledgeRetrievalRepository
from app.knowledge.service import KnowledgeRetrievalService
from app.knowledge.vocabulary import AudienceGroup, Jurisdiction
from app.portal.service import PortalReadService
from app.repositories.demo import DemoRepository
from app.services.employee import EmployeeService
from app.services.it import ITService
from app.services.leave_preparation import LeavePreparationService
from app.workflow.action_creation import ActionCreationService
from app.workflow.atomic_execution import (
    AtomicConfirmedExecutor,
    AtomicExecutionFailpoints,
    AtomicOutcome,
)
from app.workflow.confirmation import ConfirmationService
from app.workflow.domain import WorkflowState
from app.workflow.it_action_creation import ITActionCreationService
from app.workflow.it_revision import ITActionRevisionService

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the PostgreSQL container running",
    ),
]

ALEX = DEMO_IDENTITY_BINDINGS["demo-v1-7f4c2a91"]
SAM = DEMO_IDENTITY_BINDINGS["demo-v1-3b8e6d50"]
ALEX_HEADERS = {"X-Demo-Session": "demo-v1-7f4c2a91"}
SAM_HEADERS = {"X-Demo-Session": "demo-v1-3b8e6d50"}


@pytest.fixture
def isolated_settings() -> Iterator[KnowledgeSettings]:
    with isolated_test_engine(prefix="knowledge_agent_v5_m2") as engine:
        yield isolated_settings_for_engine(engine)


@pytest.fixture
def engine(isolated_settings: KnowledgeSettings) -> Iterator[Engine]:
    from app.db.session import create_knowledge_engine

    value = create_knowledge_engine(isolated_settings)
    try:
        yield value
    finally:
        value.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return create_knowledge_session_factory(engine)


@pytest.fixture
def client(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> Iterator[TestClient]:
    app = create_app()
    app.state.workflow_settings = isolated_settings
    app.state.workflow_session_factory = session_factory
    with TestClient(app, raise_server_exceptions=False) as value:
        yield value


def _draft(
    *,
    summary: str = "Cannot connect to office Wi-Fi",
    description: str = "The laptop cannot join the synthetic office network.",
) -> PreparedITSupportTicket:
    return PreparedITSupportTicket(
        category=ITTicketCategory.NETWORK,
        summary=summary,
        description=description,
        urgency=ITTicketUrgency.MEDIUM,
    )


def _create(
    session_factory: sessionmaker[Session],
    settings: KnowledgeSettings,
    *,
    context=ALEX,
    initiation_id: UUID | None = None,
    draft: PreparedITSupportTicket | None = None,
):
    return ITActionCreationService(session_factory, settings).create_or_reuse(
        context,
        draft or _draft(),
        initiation_id or uuid4(),
    )


def _confirm(
    session_factory: sessionmaker[Session],
    settings: KnowledgeSettings,
    action_id: UUID,
    *,
    context=ALEX,
):
    confirmation = ConfirmationService(session_factory, settings)
    issued = confirmation.issue_challenge(action_id=action_id, context=context)
    return confirmation.confirm(
        action_id=action_id,
        challenge_id=issued.challenge_id,
        confirmation_token=issued.confirmation_token,
        context=context,
    )


def _count(engine: Engine, sql: str, **params: object) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(sql), params).scalar_one())


class _DeterministicITEmbedder:
    profile = EmbeddingProfile()

    def embed_documents(self, contents, *, title):
        assert title == "IT Support Request and Triage Guide"
        vectors = []
        for content in contents:
            vector = [0.0] * 768
            vector[0 if "## Urgency guidance" in content else 1] = 1.0
            vectors.append(tuple(vector))
        return tuple(vectors)


class _DeterministicITQueryEmbedder:
    def embed_query(self, query):
        assert "high urgency" in query
        return (1.0, *([0.0] * 767))


class _FixedClock:
    def today(self):
        return date(2026, 9, 3)


class _DeterministicITGrounder:
    def generate(self, question, referenced_evidence):
        assert "high urgency" in question
        assert referenced_evidence[0].evidence.doc_code == "SOP-IT-003"
        assert referenced_evidence[0].evidence.anchor == "urgency-guidance"
        return GroundedAnswerDraft(
            status="answered",
            answer="Use high urgency when essential work is blocked without a safe workaround.",
            evidence_refs=("E1",),
        )


def test_actual_sop_it_003_is_ingested_retrievable_and_policy_resolvable(
    engine: Engine,
) -> None:
    session_factory = create_knowledge_session_factory(engine)
    ingestion = KnowledgeIngestionService(
        repository=KnowledgeIngestionRepository(session_factory),
        embedder=_DeterministicITEmbedder(),
    )
    result = ingestion.ingest_file(Path("corpus/v2/13-it-support-request-guide.md"))
    applicability = KnowledgeApplicabilityContext(
        jurisdiction=Jurisdiction.AU_VIC,
        audience_groups=frozenset({AudienceGroup.ALL_EMPLOYEES, AudienceGroup.MELBOURNE_EMPLOYEES}),
    )
    retrieval = KnowledgeRetrievalService(
        embedder=_DeterministicITQueryEmbedder(),
        repository=KnowledgeRetrievalRepository(session_factory),
        clock=_FixedClock(),
    )
    response = KnowledgeQueryService(
        retrieval=retrieval,
        generator=_DeterministicITGrounder(),
    ).query("When should an IT request use high urgency?", applicability)
    destination = PortalReadService(session_factory, DemoRepository()).policy_document(
        "SOP-IT-003",
        "1.0",
        applicability,
        trusted_today=date(2026, 9, 3),
    )

    assert result.outcome is IngestionOutcome.INSERTED
    assert result.chunk_count == 5
    assert response.citations[0].model_dump() == {
        "doc_code": "SOP-IT-003",
        "title": "IT Support Request and Triage Guide",
        "version": "1.0",
        "section_anchor": "urgency-guidance",
        "page": None,
    }
    assert destination.doc_code == "SOP-IT-003"
    assert destination.version == "1.0"
    assert any(section.anchor == "urgency-guidance" for section in destination.sections)


def test_seeded_tickets_are_postgres_backed_listed_and_owner_isolated(
    client: TestClient,
) -> None:
    alex = client.get("/api/v1/me/tickets", headers=ALEX_HEADERS)
    sam = client.get("/api/v1/me/tickets", headers=SAM_HEADERS)
    own = client.get("/api/v1/me/tickets/TKT-1001", headers=ALEX_HEADERS)
    cross = client.get("/api/v1/me/tickets/TKT-2001", headers=ALEX_HEADERS)
    missing = client.get("/api/v1/me/tickets/TKT-999999", headers=ALEX_HEADERS)

    assert alex.status_code == sam.status_code == own.status_code == 200
    assert {item["ticket_id"] for item in alex.json()["items"]} == {"TKT-1001", "TKT-1002"}
    assert {item["ticket_id"] for item in sam.json()["items"]} == {"TKT-2001"}
    assert cross.status_code == missing.status_code == 404
    assert cross.json()["error_code"] == missing.json()["error_code"] == "ticket_not_found"


def test_prepare_retry_is_per_initiation_and_creates_no_ticket(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    initiation = uuid4()
    first = _create(session_factory, isolated_settings, initiation_id=initiation)
    retry = _create(session_factory, isolated_settings, initiation_id=initiation)
    separate = _create(session_factory, isolated_settings, draft=_draft())

    assert first.action_id == retry.action_id
    assert separate.action_id != first.action_id
    assert first.draft == retry.draft
    assert _count(engine, "SELECT count(*) FROM it_tickets WHERE source_action_id IS NOT NULL") == 0
    assert _count(engine, "SELECT count(*) FROM action_workflows") == 2


def test_natural_language_agent_prepare_persists_authoritative_it_action_only(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    class SessionTurns:
        def __init__(self) -> None:
            self.turn = 0

        def next(self, tool_responses=()):
            self.turn += 1
            if self.turn == 1:
                return AgentModelTurn(
                    requested_calls=(
                        AgentRequestedToolCall(
                            name="prepare_it_support_ticket",
                            arguments={
                                "category": "network",
                                "summary": "Cannot connect to office Wi-Fi",
                                "description": (
                                    "The laptop cannot join the synthetic office network."
                                ),
                                "urgency": "medium",
                            },
                            provider_call_id="m2-call-1",
                        ),
                    )
                )
            assert tool_responses
            return AgentModelTurn(final_text="I prepared an IT support draft for review.")

    class Provider:
        def start(self, user_message, trusted_today):
            assert "Wi-Fi" in user_message
            return SessionTurns()

    repository = DemoRepository()
    employees = EmployeeService(repository)
    agent = AgentService(
        provider=Provider(),
        dispatcher=ToolDispatcher(
            employee_service=employees,
            it_service=ITService(repository),
            knowledge_service=Mock(spec=KnowledgeQueryService),
            demo_repository=repository,
            leave_preparation_service=LeavePreparationService(employees),
        ),
    )
    app = create_app(repository=repository, agent_service=agent)
    app.state.workflow_settings = isolated_settings
    app.state.workflow_session_factory = session_factory
    initiation_id = uuid4()
    with TestClient(app, raise_server_exceptions=False) as api:
        response = api.post(
            "/api/v1/assistant/query",
            headers=ALEX_HEADERS,
            json={
                "message": "My laptop cannot connect to the office Wi-Fi. Please prepare help.",
                "initiation_id": str(initiation_id),
            },
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["prepared_action"]["category"] == "network"
    assert payload["prepared_action"]["urgency"] == "medium"
    assert payload["action_status"] == "created"
    assert payload["action"]["draft"]["action_type"] == "create_it_support_ticket"
    with session_factory() as session:
        persisted = session.execute(
            text("SELECT draft_payload FROM action_revisions WHERE action_id = :action_id"),
            {"action_id": UUID(payload["action"]["action_id"])},
        ).scalar_one()
    assert payload["action"]["draft"] == persisted
    assert _count(engine, "SELECT count(*) FROM it_tickets WHERE source_action_id IS NOT NULL") == 0


def test_edit_appends_revision_and_old_challenge_cannot_authorize_current(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    created = _create(session_factory, isolated_settings)
    assert created.action_id is not None
    confirmation = ConfirmationService(session_factory, isolated_settings)
    old_challenge = confirmation.issue_challenge(action_id=created.action_id, context=ALEX)
    edited = ITActionRevisionService(session_factory, isolated_settings).create_revision(
        action_id=created.action_id,
        request=ReviseITSupportTicketRequest(
            expected_revision=1,
            category=ITTicketCategory.ACCESS,
            summary="Payroll portal access blocked",
            description="Sign-in is rejected after the approved password reset.",
            urgency=ITTicketUrgency.HIGH,
        ),
        context=ALEX,
    )

    assert edited.revision == 2
    assert edited.draft["summary"] == "Payroll portal access blocked"
    with session_factory() as session:
        rows = session.execute(
            text(
                "SELECT revision, state, draft_hash FROM action_revisions "
                "WHERE action_id = :action_id ORDER BY revision"
            ),
            {"action_id": created.action_id},
        ).all()
        current = session.execute(
            text("SELECT current_revision FROM action_workflows WHERE action_id = :action_id"),
            {"action_id": created.action_id},
        ).scalar_one()
    assert [(row.revision, row.state) for row in rows] == [
        (1, WorkflowState.SUPERSEDED.value),
        (2, WorkflowState.AWAITING_CONFIRMATION.value),
    ]
    assert rows[0].draft_hash != rows[1].draft_hash
    assert current == 2
    with pytest.raises(ConfirmationInvalidError):
        confirmation.confirm(
            action_id=created.action_id,
            challenge_id=old_challenge.challenge_id,
            confirmation_token=old_challenge.confirmation_token,
            context=ALEX,
        )


def test_expired_unswept_it_revision_cannot_be_edited_into_fresh_ttl(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    created = _create(session_factory, isolated_settings)
    assert created.action_id is not None
    confirmation = ConfirmationService(session_factory, isolated_settings)
    confirmation.issue_challenge(action_id=created.action_id, context=ALEX)
    with session_factory() as session:
        expired_at = session.execute(
            text(
                "UPDATE action_revisions "
                "SET action_expires_at = clock_timestamp() - interval '1 second' "
                "WHERE action_id = :action_id AND revision = 1 "
                "RETURNING action_expires_at"
            ),
            {"action_id": created.action_id},
        ).scalar_one()
        session.commit()

    with pytest.raises(ActionConflictError):
        ITActionRevisionService(session_factory, isolated_settings).create_revision(
            action_id=created.action_id,
            request=ReviseITSupportTicketRequest(
                expected_revision=1,
                category=ITTicketCategory.HARDWARE,
                summary="Dock is not detected",
                description="The approved laptop no longer detects its synthetic dock.",
                urgency=ITTicketUrgency.MEDIUM,
            ),
            context=ALEX,
        )

    with session_factory() as session:
        revisions = session.execute(
            text(
                "SELECT revision, state, action_expires_at "
                "FROM action_revisions WHERE action_id = :action_id ORDER BY revision"
            ),
            {"action_id": created.action_id},
        ).all()
        current_revision = session.execute(
            text("SELECT current_revision FROM action_workflows WHERE action_id = :action_id"),
            {"action_id": created.action_id},
        ).scalar_one()
        challenge_status = session.execute(
            text(
                "SELECT status FROM confirmation_challenges "
                "WHERE action_id = :action_id AND revision = 1"
            ),
            {"action_id": created.action_id},
        ).scalar_one()
        expiry_audits = session.execute(
            text(
                "SELECT count(*) FROM action_audit_events "
                "WHERE action_id = :action_id AND revision = 1 "
                "AND event_type = 'ACTION_EXPIRED'"
            ),
            {"action_id": created.action_id},
        ).scalar_one()

    assert [(row.revision, row.state) for row in revisions] == [(1, WorkflowState.EXPIRED.value)]
    assert revisions[0].action_expires_at == expired_at
    assert current_revision == 1
    assert challenge_status == "SUPERSEDED"
    assert expiry_audits == 1


def test_revision_api_allows_only_owned_it_business_fields(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    client: TestClient,
) -> None:
    created = _create(session_factory, isolated_settings)
    assert created.action_id is not None
    path = f"/api/v1/actions/{created.action_id}/revisions"
    payload = {
        "expected_revision": 1,
        "category": "hardware",
        "summary": "Dock is not detected",
        "description": "The approved laptop no longer detects its synthetic dock.",
        "urgency": "medium",
    }
    cross_owner = client.post(path, headers=SAM_HEADERS, json=payload)
    forbidden = client.post(
        path,
        headers=ALEX_HEADERS,
        json={**payload, "employee_id": "EMP-1002"},
    )
    revised = client.post(path, headers=ALEX_HEADERS, json=payload)

    assert cross_owner.status_code == 404
    assert forbidden.status_code == 422
    assert revised.status_code == 200
    assert revised.json()["revision"] == 2
    assert revised.json()["draft"]["summary"] == "Dock is not detected"
    assert "employee_id" not in revised.json()["draft"]


def test_confirmation_edit_race_serializes_to_one_authoritative_outcome(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
) -> None:
    created = _create(session_factory, isolated_settings)
    assert created.action_id is not None
    confirmation = ConfirmationService(session_factory, isolated_settings)
    challenge = confirmation.issue_challenge(action_id=created.action_id, context=ALEX)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def confirm() -> None:
        barrier.wait()
        try:
            result = confirmation.confirm(
                action_id=created.action_id,
                challenge_id=challenge.challenge_id,
                confirmation_token=challenge.confirmation_token,
                context=ALEX,
            )
            outcomes.append(f"confirm:{result.revision}:{result.state}")
        except (ActionConflictError, ConfirmationInvalidError):
            outcomes.append("confirm:rejected")

    def edit() -> None:
        barrier.wait()
        try:
            result = ITActionRevisionService(session_factory, isolated_settings).create_revision(
                action_id=created.action_id,
                request=ReviseITSupportTicketRequest(
                    expected_revision=1,
                    category=ITTicketCategory.SOFTWARE,
                    summary="Meeting app will not open",
                    description="The synthetic meeting app exits immediately.",
                    urgency=ITTicketUrgency.MEDIUM,
                ),
                context=ALEX,
            )
            outcomes.append(f"edit:{result.revision}:{result.state}")
        except ActionConflictError:
            outcomes.append("edit:rejected")

    workers = [threading.Thread(target=confirm), threading.Thread(target=edit)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    current = confirmation.get_action(action_id=created.action_id, context=ALEX)
    assert current.revision in {1, 2}
    if current.revision == 1:
        assert current.state == WorkflowState.CONFIRMED.value
        assert "edit:rejected" in outcomes
    else:
        assert current.state == WorkflowState.AWAITING_CONFIRMATION.value
        assert "confirm:rejected" in outcomes


def test_confirmed_it_executes_once_without_leave_advisory_lock_and_projects_result(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
    client: TestClient,
) -> None:
    created = _create(session_factory, isolated_settings)
    assert created.action_id is not None
    confirmed = _confirm(session_factory, isolated_settings, created.action_id)
    assert confirmed.state == WorkflowState.CONFIRMED.value
    with patch(
        "app.workflow.atomic_execution.acquire_employee_lock",
        side_effect=AssertionError("IT must not take the leave advisory lock"),
    ):
        first = AtomicConfirmedExecutor(session_factory, isolated_settings).execute_action(
            created.action_id
        )
    second = AtomicConfirmedExecutor(session_factory, isolated_settings).execute_action(
        created.action_id
    )

    assert first.outcome is AtomicOutcome.SUCCEEDED
    assert first.ticket_id is not None
    assert second.outcome is AtomicOutcome.IDLE
    assert (
        _count(
            engine,
            "SELECT count(*) FROM it_tickets WHERE source_action_id = :action_id",
            action_id=created.action_id,
        )
        == 1
    )
    listed = client.get("/api/v1/me/tickets", headers=ALEX_HEADERS)
    detail = client.get(
        f"/api/v1/actions/{created.action_id}/detail",
        headers=ALEX_HEADERS,
    )
    cross = client.get(
        f"/api/v1/actions/{created.action_id}/detail",
        headers=SAM_HEADERS,
    )
    assert first.ticket_id in {item["ticket_id"] for item in listed.json()["items"]}
    assert detail.json()["result"]["ticket_id"] == first.ticket_id
    assert detail.json()["revision"] == 1
    assert all("worker_id" not in event["safe_metadata"] for event in detail.json()["audit_events"])
    assert cross.status_code == 404


def test_ticket_insert_failure_rolls_back_and_retry_succeeds(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    created = _create(session_factory, isolated_settings)
    assert created.action_id is not None
    _confirm(session_factory, isolated_settings, created.action_id)
    failed = AtomicConfirmedExecutor(
        session_factory,
        isolated_settings,
        failpoints=AtomicExecutionFailpoints(
            raise_after_it_ticket_insert=RuntimeError("test-only rollback")
        ),
    ).execute_action(created.action_id)
    assert failed.outcome is AtomicOutcome.TRANSIENT
    assert (
        _count(
            engine,
            "SELECT count(*) FROM it_tickets WHERE source_action_id = :action_id",
            action_id=created.action_id,
        )
        == 0
    )
    assert (
        _count(
            engine,
            "SELECT count(*) FROM action_revisions "
            "WHERE action_id = :action_id AND state = 'CONFIRMED'",
            action_id=created.action_id,
        )
        == 1
    )
    retried = AtomicConfirmedExecutor(session_factory, isolated_settings).execute_action(
        created.action_id
    )
    assert retried.outcome is AtomicOutcome.SUCCEEDED


def test_lost_commit_ack_observes_one_authoritative_ticket(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    engine: Engine,
) -> None:
    created = _create(session_factory, isolated_settings)
    assert created.action_id is not None
    _confirm(session_factory, isolated_settings, created.action_id)
    result = AtomicConfirmedExecutor(
        session_factory,
        isolated_settings,
        failpoints=AtomicExecutionFailpoints(discard_after_commit=True),
    ).execute_action(created.action_id)
    assert result.outcome is AtomicOutcome.SUCCEEDED
    assert result.ticket_id is not None
    assert (
        _count(
            engine,
            "SELECT count(*) FROM it_tickets WHERE source_action_id = :action_id",
            action_id=created.action_id,
        )
        == 1
    )


def test_hr_and_it_actions_coexist_in_owner_projection(
    isolated_settings: KnowledgeSettings,
    session_factory: sessionmaker[Session],
    client: TestClient,
) -> None:
    it_action = _create(session_factory, isolated_settings)
    leave = LeaveRequestDraft(
        leave_type="annual",
        start_date=date(2026, 10, 21),
        end_date=date(2026, 10, 21),
        scheduled_work_days=1,
        requested_hours=Decimal("7.60"),
        current_balance_hours=Decimal("76.00"),
        projected_balance_hours=Decimal("68.40"),
        preparation_status=LeavePreparationStatus.READY,
        reason="Family visit",
        public_holiday_check_required=True,
        non_executing=True,
    )
    leave_action = ActionCreationService(session_factory, isolated_settings).create_or_reuse(
        ALEX, leave
    )
    response = client.get("/api/v1/me/actions?limit=50", headers=ALEX_HEADERS)
    assert response.status_code == 200
    actions = {item["action_id"]: item for item in response.json()["items"]}
    assert actions[str(it_action.action_id)]["action_type"] == "create_it_support_ticket"
    assert actions[str(leave_action.action_id)]["action_type"] == "submit_annual_leave"

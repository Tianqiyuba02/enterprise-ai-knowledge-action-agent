import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from langgraph.checkpoint.base import empty_checkpoint
from sqlalchemy import create_engine, inspect, text

from app.config import load_knowledge_settings
from app.db.session import create_knowledge_engine
from app.workflow.calendar import V4_CALENDAR_VERSION
from app.workflow.checkpointing import open_postgres_checkpointer

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]

CHECKPOINT_TABLES = (
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)
V4_TABLES = (
    "public_holidays",
    "action_workflows",
    "action_revisions",
    "confirmation_challenges",
    "workflow_outbox",
    "action_execution_ledger",
    "action_audit_events",
    "leave_requests",
)


def _replace_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def isolated_database_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    settings = load_knowledge_settings()
    admin_url = settings.database_url.get_secret_value()
    database_name = f"knowledge_agent_v4_cp_{uuid.uuid4().hex[:12]}"
    isolated_url = _replace_database(admin_url, database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        monkeypatch.setenv("APP_DATABASE_URL", isolated_url)
        yield isolated_url
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


def test_0001_to_0003_enables_saver_without_runtime_setup(isolated_database_url: str) -> None:
    config = AlembicConfig("alembic.ini")
    command.upgrade(config, "0001_v2_knowledge")
    command.upgrade(config, "0002_v4_action_workflows")
    command.upgrade(config, "0003_v4_langgraph_checkpoints")

    engine = create_engine(isolated_database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"documents", "document_chunks"} <= tables
        assert set(V4_TABLES) <= tables
        assert set(CHECKPOINT_TABLES) <= tables
        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            holiday_count = connection.execute(
                text(
                    """
                    SELECT count(*) FROM public_holidays
                    WHERE calendar_version = :calendar_version
                    """
                ),
                {"calendar_version": V4_CALENDAR_VERSION},
            ).scalar_one()
            migration_versions = (
                connection.execute(text("SELECT v FROM checkpoint_migrations ORDER BY v"))
                .scalars()
                .all()
            )
        assert version == "0003_v4_langgraph_checkpoints"
        assert holiday_count == 14
        assert migration_versions == list(range(10))
    finally:
        engine.dispose()

    thread_id = f"thread-{uuid.uuid4()}"
    write_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    with open_postgres_checkpointer() as checkpointer:
        checkpointer.put(write_config, checkpoint, {}, {})
        loaded = checkpointer.get({"configurable": {"thread_id": thread_id}})

    assert loaded is not None
    assert loaded["id"] == checkpoint["id"]
    assert loaded["channel_values"] == checkpoint["channel_values"]


def test_0003_downgrade_removes_only_checkpoint_tables(isolated_database_url: str) -> None:
    config = AlembicConfig("alembic.ini")
    command.upgrade(config, "0003_v4_langgraph_checkpoints")
    command.downgrade(config, "0002_v4_action_workflows")
    engine = create_engine(isolated_database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {"documents", "document_chunks"} <= tables
        assert set(V4_TABLES) <= tables
        assert set(CHECKPOINT_TABLES).isdisjoint(tables)
        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            holiday_count = connection.execute(
                text("SELECT count(*) FROM public_holidays")
            ).scalar_one()
        assert version == "0002_v4_action_workflows"
        assert holiday_count == 14
    finally:
        engine.dispose()


def test_additive_0003_preserves_live_v2_corpus() -> None:
    command.upgrade(AlembicConfig("alembic.ini"), "head")
    engine = create_knowledge_engine()
    try:
        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            documents = connection.execute(text("SELECT count(*) FROM documents")).scalar_one()
            chunks = connection.execute(text("SELECT count(*) FROM document_chunks")).scalar_one()
            holidays = connection.execute(text("SELECT count(*) FROM public_holidays")).scalar_one()
        assert version == "0003_v4_langgraph_checkpoints"
        assert documents == 12
        assert chunks == 42
        assert holidays == 14
    finally:
        engine.dispose()

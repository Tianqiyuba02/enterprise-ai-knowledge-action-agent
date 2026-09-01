import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect, text

from app.config import load_knowledge_settings

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]

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
    database_name = f"knowledge_agent_v4_{uuid.uuid4().hex[:12]}"
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


def test_0002_downgrade_removes_only_v4_schema(isolated_database_url: str) -> None:
    config = AlembicConfig("alembic.ini")
    command.upgrade(config, "0002_v4_action_workflows")
    engine = create_engine(isolated_database_url)
    try:
        inspector = inspect(engine)
        assert set(V4_TABLES) <= set(inspector.get_table_names())
        assert {"documents", "document_chunks"} <= set(inspector.get_table_names())
        with engine.connect() as connection:
            holiday_count = connection.execute(
                text("SELECT count(*) FROM public_holidays")
            ).scalar_one()
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert holiday_count == 14
        assert version == "0002_v4_action_workflows"
    finally:
        engine.dispose()

    command.downgrade(config, "0001_v2_knowledge")
    engine = create_engine(isolated_database_url)
    try:
        inspector = inspect(engine)
        remaining = set(inspector.get_table_names())
        assert {"documents", "document_chunks"} <= remaining
        assert set(V4_TABLES).isdisjoint(remaining)
        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert version == "0001_v2_knowledge"
    finally:
        engine.dispose()

"""Isolated PostgreSQL lifecycle for destructive integration-test fixtures.

This is not a general test-database framework. Callers must still bind engines
and Alembic to the yielded isolated URL. Destructive setup must never target
the configured shared development database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, text

from app.config import load_knowledge_settings
from app.db.session import create_knowledge_engine

SHARED_DEVELOPMENT_DATABASE_NAME = "knowledge_agent"


class SharedDevelopmentDatabaseError(RuntimeError):
    """Raised when a destructive test path targets the shared development database."""


def database_name_from_url(url: str) -> str:
    return urlsplit(url).path.strip("/")


def replace_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def refuse_shared_development_database(candidate_url: str, shared_url: str) -> None:
    """Refuse if the candidate is the same database as the configured shared development DB."""

    candidate_name = database_name_from_url(candidate_url)
    shared_name = database_name_from_url(shared_url)
    if candidate_name in (shared_name, SHARED_DEVELOPMENT_DATABASE_NAME):
        raise SharedDevelopmentDatabaseError(
            "refusing destructive test lifecycle against the shared development database "
            f"{shared_name!r}"
        )


def refuse_engine_targets_shared_database(engine: Engine) -> None:
    """Refuse deletes against the configured shared DB, ignoring APP_DATABASE_URL overrides."""

    knowledge_url = load_knowledge_settings().knowledge_database_url.get_secret_value()
    candidate_name = engine.url.database or ""
    shared_name = database_name_from_url(knowledge_url)
    if candidate_name in (shared_name, SHARED_DEVELOPMENT_DATABASE_NAME):
        raise SharedDevelopmentDatabaseError(
            "refusing destructive corpus cleanup against the shared development database "
            f"{shared_name!r}"
        )


@contextmanager
def isolated_test_engine(*, prefix: str) -> Iterator[Engine]:
    """Create, migrate, and drop a unique database. Never mutates the shared development DB."""

    live = load_knowledge_settings()
    admin_url = live.database_url.get_secret_value()
    database_name = f"{prefix}_{uuid4().hex[:12]}"
    isolated_url = replace_database(admin_url, database_name)
    refuse_shared_development_database(isolated_url, admin_url)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    previous = os.environ.get("APP_DATABASE_URL")
    engine: Engine | None = None
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        os.environ["APP_DATABASE_URL"] = isolated_url
        refuse_shared_development_database(os.environ["APP_DATABASE_URL"], admin_url)
        command.upgrade(AlembicConfig("alembic.ini"), "head")
        isolated_settings = load_knowledge_settings()
        refuse_shared_development_database(
            isolated_settings.database_url.get_secret_value(),
            admin_url,
        )
        engine = create_knowledge_engine(isolated_settings)
        yield engine
    finally:
        if previous is None:
            os.environ.pop("APP_DATABASE_URL", None)
        else:
            os.environ["APP_DATABASE_URL"] = previous
        if engine is not None:
            engine.dispose()
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

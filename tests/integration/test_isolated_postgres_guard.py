import os

import pytest
from isolated_postgres import (
    SHARED_DEVELOPMENT_DATABASE_NAME,
    SharedDevelopmentDatabaseError,
    isolated_settings_for_engine,
    isolated_test_engine,
    refuse_shared_development_database,
    replace_database,
    shared_development_settings,
)
from sqlalchemy import text

from app.db.session import create_knowledge_engine

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"


def test_refuse_shared_development_database_by_name() -> None:
    shared = "postgresql+psycopg://knowledge_app:x@127.0.0.1:5433/knowledge_agent"
    with pytest.raises(SharedDevelopmentDatabaseError, match="shared development database"):
        refuse_shared_development_database(shared, shared)


def test_refuse_allows_unique_isolated_name() -> None:
    shared = "postgresql+psycopg://knowledge_app:x@127.0.0.1:5433/knowledge_agent"
    isolated = replace_database(shared, "knowledge_agent_v2_ing_abc123def456")
    refuse_shared_development_database(isolated, shared)
    assert isolated.endswith("/knowledge_agent_v2_ing_abc123def456")


@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_ENABLED,
    reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
)
def test_isolated_engine_restores_app_database_url_before_later_fixture() -> None:
    previous = os.environ.get("APP_DATABASE_URL")
    try:
        os.environ.pop("APP_DATABASE_URL", None)
        isolated_name = None
        with isolated_test_engine(prefix="knowledge_agent_v4_iso") as isolated:
            isolated_name = isolated.url.database
            assert isolated_name != SHARED_DEVELOPMENT_DATABASE_NAME
            assert os.environ.get("APP_DATABASE_URL") is None
            isolated_settings = isolated_settings_for_engine(isolated)
            assert isolated_settings.database_url.get_secret_value().endswith(f"/{isolated_name}")
            with isolated.connect() as connection:
                connection.execute(text("SELECT 1"))
        assert os.environ.get("APP_DATABASE_URL") is None

        later = create_knowledge_engine(shared_development_settings())
        try:
            assert later.url.database == SHARED_DEVELOPMENT_DATABASE_NAME
            assert later.url.database != isolated_name
        finally:
            later.dispose()
    finally:
        if previous is None:
            os.environ.pop("APP_DATABASE_URL", None)
        else:
            os.environ["APP_DATABASE_URL"] = previous


@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_ENABLED,
    reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
)
def test_isolated_engine_restores_preexisting_app_database_url() -> None:
    sentinel = "postgresql+psycopg://knowledge_app:x@127.0.0.1:5433/unrelated_sentinel"
    previous = os.environ.get("APP_DATABASE_URL")
    try:
        os.environ["APP_DATABASE_URL"] = sentinel
        with isolated_test_engine(prefix="knowledge_agent_v4_iso") as isolated:
            assert isolated.url.database != SHARED_DEVELOPMENT_DATABASE_NAME
            assert os.environ.get("APP_DATABASE_URL") == sentinel
        assert os.environ.get("APP_DATABASE_URL") == sentinel
    finally:
        if previous is None:
            os.environ.pop("APP_DATABASE_URL", None)
        else:
            os.environ["APP_DATABASE_URL"] = previous

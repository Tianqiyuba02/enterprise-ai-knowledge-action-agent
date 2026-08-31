import pytest
from isolated_postgres import (
    SharedDevelopmentDatabaseError,
    refuse_shared_development_database,
    replace_database,
)


def test_refuse_shared_development_database_by_name() -> None:
    shared = "postgresql+psycopg://knowledge_app:x@127.0.0.1:5433/knowledge_agent"
    with pytest.raises(SharedDevelopmentDatabaseError, match="shared development database"):
        refuse_shared_development_database(shared, shared)


def test_refuse_allows_unique_isolated_name() -> None:
    shared = "postgresql+psycopg://knowledge_app:x@127.0.0.1:5433/knowledge_agent"
    isolated = replace_database(shared, "knowledge_agent_v2_ing_abc123def456")
    refuse_shared_development_database(isolated, shared)
    assert isolated.endswith("/knowledge_agent_v2_ing_abc123def456")

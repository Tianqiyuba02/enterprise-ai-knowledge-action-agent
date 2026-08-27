"""Migration-controlled LangGraph checkpoint factory.

Serialization boundary:

- Checkpoints must contain only JSON-safe primitives (str, int, bool, null,
  mappings, and lists) plus the package-safe msgpack allowlist.
- Do not persist SQLAlchemy models, DB sessions, AuthenticatedEmployeeContext,
  Pydantic domain objects, confirmation tokens, provider clients, exceptions,
  or arbitrary Python classes.
- LANGGRAPH_STRICT_MSGPACK=true and an explicit JsonPlusSerializer allowlist
  restrict deserialization to known-safe types.
- PostgresSaver 3.1.2 uses unqualified table names and has no schema argument.
  Checkpoint tables therefore live in public, created only by Alembic 0003.
- Application code must never invoke the PostgresSaver runtime schema setup API.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import Connection
from psycopg.rows import dict_row
from pydantic import SecretStr

from app.config import KnowledgeSettings, load_knowledge_settings

STRICT_MSGPACK_ENV = "LANGGRAPH_STRICT_MSGPACK"


def enable_strict_checkpoint_serialization() -> None:
    """Restrict checkpoint deserialization before any saver is constructed."""

    os.environ[STRICT_MSGPACK_ENV] = "true"


def create_checkpoint_serializer() -> JsonPlusSerializer:
    """Return the pinned-package strict msgpack serializer."""

    enable_strict_checkpoint_serialization()
    return JsonPlusSerializer(allowed_msgpack_modules=None, pickle_fallback=False)


def psycopg_conninfo(database_url: str | SecretStr) -> str:
    """Translate the SQLAlchemy APP/KNOWLEDGE URL into a psycopg conninfo."""

    value = database_url.get_secret_value() if isinstance(database_url, SecretStr) else database_url
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    if value.startswith("postgres+psycopg://"):
        return "postgresql://" + value.removeprefix("postgres+psycopg://")
    return value


def create_postgres_checkpointer(
    settings: KnowledgeSettings | None = None,
) -> tuple[Connection, PostgresSaver]:
    """Create a sync PostgresSaver whose connection the caller must close.

    The connection uses autocommit=True and dict_row as required by the pinned
    PostgresSaver. The caller owns the connection lifecycle.
    """

    resolved = settings or load_knowledge_settings()
    connection = Connection.connect(
        psycopg_conninfo(resolved.database_url),
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    checkpointer = PostgresSaver(connection, serde=create_checkpoint_serializer())
    return connection, checkpointer


@contextmanager
def open_postgres_checkpointer(
    settings: KnowledgeSettings | None = None,
) -> Iterator[PostgresSaver]:
    """Own a short-lived checkpointer connection for sync orchestration."""

    connection, checkpointer = create_postgres_checkpointer(settings)
    try:
        yield checkpointer
    finally:
        connection.close()

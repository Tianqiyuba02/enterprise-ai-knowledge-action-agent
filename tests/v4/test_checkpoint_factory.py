import ast
import os
from pathlib import Path

from langgraph.checkpoint.postgres.base import MIGRATIONS
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import SecretStr

from app.config import DEFAULT_KNOWLEDGE_DATABASE_URL, KnowledgeSettings
from app.workflow.checkpointing import (
    STRICT_MSGPACK_ENV,
    create_checkpoint_serializer,
    create_postgres_checkpointer,
    enable_strict_checkpoint_serialization,
    psycopg_conninfo,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "app"
SETUP_CALL_NAMES = {"setup"}
SETUP_RECEIVERS = {"checkpointer", "saver", "postgres_saver", "PostgresSaver"}


def test_pinned_postgres_saver_has_no_schema_and_ten_migrations() -> None:
    import inspect

    from langgraph.checkpoint.postgres import PostgresSaver

    assert "schema" not in inspect.signature(PostgresSaver.__init__).parameters
    assert len(MIGRATIONS) == 10
    assert "CREATE TABLE IF NOT EXISTS checkpoint_migrations" in MIGRATIONS[0]
    assert "CREATE TABLE IF NOT EXISTS checkpoints" in MIGRATIONS[1]
    assert "CREATE TABLE IF NOT EXISTS checkpoint_blobs" in MIGRATIONS[2]
    assert "CREATE TABLE IF NOT EXISTS checkpoint_writes" in MIGRATIONS[3]
    assert "task_path" in MIGRATIONS[9]


def test_psycopg_conninfo_strips_sqlalchemy_dialect() -> None:
    assert psycopg_conninfo(DEFAULT_KNOWLEDGE_DATABASE_URL) == (
        "postgresql://knowledge_app:knowledge_app_local_only@127.0.0.1:5433/knowledge_agent"
    )
    assert (
        psycopg_conninfo(SecretStr("postgresql+psycopg://app:secret@127.0.0.1:5433/app"))
        == "postgresql://app:secret@127.0.0.1:5433/app"
    )


def test_strict_serializer_is_explicit_and_disables_pickle() -> None:
    enable_strict_checkpoint_serialization()
    serializer = create_checkpoint_serializer()

    assert os.environ[STRICT_MSGPACK_ENV] == "true"
    assert isinstance(serializer, JsonPlusSerializer)
    assert serializer.pickle_fallback is False
    assert serializer._allowed_msgpack_modules is None


def test_factory_uses_resolved_app_database_url(monkeypatch) -> None:
    settings = KnowledgeSettings(
        app_database_url=SecretStr("postgresql+psycopg://app:app@127.0.0.1:5433/app_db"),
        knowledge_database_url=SecretStr(DEFAULT_KNOWLEDGE_DATABASE_URL),
        _env_file=None,
    )
    captured: dict[str, object] = {}

    class _FakeConnection:
        @staticmethod
        def connect(conninfo: str, **kwargs: object) -> object:
            captured["conninfo"] = conninfo
            captured["kwargs"] = kwargs
            return object()

    class _FakeSaver:
        def __init__(self, conn: object, serde: object = None) -> None:
            captured["conn"] = conn
            captured["serde"] = serde

    monkeypatch.setattr("app.workflow.checkpointing.Connection", _FakeConnection)
    monkeypatch.setattr("app.workflow.checkpointing.PostgresSaver", _FakeSaver)

    connection, checkpointer = create_postgres_checkpointer(settings)

    assert captured["conninfo"] == "postgresql://app:app@127.0.0.1:5433/app_db"
    assert captured["kwargs"]["autocommit"] is True
    assert captured["kwargs"]["prepare_threshold"] == 0
    assert isinstance(captured["serde"], JsonPlusSerializer)
    assert connection is captured["conn"]
    assert isinstance(checkpointer, _FakeSaver)


def _calls_runtime_setup(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in SETUP_CALL_NAMES:
            if isinstance(func.value, ast.Name) and func.value.id in SETUP_RECEIVERS:
                hits.append(f"{path}:{node.lineno}")
            if isinstance(func.value, ast.Attribute) and func.value.attr in SETUP_RECEIVERS:
                hits.append(f"{path}:{node.lineno}")
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "setup"
            and isinstance(func.value, ast.Name)
            and func.value.id == "PostgresSaver"
        ):
            hits.append(f"{path}:{node.lineno}")
    return hits


def test_application_never_calls_checkpointer_setup() -> None:
    hits: list[str] = []
    for path in SRC.rglob("*.py"):
        hits.extend(_calls_runtime_setup(path))
    assert hits == []

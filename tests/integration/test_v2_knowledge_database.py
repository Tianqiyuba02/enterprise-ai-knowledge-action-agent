import os
import uuid
from collections.abc import Iterator
from datetime import date

import pytest
from isolated_postgres import isolated_test_engine
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Document, DocumentChunk

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]


@pytest.fixture(scope="session")
def migrated_engine() -> Iterator[Engine]:
    with isolated_test_engine(prefix="knowledge_agent_v2_schema") as engine:
        yield engine


@pytest.fixture
def connection(migrated_engine: Engine) -> Iterator[Connection]:
    with migrated_engine.connect() as database_connection:
        transaction = database_connection.begin()
        try:
            yield database_connection
        finally:
            transaction.rollback()


def _document_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "doc_code": f"POL-{uuid.uuid4().hex[:8].upper()}",
        "version": "1.0",
        "title": "Synthetic Test Policy",
        "status": "approved",
        "effective_date": date(2026, 1, 1),
        "expiry_date": None,
        "jurisdiction": "AU-VIC",
        "audience_groups": ["all_employees"],
        "source_uri": "corpus/synthetic-test-policy.md",
        "content_checksum": "a" * 64,
        "superseded_by_id": None,
        "embedding_model_id": "gemini-embedding-2",
        "embedding_dimension": 768,
    }
    values.update(overrides)
    return values


def _chunk_values(document_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "document_id": document_id,
        "chunk_index": 0,
        "section_label": "Scope",
        "anchor": "section-scope",
        "page": None,
        "content": "Synthetic policy content for a PostgreSQL constraint test.",
        "embedding": [0.0] * 768,
        "token_count": 10,
    }
    values.update(overrides)
    return values


def test_empty_database_upgrades_to_expected_head_and_schema(
    connection: Connection,
) -> None:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    document_columns = {column["name"] for column in inspector.get_columns("documents")}
    chunk_columns = {column["name"] for column in inspector.get_columns("document_chunks")}
    vector_type = connection.execute(
        text(
            """
            SELECT format_type(attribute.atttypid, attribute.atttypmod)
            FROM pg_attribute AS attribute
            JOIN pg_class AS table_class ON table_class.oid = attribute.attrelid
            WHERE table_class.relname = 'document_chunks'
              AND attribute.attname = 'embedding'
              AND attribute.attnum > 0
            """
        )
    ).scalar_one()

    assert (
        connection.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one()
        == "vector"
    )
    assert (
        connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        == "0004_v4_phase1a_occupancy"
    )
    assert {"documents", "document_chunks"} <= tables
    assert document_columns == {column.name for column in Document.__table__.columns}
    assert chunk_columns == {column.name for column in DocumentChunk.__table__.columns}
    assert vector_type == "vector(768)"


def test_document_identity_is_unique(connection: Connection) -> None:
    first = _document_values(doc_code="POL-HR-001", version="1.0")
    connection.execute(Document.__table__.insert().values(**first))

    duplicate = _document_values(doc_code="POL-HR-001", version="1.0")
    with pytest.raises(IntegrityError):
        connection.execute(Document.__table__.insert().values(**duplicate))


def test_expiry_must_be_after_effective_date(connection: Connection) -> None:
    values = _document_values(
        effective_date=date(2026, 1, 1),
        expiry_date=date(2026, 1, 1),
    )

    with pytest.raises(IntegrityError):
        connection.execute(Document.__table__.insert().values(**values))


@pytest.mark.parametrize("checksum", ["a" * 63, "A" * 64, "g" * 64])
def test_checksum_must_be_lowercase_sha256(connection: Connection, checksum: str) -> None:
    with pytest.raises(IntegrityError):
        connection.execute(
            Document.__table__.insert().values(**_document_values(content_checksum=checksum))
        )


def test_document_cannot_supersede_itself(connection: Connection) -> None:
    document_id = uuid.uuid4()
    values = _document_values(
        id=document_id,
        status="superseded",
        superseded_by_id=document_id,
    )

    with pytest.raises(IntegrityError):
        connection.execute(Document.__table__.insert().values(**values))


def test_superseded_status_requires_successor_link(connection: Connection) -> None:
    with pytest.raises(IntegrityError):
        connection.execute(
            Document.__table__.insert().values(
                **_document_values(status="superseded", superseded_by_id=None)
            )
        )


def test_approved_status_rejects_successor_link(connection: Connection) -> None:
    successor = _document_values()
    connection.execute(Document.__table__.insert().values(**successor))
    invalid = _document_values(
        status="approved",
        superseded_by_id=successor["id"],
    )

    with pytest.raises(IntegrityError):
        connection.execute(Document.__table__.insert().values(**invalid))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "withdrawn"),
        ("jurisdiction", "AU-QLD"),
        ("embedding_dimension", 3072),
    ],
)
def test_document_controlled_values_are_enforced(
    connection: Connection, field: str, value: object
) -> None:
    with pytest.raises(IntegrityError):
        connection.execute(Document.__table__.insert().values(**_document_values(**{field: value})))


@pytest.mark.parametrize(
    "audience_groups",
    [[], ["contractors"]],
)
def test_audience_array_is_nonempty_and_controlled(
    connection: Connection, audience_groups: list[str]
) -> None:
    with pytest.raises(IntegrityError):
        connection.execute(
            Document.__table__.insert().values(**_document_values(audience_groups=audience_groups))
        )


def test_document_chunk_requires_existing_document(connection: Connection) -> None:
    with pytest.raises(IntegrityError):
        connection.execute(DocumentChunk.__table__.insert().values(**_chunk_values(uuid.uuid4())))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chunk_index", -1),
        ("token_count", 0),
        ("page", 0),
    ],
)
def test_document_chunk_numeric_constraints(connection: Connection, field: str, value: int) -> None:
    document = _document_values()
    connection.execute(Document.__table__.insert().values(**document))

    with pytest.raises(IntegrityError):
        connection.execute(
            DocumentChunk.__table__.insert().values(
                **_chunk_values(document["id"], **{field: value})
            )
        )


def test_document_chunk_index_is_unique_per_document(connection: Connection) -> None:
    document = _document_values()
    connection.execute(Document.__table__.insert().values(**document))
    connection.execute(
        DocumentChunk.__table__.insert().values(**_chunk_values(document["id"], chunk_index=0))
    )

    with pytest.raises(IntegrityError):
        connection.execute(
            DocumentChunk.__table__.insert().values(**_chunk_values(document["id"], chunk_index=0))
        )

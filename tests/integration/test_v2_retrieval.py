import os
import uuid
from collections.abc import Iterator, Sequence
from datetime import date, timedelta

import pytest
from isolated_postgres import isolated_test_engine, refuse_engine_targets_shared_database
from sqlalchemy import Engine, create_engine, delete
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Document, DocumentChunk
from app.db.session import create_knowledge_session_factory
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.errors import KnowledgeDatabaseError
from app.knowledge.repository import KnowledgeRetrievalRepository
from app.knowledge.vocabulary import AudienceGroup, Jurisdiction

POSTGRES_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"
TRUSTED_TODAY = date(2026, 8, 25)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_ENABLED,
        reason="set RUN_POSTGRES_TESTS=1 with the V2 PostgreSQL container running",
    ),
]


@pytest.fixture(scope="session")
def retrieval_engine() -> Iterator[Engine]:
    with isolated_test_engine(prefix="knowledge_agent_v2_ret") as engine:
        yield engine


@pytest.fixture(autouse=True)
def clean_knowledge_tables(retrieval_engine: Engine) -> Iterator[None]:
    _delete_all(retrieval_engine)
    yield
    _delete_all(retrieval_engine)


@pytest.fixture
def repository(retrieval_engine: Engine) -> KnowledgeRetrievalRepository:
    return KnowledgeRetrievalRepository(create_knowledge_session_factory(retrieval_engine))


@pytest.fixture
def applicability() -> KnowledgeApplicabilityContext:
    return KnowledgeApplicabilityContext(
        jurisdiction=Jurisdiction.AU_VIC,
        audience_groups=frozenset(
            {
                AudienceGroup.ALL_EMPLOYEES,
                AudienceGroup.MELBOURNE_EMPLOYEES,
            }
        ),
    )


def _delete_all(engine: Engine) -> None:
    refuse_engine_targets_shared_database(engine)
    with engine.begin() as connection:
        connection.execute(delete(DocumentChunk))
        connection.execute(delete(Document))


def _vector(first: float, second: float = 0.0) -> list[float]:
    return [first, second, *([0.0] * 766)]


def _insert_evidence(
    engine: Engine,
    *,
    doc_code: str,
    version: str = "1.0",
    vector: Sequence[float],
    status: str = "approved",
    effective_date: date = date(2026, 1, 1),
    expiry_date: date | None = None,
    jurisdiction: str = "AU-VIC",
    audience_groups: list[str] | None = None,
    superseded_by_id: uuid.UUID | None = None,
) -> uuid.UUID:
    document_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            Document.__table__.insert().values(
                id=document_id,
                doc_code=doc_code,
                version=version,
                title=f"Synthetic {doc_code}",
                status=status,
                effective_date=effective_date,
                expiry_date=expiry_date,
                jurisdiction=jurisdiction,
                audience_groups=audience_groups or ["all_employees"],
                source_uri=f"synthetic://retrieval/{doc_code}/{version}",
                content_checksum="a" * 64,
                superseded_by_id=superseded_by_id,
                embedding_model_id="gemini-embedding-2",
                embedding_dimension=768,
            )
        )
        connection.execute(
            DocumentChunk.__table__.insert().values(
                id=uuid.uuid4(),
                document_id=document_id,
                chunk_index=0,
                section_label="Scope",
                anchor="scope",
                page=None,
                content=f"Deterministic evidence for {doc_code} {version}.",
                embedding=list(vector),
                token_count=6,
            )
        )
    return document_id


def _search(
    repository: KnowledgeRetrievalRepository,
    applicability: KnowledgeApplicabilityContext,
    *,
    vector: Sequence[float] | None = None,
    top_k: int = 20,
    today: date = TRUSTED_TODAY,
):
    return repository.search(
        query_embedding=vector or _vector(1.0),
        applicability=applicability,
        trusted_today=today,
        top_k=top_k,
    )


def test_authority_and_applicability_traps_are_excluded_before_ranking(
    retrieval_engine: Engine,
    repository: KnowledgeRetrievalRepository,
    applicability: KnowledgeApplicabilityContext,
) -> None:
    approved_id = _insert_evidence(
        retrieval_engine,
        doc_code="POL-HR-001",
        version="2.0",
        vector=_vector(0.9, 0.1),
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-HR-001",
        version="1.0",
        vector=_vector(1.0),
        status="superseded",
        superseded_by_id=approved_id,
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-WRK-002",
        vector=_vector(1.0),
        jurisdiction="AU-NSW",
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="SOP-TRV-001",
        vector=_vector(1.0),
        jurisdiction="GLOBAL",
        audience_groups=["managers"],
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="DRAFT-FAC-001",
        vector=_vector(1.0),
        status="draft",
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-FIN-003",
        vector=_vector(1.0),
        effective_date=date(2030, 1, 1),
        jurisdiction="GLOBAL",
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-TRV-LEGACY",
        vector=_vector(1.0),
        effective_date=date(2023, 1, 1),
        expiry_date=date(2025, 12, 31),
        jurisdiction="GLOBAL",
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-HR-002",
        vector=_vector(0.8, 0.2),
        jurisdiction="GLOBAL",
    )

    evidence = _search(repository, applicability)
    identities = {(item.doc_code, item.version) for item in evidence}

    assert ("POL-HR-001", "2.0") in identities
    assert ("POL-HR-002", "1.0") in identities
    assert ("POL-HR-001", "1.0") not in identities
    assert {
        "POL-WRK-002",
        "SOP-TRV-001",
        "DRAFT-FAC-001",
        "POL-FIN-003",
        "POL-TRV-LEGACY",
    }.isdisjoint({item.doc_code for item in evidence})


def test_audience_overlap_requires_at_least_one_trusted_group(
    retrieval_engine: Engine,
    repository: KnowledgeRetrievalRepository,
    applicability: KnowledgeApplicabilityContext,
) -> None:
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-AUDIENCE-YES",
        vector=_vector(0.9, 0.1),
        audience_groups=["managers", "all_employees"],
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-AUDIENCE-NO",
        vector=_vector(1.0),
        audience_groups=["managers"],
    )

    evidence = _search(repository, applicability)

    assert [item.doc_code for item in evidence] == ["POL-AUDIENCE-YES"]


def test_effective_and_expiry_date_boundaries_use_injected_date(
    retrieval_engine: Engine,
    repository: KnowledgeRetrievalRepository,
    applicability: KnowledgeApplicabilityContext,
) -> None:
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-EFFECTIVE-TODAY",
        vector=_vector(0.9, 0.1),
        effective_date=TRUSTED_TODAY,
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-EFFECTIVE-FUTURE",
        vector=_vector(1.0),
        effective_date=TRUSTED_TODAY + timedelta(days=1),
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-EXPIRY-TODAY",
        vector=_vector(1.0),
        expiry_date=TRUSTED_TODAY,
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-EXPIRY-FUTURE",
        vector=_vector(0.8, 0.2),
        expiry_date=TRUSTED_TODAY + timedelta(days=1),
    )

    codes = {item.doc_code for item in _search(repository, applicability)}

    assert codes == {"POL-EFFECTIVE-TODAY", "POL-EXPIRY-FUTURE"}


def test_exact_cosine_distance_order_and_top_k(
    retrieval_engine: Engine,
    repository: KnowledgeRetrievalRepository,
    applicability: KnowledgeApplicabilityContext,
) -> None:
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-RANK-1",
        vector=_vector(1.0),
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-RANK-2",
        vector=_vector(0.8, 0.6),
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-RANK-3",
        vector=_vector(0.0, 1.0),
    )

    evidence = _search(repository, applicability, top_k=2)

    assert [item.doc_code for item in evidence] == ["POL-RANK-1", "POL-RANK-2"]
    assert evidence[0].cosine_distance == pytest.approx(0.0)
    assert evidence[1].cosine_distance == pytest.approx(0.2)
    assert evidence[0].similarity > evidence[1].similarity


def test_ineligible_perfect_vector_cannot_outrank_eligible_evidence(
    retrieval_engine: Engine,
    repository: KnowledgeRetrievalRepository,
    applicability: KnowledgeApplicabilityContext,
) -> None:
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-NSW-PERFECT",
        vector=_vector(1.0),
        jurisdiction="AU-NSW",
    )
    _insert_evidence(
        retrieval_engine,
        doc_code="POL-VIC-ELIGIBLE",
        vector=_vector(0.8, 0.6),
        jurisdiction="AU-VIC",
    )

    evidence = _search(repository, applicability, top_k=1)

    assert [item.doc_code for item in evidence] == ["POL-VIC-ELIGIBLE"]
    assert evidence[0].cosine_distance == pytest.approx(0.2)


def test_database_failure_is_mapped_without_connection_details(
    applicability: KnowledgeApplicabilityContext,
) -> None:
    engine = create_engine(
        "postgresql+psycopg://unavailable:secret@127.0.0.1:1/unavailable",
        connect_args={"connect_timeout": 1},
    )
    repository = KnowledgeRetrievalRepository(
        sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    )

    with pytest.raises(KnowledgeDatabaseError) as captured:
        _search(repository, applicability, top_k=1)

    assert "secret" not in str(captured.value)
    assert "127.0.0.1" not in str(captured.value)
    engine.dispose()

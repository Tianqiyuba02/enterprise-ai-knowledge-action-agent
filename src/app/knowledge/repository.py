"""Exact pgvector retrieval with authority/applicability filtering before ranking."""

import math
from collections.abc import Sequence
from datetime import date
from numbers import Real

from sqlalchemy import Select, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import APPROVED_EMBEDDING_DIMENSION
from app.db.models import Document, DocumentChunk
from app.knowledge.context import KnowledgeApplicabilityContext
from app.knowledge.errors import InvalidQueryVectorError, KnowledgeDatabaseError
from app.knowledge.models import RetrievedEvidence


class KnowledgeRetrievalRepository:
    """Run one bounded exact cosine-distance query over eligible chunks only."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def search(
        self,
        *,
        query_embedding: Sequence[float],
        applicability: KnowledgeApplicabilityContext,
        trusted_today: date,
        top_k: int,
    ) -> tuple[RetrievedEvidence, ...]:
        vector = _validate_query_vector(query_embedding)
        if not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        statement = _build_retrieval_statement(
            query_embedding=vector,
            applicability=applicability,
            trusted_today=trusted_today,
            top_k=top_k,
        )
        try:
            with self._session_factory() as session:
                rows = session.execute(statement).all()
        except SQLAlchemyError as exc:
            raise KnowledgeDatabaseError(
                "The knowledge database could not complete retrieval."
            ) from exc

        return tuple(
            RetrievedEvidence(
                document_id=row.document_id,
                chunk_id=row.chunk_id,
                doc_code=row.doc_code,
                version=row.version,
                title=row.title,
                status=row.status,
                effective_date=row.effective_date,
                expiry_date=row.expiry_date,
                jurisdiction=row.jurisdiction,
                audience_groups=frozenset(row.audience_groups),
                section_label=row.section_label,
                anchor=row.anchor,
                page=row.page,
                content=row.content,
                token_count=row.token_count,
                cosine_distance=float(row.cosine_distance),
            )
            for row in rows
        )


def _build_retrieval_statement(
    *,
    query_embedding: Sequence[float],
    applicability: KnowledgeApplicabilityContext,
    trusted_today: date,
    top_k: int,
) -> Select[tuple[object, ...]]:
    cosine_distance = DocumentChunk.embedding.cosine_distance(list(query_embedding)).label(
        "cosine_distance"
    )
    trusted_audiences = sorted(group.value for group in applicability.audience_groups)
    return (
        select(
            Document.id.label("document_id"),
            DocumentChunk.id.label("chunk_id"),
            Document.doc_code,
            Document.version,
            Document.title,
            Document.status,
            Document.effective_date,
            Document.expiry_date,
            Document.jurisdiction,
            Document.audience_groups,
            DocumentChunk.section_label,
            DocumentChunk.anchor,
            DocumentChunk.page,
            DocumentChunk.content,
            DocumentChunk.token_count,
            cosine_distance,
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            Document.status == "approved",
            Document.effective_date <= trusted_today,
            or_(Document.expiry_date.is_(None), Document.expiry_date > trusted_today),
            or_(
                Document.jurisdiction == "GLOBAL",
                Document.jurisdiction == applicability.jurisdiction.value,
            ),
            Document.audience_groups.overlap(trusted_audiences),
        )
        .order_by(
            cosine_distance.asc(),
            Document.doc_code.asc(),
            Document.version.asc(),
            DocumentChunk.chunk_index.asc(),
        )
        .limit(top_k)
    )


def _validate_query_vector(query_embedding: Sequence[float]) -> tuple[float, ...]:
    if len(query_embedding) != APPROVED_EMBEDDING_DIMENSION:
        raise InvalidQueryVectorError("Query embedding must use the 768-dimension index profile.")
    if any(
        isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value))
        for value in query_embedding
    ):
        raise InvalidQueryVectorError("Query embedding contains invalid numeric data.")
    return tuple(float(value) for value in query_embedding)

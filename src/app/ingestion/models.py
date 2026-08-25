"""Strict internal models for V2 Markdown corpus ingestion."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.config import APPROVED_EMBEDDING_DIMENSION, APPROVED_EMBEDDING_MODEL
from app.knowledge.vocabulary import AudienceGroup, Jurisdiction

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]
NonEmptyRawString = Annotated[str, StringConstraints(min_length=1, strict=True)]


class DocumentSourceStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SupersedesIdentity(StrictModel):
    doc_code: NonEmptyString
    version: NonEmptyString


class DocumentMetadata(StrictModel):
    """Immutable source-controlled metadata parsed from YAML front matter."""

    doc_code: NonEmptyString
    version: NonEmptyString
    title: NonEmptyString
    status: DocumentSourceStatus
    effective_date: date
    expiry_date: date | None = None
    jurisdiction: Jurisdiction
    audience_groups: frozenset[AudienceGroup] = Field(min_length=1)
    source_uri: NonEmptyString
    supersedes: SupersedesIdentity | None = None

    @model_validator(mode="after")
    def validate_authority_metadata(self) -> Self:
        if self.expiry_date is not None and self.expiry_date <= self.effective_date:
            raise ValueError("expiry_date must be later than effective_date")
        if self.supersedes is not None:
            if self.status is DocumentSourceStatus.DRAFT:
                raise ValueError("draft documents cannot declare supersession")
            if self.supersedes.doc_code != self.doc_code:
                raise ValueError("supersession must remain within one doc_code")
            if self.supersedes.version == self.version:
                raise ValueError("a document cannot supersede itself")
        return self


class SourceDocument(StrictModel):
    """One parsed source document with normalized Markdown body."""

    metadata: DocumentMetadata
    body: NonEmptyRawString
    source_name: NonEmptyString


class DocumentChunk(StrictModel):
    """One deterministic heading-aware retrieval unit before embedding."""

    chunk_index: Annotated[int, Field(ge=0)]
    section_label: NonEmptyString
    anchor: NonEmptyString
    content: NonEmptyRawString
    token_count: Annotated[int, Field(gt=0)]


class EmbeddingProfile(StrictModel):
    model_id: Literal["gemini-embedding-2"] = APPROVED_EMBEDDING_MODEL
    dimension: Literal[768] = APPROVED_EMBEDDING_DIMENSION


class PreparedChunk(StrictModel):
    chunk: DocumentChunk
    embedding: tuple[float, ...]

    @model_validator(mode="after")
    def validate_embedding_dimension(self) -> Self:
        if len(self.embedding) != APPROVED_EMBEDDING_DIMENSION:
            raise ValueError("embedding does not match the approved 768-dimension profile")
        return self


class IngestionOutcome(StrEnum):
    INSERTED = "inserted"
    NO_OP = "no_op"


class IngestionResult(StrictModel):
    outcome: IngestionOutcome
    doc_code: NonEmptyString
    version: NonEmptyString
    chunk_count: Annotated[int, Field(ge=0)]
    embedding_model_id: Literal["gemini-embedding-2"] = APPROVED_EMBEDDING_MODEL
    embedding_dimension: Literal[768] = APPROVED_EMBEDDING_DIMENSION

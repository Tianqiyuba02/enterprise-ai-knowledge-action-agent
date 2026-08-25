"""Strict internal evidence models for authority-aware retrieval."""

import uuid
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.knowledge.vocabulary import AudienceGroup, Jurisdiction

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]
NonEmptyContent = Annotated[str, StringConstraints(min_length=1, strict=True)]


class RetrievedEvidence(BaseModel):
    """One approved, applicable chunk ranked by canonical cosine distance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: uuid.UUID
    chunk_id: uuid.UUID
    doc_code: NonEmptyString
    version: NonEmptyString
    title: NonEmptyString
    status: Literal["approved"]
    effective_date: date
    expiry_date: date | None
    jurisdiction: Jurisdiction
    audience_groups: frozenset[AudienceGroup] = Field(min_length=1)
    section_label: NonEmptyString
    anchor: NonEmptyString
    page: Annotated[int | None, Field(ge=1)] = None
    content: NonEmptyContent
    token_count: Annotated[int, Field(gt=0)]
    cosine_distance: Annotated[float, Field(ge=0.0, le=2.0)]

    @property
    def similarity(self) -> float:
        """Convenience score derived only as one minus canonical cosine distance."""

        return 1.0 - self.cosine_distance

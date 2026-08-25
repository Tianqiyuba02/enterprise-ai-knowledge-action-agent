"""Strict public contracts for the authenticated V2 knowledge endpoint."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.grounding.models import KnowledgeAnswerStatus

StrictQuestion = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000, strict=True),
]
NonEmptyPublicText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000, strict=True),
]
NonEmptyMetadata = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]


class KnowledgeAPIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeQueryRequest(KnowledgeAPIModel):
    question: StrictQuestion


class KnowledgeCitation(KnowledgeAPIModel):
    doc_code: NonEmptyMetadata
    title: NonEmptyMetadata
    version: NonEmptyMetadata
    section_anchor: NonEmptyMetadata
    page: Annotated[int | None, Field(ge=1)] = None


class KnowledgeQueryResponse(KnowledgeAPIModel):
    status: KnowledgeAnswerStatus
    answer: NonEmptyPublicText
    citations: tuple[KnowledgeCitation, ...] = Field(max_length=6)

    @model_validator(mode="after")
    def validate_semantic_outcome(self) -> Self:
        if self.status is KnowledgeAnswerStatus.ANSWERED and not self.citations:
            raise ValueError("answered response requires at least one citation")
        if self.status is KnowledgeAnswerStatus.INSUFFICIENT_EVIDENCE and self.citations:
            raise ValueError("insufficient evidence response must not include citations")
        if self.status is KnowledgeAnswerStatus.CONFLICTING_EVIDENCE:
            document_identities = {
                (citation.doc_code, citation.version) for citation in self.citations
            }
            if len(document_identities) < 2:
                raise ValueError("conflicting evidence response requires two document identities")
        return self

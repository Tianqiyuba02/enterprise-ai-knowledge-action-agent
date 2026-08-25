"""Strict internal structured output for V2 grounded generation."""

import re
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmptyAnswer = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000, strict=True),
]
EvidenceReference = Annotated[
    str,
    StringConstraints(pattern=r"^E[1-9][0-9]*$", max_length=8, strict=True),
]


class KnowledgeAnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


class GroundedAnswerDraft(BaseModel):
    """Only model-controlled fields accepted from grounded generation."""

    model_config = ConfigDict(extra="forbid")

    status: KnowledgeAnswerStatus
    answer: NonEmptyAnswer
    evidence_refs: tuple[EvidenceReference, ...] = Field(max_length=6)

    @model_validator(mode="after")
    def validate_status_references(self) -> Self:
        if re.search(r"\bE[1-9][0-9]*\b", self.answer):
            raise ValueError("public answer text must not expose internal evidence references")
        distinct_refs = set(self.evidence_refs)
        if self.status is KnowledgeAnswerStatus.ANSWERED and not distinct_refs:
            raise ValueError("answered output requires at least one evidence reference")
        if self.status is KnowledgeAnswerStatus.INSUFFICIENT_EVIDENCE and distinct_refs:
            raise ValueError("insufficient evidence output must not cite evidence")
        if self.status is KnowledgeAnswerStatus.CONFLICTING_EVIDENCE and len(distinct_refs) < 2:
            raise ValueError(
                "conflicting evidence output requires at least two evidence references"
            )
        return self

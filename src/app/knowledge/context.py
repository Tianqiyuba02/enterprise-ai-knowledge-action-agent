"""Immutable trusted applicability context for authority-aware retrieval."""

from pydantic import BaseModel, ConfigDict, Field

from app.knowledge.vocabulary import AudienceGroup, Jurisdiction


class KnowledgeApplicabilityContext(BaseModel):
    """Server-derived retrieval filters that clients and models cannot supply."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    jurisdiction: Jurisdiction
    audience_groups: frozenset[AudienceGroup] = Field(min_length=1)

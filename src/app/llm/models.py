"""Structured response models accepted from the V0 LLM call."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StringConstraints


class QuestionCategory(StrEnum):
    """Supported V0 classification categories."""

    HR = "hr"
    IT = "it"
    EXPENSE = "expense"
    TRAVEL = "travel"
    GENERAL = "general"


class QuestionAnalysis(BaseModel):
    """Schema-validated analysis returned for one employee question."""

    model_config = ConfigDict(extra="forbid")

    category: QuestionCategory
    summary: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500, strict=True),
    ]
    requires_action: StrictBool
    confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]

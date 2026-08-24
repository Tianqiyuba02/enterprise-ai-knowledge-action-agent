import pytest
from pydantic import ValidationError

from app.llm.models import QuestionAnalysis, QuestionCategory


def test_valid_structured_response() -> None:
    response = QuestionAnalysis.model_validate(
        {
            "category": "it",
            "summary": "Reset access to the payroll portal.",
            "requires_action": True,
            "confidence": 0.94,
        }
    )

    assert response.category is QuestionCategory.IT
    assert response.requires_action is True
    assert response.confidence == 0.94


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("category", "legal"),
        ("requires_action", "yes"),
        ("confidence", "0.8"),
        ("confidence", -0.01),
        ("confidence", 1.01),
        ("summary", ""),
    ],
)
def test_schema_rejects_invalid_fields(field: str, value: object) -> None:
    payload = {
        "category": "hr",
        "summary": "Check annual leave entitlement.",
        "requires_action": False,
        "confidence": 0.8,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        QuestionAnalysis.model_validate(payload)


def test_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        QuestionAnalysis.model_validate(
            {
                "category": "general",
                "summary": "A general question.",
                "requires_action": False,
                "confidence": 0.7,
                "untrusted_extra": "must not be accepted",
            }
        )

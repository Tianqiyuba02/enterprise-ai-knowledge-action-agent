import json
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.config import KnowledgeSettings, Settings
from app.grounding.client import (
    GeminiGroundedGenerationClient,
    GroundedServiceError,
    GroundedTimeoutError,
    InvalidGroundedResponseError,
)
from app.grounding.models import KnowledgeAnswerStatus
from app.grounding.references import assign_evidence_references
from app.knowledge.models import RetrievedEvidence
from app.knowledge.vocabulary import AudienceGroup, Jurisdiction


def _evidence() -> RetrievedEvidence:
    return RetrievedEvidence(
        document_id=uuid.uuid4(),
        chunk_id=uuid.uuid4(),
        doc_code="POL-HR-001",
        version="2.0",
        title="Annual Leave Policy",
        status="approved",
        effective_date=date(2026, 1, 1),
        expiry_date=None,
        jurisdiction=Jurisdiction.AU_VIC,
        audience_groups=frozenset({AudienceGroup.ALL_EMPLOYEES}),
        section_label="Entitlement",
        anchor="entitlement",
        page=None,
        content="Eligible employees receive twenty days of annual leave.",
        token_count=8,
        cosine_distance=0.2,
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(gemini_api_key="test-only-key", _env_file=None)


@pytest.fixture
def knowledge_settings() -> KnowledgeSettings:
    return KnowledgeSettings(_env_file=None)


def test_grounded_client_uses_schema_and_returns_locally_validated_draft(
    settings: Settings,
    knowledge_settings: KnowledgeSettings,
) -> None:
    sdk_client = Mock()
    sdk_client.models.generate_content.return_value = SimpleNamespace(
        text=json.dumps(
            {
                "status": "answered",
                "answer": "Eligible employees receive twenty days.",
                "evidence_refs": ["E1"],
            }
        )
    )
    client = GeminiGroundedGenerationClient(
        settings,
        knowledge_settings,
        sdk_client=sdk_client,
    )
    referenced = assign_evidence_references((_evidence(),))

    draft = client.generate("How much annual leave?", referenced)

    assert draft.status is KnowledgeAnswerStatus.ANSWERED
    assert draft.evidence_refs == ("E1",)
    call = sdk_client.models.generate_content.call_args
    assert call.kwargs["model"] == "gemini-3.6-flash"
    assert settings.gemini_model == "gemini-3.5-flash"
    assert call.kwargs["config"].response_mime_type == "application/json"
    assert call.kwargs["config"].response_json_schema is not None
    assert call.kwargs["config"].automatic_function_calling.disable is True
    assert "BEGIN_UNTRUSTED_REFERENCE_DATA" in call.kwargs["contents"]


@pytest.mark.parametrize(
    "response_text",
    [
        "",
        "not json",
        json.dumps({"status": "unsupported", "answer": "Text", "evidence_refs": []}),
        json.dumps({"status": "answered", "answer": "Text", "evidence_refs": []}),
        json.dumps(
            {
                "status": "answered",
                "answer": "Text",
                "evidence_refs": ["E1"],
                "doc_code": "invented",
            }
        ),
    ],
)
def test_grounded_client_rejects_malformed_or_schema_invalid_output(
    settings: Settings,
    knowledge_settings: KnowledgeSettings,
    response_text: str,
) -> None:
    sdk_client = Mock()
    sdk_client.models.generate_content.return_value = SimpleNamespace(text=response_text)
    client = GeminiGroundedGenerationClient(
        settings,
        knowledge_settings,
        sdk_client=sdk_client,
    )

    with pytest.raises(InvalidGroundedResponseError):
        client.generate(
            "Question",
            assign_evidence_references((_evidence(),)),
        )


@pytest.mark.parametrize(
    ("provider_error", "expected_error"),
    [
        (httpx.ReadTimeout("sensitive timeout"), GroundedTimeoutError),
        (httpx.ConnectError("sensitive transport"), GroundedServiceError),
    ],
)
def test_grounded_transport_failures_are_safe(
    settings: Settings,
    knowledge_settings: KnowledgeSettings,
    provider_error: Exception,
    expected_error: type[Exception],
) -> None:
    sdk_client = Mock()
    sdk_client.models.generate_content.side_effect = provider_error
    client = GeminiGroundedGenerationClient(
        settings,
        knowledge_settings,
        sdk_client=sdk_client,
    )

    with pytest.raises(expected_error) as captured:
        client.generate(
            "Question",
            assign_evidence_references((_evidence(),)),
        )

    assert "sensitive" not in str(captured.value)

from collections.abc import Iterator
from typing import cast
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api.application import create_app
from app.api.knowledge_models import KnowledgeCitation, KnowledgeQueryResponse
from app.embeddings.client import (
    EmbeddingRateLimitError,
    EmbeddingServiceError,
    EmbeddingTimeoutError,
    InvalidEmbeddingResponseError,
)
from app.grounding.client import (
    GroundedServiceError,
    GroundedTimeoutError,
    InvalidGroundedResponseError,
)
from app.knowledge.errors import KnowledgeDatabaseError
from app.knowledge.query_service import KnowledgeQueryService
from app.knowledge.vocabulary import AudienceGroup, Jurisdiction

PRIMARY_SESSION = {"X-Demo-Session": "demo-v1-7f4c2a91"}


@pytest.fixture
def knowledge_api_client() -> Iterator[tuple[TestClient, Mock]]:
    service = Mock(spec=KnowledgeQueryService)
    app = create_app(
        knowledge_query_service=cast(KnowledgeQueryService, service),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, service


def _answered() -> KnowledgeQueryResponse:
    return KnowledgeQueryResponse(
        status="answered",
        answer="Eligible employees receive twenty days.",
        citations=(
            KnowledgeCitation(
                doc_code="POL-HR-001",
                title="Annual Leave Policy",
                version="2.0",
                section_anchor="entitlement",
                page=None,
            ),
        ),
    )


def test_authenticated_knowledge_query_uses_server_owned_applicability(
    knowledge_api_client: tuple[TestClient, Mock],
) -> None:
    client, service = knowledge_api_client
    service.query.return_value = _answered()

    response = client.post(
        "/api/v1/knowledge/query",
        headers=PRIMARY_SESSION,
        json={"question": "  How much annual leave?  "},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "answered",
        "answer": "Eligible employees receive twenty days.",
        "citations": [
            {
                "doc_code": "POL-HR-001",
                "title": "Annual Leave Policy",
                "version": "2.0",
                "section_anchor": "entitlement",
                "page": None,
            }
        ],
    }
    question, applicability = service.query.call_args.args
    assert question == "How much annual leave?"
    assert applicability.jurisdiction is Jurisdiction.AU_VIC
    assert applicability.audience_groups == frozenset(
        {
            AudienceGroup.ALL_EMPLOYEES,
            AudienceGroup.MELBOURNE_EMPLOYEES,
        }
    )
    assert AudienceGroup.MANAGERS not in applicability.audience_groups
    assert response.headers["x-request-id"]


def test_knowledge_query_requires_existing_demo_session(
    knowledge_api_client: tuple[TestClient, Mock],
) -> None:
    client, service = knowledge_api_client

    missing = client.post("/api/v1/knowledge/query", json={"question": "Question"})
    invalid = client.post(
        "/api/v1/knowledge/query",
        headers={"X-Demo-Session": "invalid"},
        json={"question": "Question"},
    )

    assert missing.status_code == 401
    assert missing.json()["error_code"] == "invalid_demo_session"
    assert invalid.status_code == 401
    assert invalid.json()["error_code"] == "invalid_demo_session"
    service.query.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "   "},
        {"question": "x" * 4_001},
        {"question": 123},
        {"question": "Question", "employee_id": "EMP-1002"},
        {"question": "Question", "jurisdiction": "AU-NSW"},
        {"question": "Question", "audience": "managers"},
        {"question": "Question", "audience_groups": ["managers"]},
        {"question": "Question", "document_ids": ["invented"]},
        {"question": "Question", "top_k": 50},
    ],
)
def test_knowledge_request_rejects_invalid_or_client_controlled_fields(
    knowledge_api_client: tuple[TestClient, Mock],
    payload: dict[str, object],
) -> None:
    client, service = knowledge_api_client

    response = client.post(
        "/api/v1/knowledge/query",
        headers=PRIMARY_SESSION,
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "validation_error"
    service.query.assert_not_called()


@pytest.mark.parametrize(
    "response_model",
    [
        _answered(),
        KnowledgeQueryResponse(
            status="insufficient_evidence",
            answer="Approved evidence was not sufficient, so I will not guess.",
            citations=(),
        ),
        KnowledgeQueryResponse(
            status="conflicting_evidence",
            answer="Two approved sources conflict.",
            citations=(
                KnowledgeCitation(
                    doc_code="POL-SEC-004",
                    title="After-Hours Office Access Policy",
                    version="1.0",
                    section_anchor="access-window",
                    page=None,
                ),
                KnowledgeCitation(
                    doc_code="SOP-FAC-007",
                    title="Melbourne Facilities After-Hours Guide",
                    version="1.0",
                    section_anchor="escort-requirement",
                    page=None,
                ),
            ),
        ),
    ],
)
def test_all_semantic_outcomes_return_200(
    knowledge_api_client: tuple[TestClient, Mock],
    response_model: KnowledgeQueryResponse,
) -> None:
    client, service = knowledge_api_client
    service.query.return_value = response_model

    response = client.post(
        "/api/v1/knowledge/query",
        headers=PRIMARY_SESSION,
        json={"question": "Question"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == response_model.status.value
    assert "document_id" not in response.text
    assert "chunk_id" not in response.text


@pytest.mark.parametrize(
    ("error", "status_code", "error_code"),
    [
        (
            KnowledgeDatabaseError("sensitive SQL detail"),
            503,
            "knowledge_service_unavailable",
        ),
        (
            EmbeddingServiceError("sensitive provider detail"),
            503,
            "knowledge_embedding_unavailable",
        ),
        (
            EmbeddingRateLimitError("sensitive provider detail"),
            503,
            "knowledge_embedding_rate_limited",
        ),
        (
            EmbeddingTimeoutError("sensitive provider detail"),
            504,
            "knowledge_embedding_timeout",
        ),
        (
            InvalidEmbeddingResponseError("sensitive vector detail"),
            502,
            "invalid_query_embedding",
        ),
        (
            GroundedServiceError("sensitive provider detail"),
            503,
            "knowledge_model_unavailable",
        ),
        (
            GroundedTimeoutError("sensitive provider detail"),
            504,
            "knowledge_model_timeout",
        ),
        (
            InvalidGroundedResponseError("sensitive model detail"),
            502,
            "invalid_grounded_response",
        ),
    ],
)
def test_knowledge_errors_map_to_safe_public_envelopes(
    knowledge_api_client: tuple[TestClient, Mock],
    error: Exception,
    status_code: int,
    error_code: str,
) -> None:
    client, service = knowledge_api_client
    service.query.side_effect = error

    response = client.post(
        "/api/v1/knowledge/query",
        headers=PRIMARY_SESSION,
        json={"question": "Question"},
    )

    assert response.status_code == status_code
    assert response.json()["error_code"] == error_code
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert "sensitive" not in response.text

from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.config import KnowledgeSettings, Settings
from app.embeddings.client import (
    EmbeddingServiceError,
    EmbeddingTimeoutError,
    GeminiDocumentEmbeddingClient,
    InvalidEmbeddingResponseError,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(gemini_api_key="test-only-key", _env_file=None)


@pytest.fixture
def knowledge_settings() -> KnowledgeSettings:
    return KnowledgeSettings(_env_file=None)


def test_document_embedding_uses_approved_profile_and_semantics(
    settings: Settings,
    knowledge_settings: KnowledgeSettings,
) -> None:
    sdk_client = Mock()
    sdk_client.models.embed_content.return_value = SimpleNamespace(
        embeddings=[
            SimpleNamespace(values=[0.1] * 768),
            SimpleNamespace(values=[0.2] * 768),
        ]
    )
    client = GeminiDocumentEmbeddingClient(settings, knowledge_settings, sdk_client=sdk_client)

    vectors = client.embed_documents(
        ["first synthetic chunk", "second synthetic chunk"],
        title="Synthetic Policy",
    )

    assert len(vectors) == 2
    assert all(len(vector) == 768 for vector in vectors)
    call = sdk_client.models.embed_content.call_args
    assert call.kwargs["model"] == "gemini-embedding-2"
    assert [content.parts[0].text for content in call.kwargs["contents"]] == [
        "first synthetic chunk",
        "second synthetic chunk",
    ]
    assert call.kwargs["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert call.kwargs["config"].title == "Synthetic Policy"
    assert call.kwargs["config"].output_dimensionality == 768


def test_query_embedding_uses_retrieval_query_and_one_vector(
    settings: Settings,
    knowledge_settings: KnowledgeSettings,
) -> None:
    sdk_client = Mock()
    sdk_client.models.embed_content.return_value = SimpleNamespace(
        embeddings=[SimpleNamespace(values=[0.25] * 768)]
    )
    client = GeminiDocumentEmbeddingClient(settings, knowledge_settings, sdk_client=sdk_client)

    vector = client.embed_query("  annual leave entitlement  ")

    assert len(vector) == 768
    call = sdk_client.models.embed_content.call_args
    assert call.kwargs["model"] == "gemini-embedding-2"
    assert call.kwargs["contents"].parts[0].text == "annual leave entitlement"
    assert call.kwargs["config"].task_type == "RETRIEVAL_QUERY"
    assert call.kwargs["config"].title is None
    assert call.kwargs["config"].output_dimensionality == 768


@pytest.mark.parametrize(
    "embeddings",
    [
        [],
        [
            SimpleNamespace(values=[0.1] * 768),
            SimpleNamespace(values=[0.2] * 768),
        ],
        [SimpleNamespace(values=[0.1] * 767)],
        [SimpleNamespace(values=[0.1] * 767 + [float("nan")])],
    ],
)
def test_query_embedding_rejects_invalid_cardinality_dimension_and_values(
    settings: Settings,
    knowledge_settings: KnowledgeSettings,
    embeddings: object,
) -> None:
    sdk_client = Mock()
    sdk_client.models.embed_content.return_value = SimpleNamespace(embeddings=embeddings)
    client = GeminiDocumentEmbeddingClient(settings, knowledge_settings, sdk_client=sdk_client)

    with pytest.raises(InvalidEmbeddingResponseError):
        client.embed_query("annual leave")


def test_blank_query_embedding_does_not_call_provider(
    settings: Settings,
    knowledge_settings: KnowledgeSettings,
) -> None:
    sdk_client = Mock()
    client = GeminiDocumentEmbeddingClient(settings, knowledge_settings, sdk_client=sdk_client)

    with pytest.raises(InvalidEmbeddingResponseError):
        client.embed_query("  \n  ")

    sdk_client.models.embed_content.assert_not_called()


@pytest.mark.parametrize(
    "embeddings",
    [
        None,
        [],
        [SimpleNamespace(values=[0.1] * 767)],
        [SimpleNamespace(values=None)],
        [SimpleNamespace(values=[0.1] * 767 + ["invalid"])],
    ],
)
def test_malformed_embedding_responses_are_rejected(
    settings: Settings,
    knowledge_settings: KnowledgeSettings,
    embeddings: object,
) -> None:
    sdk_client = Mock()
    sdk_client.models.embed_content.return_value = SimpleNamespace(embeddings=embeddings)
    client = GeminiDocumentEmbeddingClient(settings, knowledge_settings, sdk_client=sdk_client)

    with pytest.raises(InvalidEmbeddingResponseError):
        client.embed_documents(["synthetic chunk"], title="Synthetic Policy")


@pytest.mark.parametrize(
    ("provider_error", "expected_error"),
    [
        (httpx.ReadTimeout("sensitive timeout detail"), EmbeddingTimeoutError),
        (httpx.ConnectError("sensitive connection detail"), EmbeddingServiceError),
    ],
)
def test_embedding_transport_errors_are_mapped_safely(
    settings: Settings,
    knowledge_settings: KnowledgeSettings,
    provider_error: Exception,
    expected_error: type[Exception],
) -> None:
    sdk_client = Mock()
    sdk_client.models.embed_content.side_effect = provider_error
    client = GeminiDocumentEmbeddingClient(settings, knowledge_settings, sdk_client=sdk_client)

    with pytest.raises(expected_error) as captured:
        client.embed_documents(["synthetic chunk"], title="Synthetic Policy")

    assert "sensitive" not in str(captured.value)


def test_embedding_input_is_validated_before_provider_call(
    settings: Settings,
    knowledge_settings: KnowledgeSettings,
) -> None:
    sdk_client = Mock()
    client = GeminiDocumentEmbeddingClient(settings, knowledge_settings, sdk_client=sdk_client)

    with pytest.raises(InvalidEmbeddingResponseError):
        client.embed_documents([], title="Synthetic Policy")

    sdk_client.models.embed_content.assert_not_called()

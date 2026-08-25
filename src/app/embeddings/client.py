"""Narrow Gemini document-embedding boundary for V2 ingestion."""

import math
from collections.abc import Sequence
from numbers import Real
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from app.config import KnowledgeSettings, Settings
from app.ingestion.models import EmbeddingProfile


class EmbeddingClientError(RuntimeError):
    """Base class for safe V2 embedding failures."""


class EmbeddingAuthenticationError(EmbeddingClientError):
    """Raised when the configured provider credential is rejected."""


class EmbeddingRateLimitError(EmbeddingClientError):
    """Raised when the provider rate limits an embedding request."""


class EmbeddingTimeoutError(EmbeddingClientError):
    """Raised when an embedding request exceeds the bounded timeout."""


class EmbeddingServiceError(EmbeddingClientError):
    """Raised when the provider or transport is unavailable."""


class InvalidEmbeddingResponseError(EmbeddingClientError):
    """Raised when vectors do not match the approved index profile."""


class GeminiDocumentEmbeddingClient:
    """Embed document chunks with the approved Gemini V2 profile."""

    def __init__(
        self,
        settings: Settings,
        knowledge_settings: KnowledgeSettings,
        sdk_client: Any | None = None,
    ) -> None:
        self.profile = EmbeddingProfile(
            model_id=knowledge_settings.knowledge_embedding_model,
            dimension=knowledge_settings.knowledge_embedding_dimension,
        )
        self._client = sdk_client or genai.Client(
            api_key=settings.gemini_api_key.get_secret_value(),
            http_options=types.HttpOptions(
                timeout=settings.gemini_timeout_seconds * 1_000,
                retry_options=types.HttpRetryOptions(
                    attempts=settings.gemini_max_attempts,
                    initial_delay=0.5,
                    max_delay=2.0,
                    jitter=0.25,
                    http_status_codes=[408, 429, 500, 502, 503, 504],
                ),
            ),
        )

    def embed_documents(
        self,
        contents: Sequence[str],
        *,
        title: str,
    ) -> tuple[tuple[float, ...], ...]:
        """Return one validated 768-value vector per supplied document chunk."""

        if not contents or any(not content.strip() for content in contents):
            raise InvalidEmbeddingResponseError("Document embedding input must not be empty.")
        if not title.strip():
            raise InvalidEmbeddingResponseError("Document embedding title must not be empty.")

        try:
            batch_contents = [
                types.Content(parts=[types.Part(text=content)]) for content in contents
            ]
            response = self._client.models.embed_content(
                model=self.profile.model_id,
                contents=batch_contents,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    title=title,
                    output_dimensionality=self.profile.dimension,
                ),
            )
        except errors.APIError as exc:
            raise _safe_embedding_provider_error(exc) from exc
        except httpx.TimeoutException as exc:
            raise EmbeddingTimeoutError(
                "The embedding request timed out. Please try again."
            ) from exc
        except httpx.TransportError as exc:
            raise EmbeddingServiceError(
                "The embedding service is temporarily unavailable. Please try again later."
            ) from exc

        embeddings = getattr(response, "embeddings", None)
        if not isinstance(embeddings, list) or len(embeddings) != len(contents):
            raise InvalidEmbeddingResponseError(
                "The embedding service returned an unexpected number of vectors."
            )

        validated: list[tuple[float, ...]] = []
        for embedding in embeddings:
            values = getattr(embedding, "values", None)
            if not isinstance(values, list) or len(values) != self.profile.dimension:
                raise InvalidEmbeddingResponseError(
                    "The embedding service returned a vector with an invalid dimension."
                )
            if any(
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(float(value))
                for value in values
            ):
                raise InvalidEmbeddingResponseError(
                    "The embedding service returned nonnumeric vector data."
                )
            validated.append(tuple(float(value) for value in values))
        return tuple(validated)


def _safe_embedding_provider_error(exc: errors.APIError) -> EmbeddingClientError:
    code = int(exc.code or 0)
    status = str(exc.status or "").upper()
    provider_reason = str(exc.details or "").upper()

    if (
        code in {401, 403}
        or status in {"UNAUTHENTICATED", "PERMISSION_DENIED"}
        or "API_KEY_INVALID" in provider_reason
    ):
        return EmbeddingAuthenticationError(
            "The embedding service rejected its configured credential."
        )
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return EmbeddingRateLimitError("The embedding service is busy. Please try again later.")
    if code in {408, 504} or status == "DEADLINE_EXCEEDED":
        return EmbeddingTimeoutError("The embedding request timed out. Please try again.")
    return EmbeddingServiceError(
        "The embedding service could not complete the request. Please try again later."
    )

# ADR 0003: Use Gemini Embedding 2 with a 768-dimension V2 index profile

- Status: Accepted
- Date: 2026-08-25
- Milestone: V2
- Resolves: OQ-2

## Context

V2 requires semantic retrieval while the project already uses the Google GenAI ecosystem. Because
current provider documentation used inconsistent stable and preview naming, the intended stable
embedding capability was checked empirically before database implementation.

## Decision

Using the existing `google-genai` 2.19.0 SDK, the 2026-08-25 capability check verified:

- stable model ID `gemini-embedding-2`;
- 768-value numeric output;
- `RETRIEVAL_QUERY`;
- `RETRIEVAL_DOCUMENT`; and
- document-title input for retrieval-document embeddings.

Credentials and full vectors were not printed, logged, or recorded.

The model ID and dimension form the explicit V2 embedding/index profile. That profile is separate
from immutable document source identity. Re-ingestion is a no-op only when document identity,
canonical source checksum, embedding model ID, and embedding dimension all match. A profile
mismatch is reported explicitly; reindexing is a separate approved operation and never a silent
overwrite.

## Consequences

- V2 uses `gemini-embedding-2` with `vector(768)`.
- Corpus chunks use retrieval-document semantics and titles; questions use retrieval-query
  semantics.
- Embedding calls remain behind a narrow V2 boundary.
- No multi-provider embedding framework is introduced.
- Changing the model or dimension requires an explicit migration/reindexing decision.

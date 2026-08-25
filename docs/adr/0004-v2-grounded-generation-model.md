# ADR 0004: Use Gemini 3.6 Flash for V2 grounded generation

- Status: Accepted
- Date: 2026-08-25
- Milestone: V2

## Context

The frozen Stage 5A grounded baseline completed zero cases with `gemini-3.5-flash`. Repeated
provider-side failures returned HTTP 503 `UNAVAILABLE`, including a minimal isolated request with
the newly loaded credential. A single equivalent capability check using `gemini-3.6-flash`
succeeded and returned the requested `OK`.

V1 question analysis and V2 grounded generation previously shared `GEMINI_MODEL`. Retrieval and
embedding do not depend on the generation model.

## Decision

V2 grounded generation uses `gemini-3.6-flash` through the V2-specific
`KNOWLEDGE_GROUNDED_MODEL` setting. The released V1 `GEMINI_MODEL` default remains
`gemini-3.5-flash`, preserving its existing configuration and contract.

This is a provider-availability migration, not RAG tuning. The embedding model, dimensions,
corpus, chunking, retrieval SQL, top-k, threshold posture, prompt, response schemas, semantic
rules, citations, and evaluation datasets remain unchanged.

## Consequences

- The completed Stage 5A retrieval baseline remains valid.
- No valid grounded baseline exists for Gemini 3.5 Flash.
- The development grounded baseline is measured fresh with Gemini 3.6 Flash.
- Holdout remains unexecuted.
- No multi-provider framework or fallback routing is introduced.

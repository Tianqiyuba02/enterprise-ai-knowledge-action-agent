# ADR 0001: Use Google Gemini as the V0 LLM provider

- Status: Accepted
- Date: 2026-08-24
- Milestone: V0
- Resolves: OQ-1 for V0

## Context

The approved kickoff leaves the primary provider unresolved. The V0 implementation instruction
requires exactly one provider and says to use the provider for which a working API key is available
when no prior choice exists. The implementation environment exposes a `GEMINI_API_KEY` credential.

## Decision

V0 uses Google Gemini through the official `google-genai` Python SDK. The provider call is isolated
in `src/app/llm/client.py`. Gemini receives the Pydantic-generated JSON Schema, and the returned JSON
is independently validated with Pydantic before it is used or displayed.

## Consequences

- There is one provider implementation and no routing or parity layer.
- Provider-specific configuration uses `GEMINI_*` environment variables.
- A future provider replacement remains localized to the narrow client boundary, but no replacement
  is implemented in V0.
- The embedding decision remains outside V0 and is not affected by this ADR.

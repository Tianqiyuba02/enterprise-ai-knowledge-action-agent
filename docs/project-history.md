# Project History

The current product is documented in [README](../README.md), [Architecture](architecture.md), and the [Engineering Case Study](case-study.md). This page preserves the milestone progression and historical evidence without making chronology the product entry point.

## V0 — Structured LLM foundation (`v0.1.0`)

Introduced the Python project, environment-only Gemini configuration, provider-native structured output, Pydantic validation, bounded failures, and offline provider mocks. Release verification recorded 23 tests, Ruff, format, and one authorized live structured-output smoke.

## V1 — FastAPI and trusted demo identity (`v0.2.0`)

Added `/api/v1`, consistent error contracts, request IDs, fixed synthetic demo sessions, server-resolved employee identity, profile/balance/ticket reads, and owner isolation. Verification recorded 42 tests plus OpenAPI and manual checks.

## V2 — Authority-aware RAG (`v0.3.0`)

Added PostgreSQL/pgvector, Alembic, governed versioned ingestion, applicability filtering before ranking, grounded answers over untrusted evidence, and server-built citations. The small frozen holdout recorded retrieval recall@6 and MRR of 1.0 and grounded status accuracy of 0.875 over 8 cases; it does not establish statistical significance.

## V3 — Agent and READ/PREPARE tools (`v0.4.0`)

Added a bounded provider-native tool loop while keeping tools read-only or preparatory. The 16-case development campaign and first-exposure 8-case holdout completed with no expectation misses. See [V3 Agent Evaluation](v3-agent-evaluation.md) and [V3 Release Readiness](v3-release-readiness.md).

## V4 — Safe Annual Leave execution (`v0.5.0`)

Added deterministic preparation, persisted authoritative drafts, out-of-band confirmation, PostgreSQL-authoritative state, employee-level concurrency control, atomic leave mutation and audit, and lost-ACK recovery.

The final development evaluation was closed as **PARTIAL / PROVIDER-LIMITED**, not a full pass:

- 15 applicable cases;
- 11 observed semantic cases;
- 11/11 observed passed;
- 4 cases uncovered because of provider availability;
- V4 holdout not created.

The live execution path is the simplified PostgreSQL poller described in [V4 Architecture Simplification Freeze 2.2](v4-architecture-simplification-freeze-2.2.md), not the original LangGraph sketch.

## V5 M1 — Enterprise Portal

Added the Next.js employee portal, Home, Assistant, My Leave, My Requests, Action Detail, independent Annual Leave Review, Policy Library, server-only persona mapping, and display of the exact persisted draft.

## V5 M2 — Multi-domain trust

Added the explicit IT Support domain, owner-scoped tickets, editable immutable IT revisions, fresh revision-bound authorization, domain dispatch, exactly-one ticket linkage, and the governed IT support source. HR and IT share control-plane invariants but retain separate business handlers.

## V5 M3 — Public demo reliability

Added the Render topology, public Next.js BFF, private FastAPI and worker services, PostgreSQL 17/pgvector, governed bootstrap, quotas, signed visitor accounting, periodic synthetic reset, readiness, and worker heartbeat. Three real public scenarios passed on the deployed M3 build; a later closure removed a dead source CTA while the repository remained private.

## V5 M4 — Portfolio and v1.0 closure

The v1.0 release candidate polishes the existing product, documents the trust model and limitations, adds deterministic browser E2E and GitHub Actions, prepares public repository basics, and synchronizes version metadata. It adds no new business capability and does not change the sealed V4/M2 execution architecture.

## Release workflow

Feature work integrates to `develop`, then a controller-approved release candidate merges to `main`. Annotated tags and GitHub Releases are created only from the reviewed main commit. Existing release history and author metadata are preserved without rewriting.

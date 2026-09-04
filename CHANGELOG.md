# Changelog

All notable portfolio releases are recorded here. Historical technical evidence remains in `docs/` and `evals/`.

## 1.0.0 — release candidate

v1.0.0 brings the completed knowledge, action, portal, multi-domain trust, and public-demo work into one reviewable portfolio release.

### Product

- Added a task-first Next.js employee portal with Home, Assistant, My Leave, IT Support, My Requests, independent Review, Action Detail, Policy Library, and About surfaces.
- Added a bounded public Render demo with a public Next.js BFF, private FastAPI and worker services, PostgreSQL/pgvector, quotas, reset, and readiness.
- Added product-styled recovery, bounded Assistant Markdown, state-aware Review copy, readable mobile navigation, and mobile draft-first Review ordering.

### Governed knowledge

- Added authority-aware retrieval over versioned governed documents.
- Applied approved/effective/jurisdiction/audience filtering before vector ranking.
- Treated retrieved text as untrusted evidence and built citations from server-owned document/version/section metadata.

### Safe actions

- Kept the model READ/PREPARE-only with no execute tool.
- Persisted authoritative action drafts before independent human review.
- Bound short-lived confirmation challenges to exact revisions; chat text cannot authorize.
- Added deterministic Annual Leave execution with balance/overlap revalidation and employee-level concurrency control.
- Added explicit IT Support execution with editable immutable revisions and exactly-one ticket linkage.
- Committed each business result, final action state, and application audit evidence in one PostgreSQL transaction.
- Preserved authoritative recovery when a successful commit acknowledgement is lost.

### Engineering evidence

- Added deterministic backend, PostgreSQL integration, frontend, and browser E2E gates.
- Added maintained trust-boundary diagrams, case study, public-safe interview notes, deployment guidance, release safety procedure, and demo script.
- Added MIT licensing, security reporting guidance, contributor expectations, and deterministic GitHub Actions CI.

### Limitations

- All identities, business records, integrations, and governed documents are synthetic.
- No production OIDC/SSO/RBAC, HRIS, payroll, or help-desk integration.
- Shared public personas, conservative quotas, periodic reset, one worker, and no HA claim.
- Small 13-document / 47-chunk synthetic governed corpus.
- Victoria-focused Annual Leave assumptions and a finite trusted calendar horizon that fails closed.
- Provider availability affects knowledge and preparation requests.
- V4 development evaluation covered 11 of 15 applicable cases; the 11 observed cases passed, but this was not declared a full Development PASS.
- No V4 holdout was created.

Core thesis: **AI proposes and prepares. Deterministic systems retain authority over business mutation.**

## 0.5.0

Safe Annual Leave preparation, out-of-band authorization, and PostgreSQL-authoritative execution.

## 0.4.0

Provider-native READ/PREPARE agent tool use with a bounded loop and frozen evaluation evidence.

## 0.3.0

Authority-aware RAG, governed ingestion, pgvector retrieval, grounded answers, and server-built citations.

## 0.2.0

FastAPI, versioned routes, trusted synthetic identity, owner-scoped reads, and consistent error contracts.

## 0.1.0

Structured Gemini CLI foundation with provider-native schema output and deterministic mocks.

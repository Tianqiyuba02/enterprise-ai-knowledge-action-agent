# V2 Implementation Notes

## Current stage

Product Milestone V2 is in progress. Stage 1 completes only the knowledge-database foundation; it
does not mean V2 is complete. Stage 2 adds synthetic corpus ingestion but still does not implement
retrieval, knowledge-query HTTP behavior, grounded generation, public citations, or evaluation
runners.

## Stage 0 embedding verification

On 2026-08-25, a live capability check using the existing `google-genai` 2.19.0 SDK verified:

- stable model `gemini-embedding-2`;
- 768 numeric values per embedding;
- `RETRIEVAL_QUERY`;
- `RETRIEVAL_DOCUMENT`; and
- document-title input for retrieval-document embeddings.

No credentials or full vectors were recorded. ADR 0003 defines the approved embedding/index
profile and its separation from immutable document source identity.

## Stage 1 persistence

The synchronous V2 stack resolves to:

- SQLAlchemy 2.0.52;
- psycopg 3.3.4 with psycopg-binary 3.3.4;
- Alembic 1.19.1; and
- pgvector 0.5.0.

The database image is pinned to `pgvector/pgvector:0.8.6-pg17-bookworm`.

V1 employee profiles, leave balances, and tickets intentionally remain in `DemoRepository`. V2
PostgreSQL stores only `documents` and `document_chunks`. Database settings and connections are
loaded only by V2-specific paths, so a missing or unavailable PostgreSQL service does not change
the released V1 health, chat, or `/me/*` behavior.

Alembic owns schema history. Stage 1 does not use `create_all()` as a migration mechanism.

## Stage 2 corpus and ingestion

The 12-document fictitious Markdown/YAML corpus includes:

- an approved annual-leave version pair with explicit supersession;
- approved personal-leave and Victorian remote-work policies;
- an approved New South Wales wrong-jurisdiction trap;
- a managers-only audience trap;
- a draft office-animals proposal;
- a future-effective expense policy;
- an expired travel policy;
- two separate approved sources with intentionally conflicting after-hours guidance; and
- an approved IT procedure containing prompt-injection-like prose as untrusted content.

The strict metadata contract forbids unknown fields and controls source status, jurisdiction, and
audience vocabulary. Drafts cannot declare supersession. Source-authored `superseded` status is
parsed but cannot be inserted directly; an approved successor drives the stored predecessor
transition.

The SHA-256 source checksum covers canonical identity, title, declared status, dates, jurisdiction,
sorted audiences, explicit predecessor identity, and normalized Markdown body. It excludes file
location, database IDs/timestamps, stored supersession linkage, and embedding/index data. Newlines
are normalized to LF, trailing line spaces are removed, and the body has one final newline.

Heading-aware chunking uses deterministic non-whitespace lexical units rather than adding a model
tokenizer dependency. The defaults are a 400-unit target and 50-unit overlap. Headings, stable
slug anchors, section labels, global chunk indexes, and token counts are preserved.

Document embeddings use `gemini-embedding-2`, `RETRIEVAL_DOCUMENT`, the policy title, and 768-value
vectors through a V2-only client. Existing identity is a no-op before provider work only when source
checksum and embedding profile both match. Source changes and profile mismatches are explicit
failures; neither silently mutates stored rows.

For a new identity, all chunks and embeddings are prepared before the transaction. A PostgreSQL
transaction-level advisory lock serializes one `doc_code`, identity and predecessor state are
rechecked, documents/chunks are inserted, and any explicit approved predecessor transition commits
atomically. No Stage 2 schema migration was required.

The ingestion CLI supports:

```bash
uv run enterprise-ai-ingest file corpus/v2/03-personal-leave.md
uv run enterprise-ai-ingest corpus
```

## Local database commands

The Compose credentials are synthetic local-development values and are not production-safe.

```bash
docker compose -f infra/compose.yaml up -d
uv run alembic upgrade head
docker compose -f infra/compose.yaml down
```

To run the explicitly separated PostgreSQL integration suite while the database is running:

```bash
RUN_POSTGRES_TESTS=1 uv run pytest tests/integration/test_v2_knowledge_database.py
```

Ordinary provider-free verification remains:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## Stage 1 verification

Completed locally:

- pre-change baseline: 42 tests passed;
- ordinary suite: 51 passed, 19 PostgreSQL tests skipped explicitly;
- V0 regression suite: 23 passed;
- released V1 API suite: 19 passed;
- V2 offline foundation/isolation suite: 9 passed;
- Alembic head resolution and offline PostgreSQL SQL generation: passed; and
- Ruff lint and format checks: passed.

Live PostgreSQL verification completed with Docker 29.7.2 and Docker Compose v5.4.0:

- the pinned PostgreSQL container became healthy;
- Alembic upgraded the live database to `0001_v2_knowledge`;
- the vector extension was installed at version 0.8.6;
- `document_chunks.embedding` was confirmed as `vector(768)`; and
- all 19 PostgreSQL migration and constraint tests passed without skips.

Stage 2 live embedding-backed smoke verification inserted `POL-HR-002` v1.0 with four chunks using
`gemini-embedding-2/768`; PostgreSQL reported all stored vectors as dimension 768. No credential or
full vector was printed.

## Stage 2 verification

- ordinary provider-free suite: 91 passed, 33 explicitly gated PostgreSQL tests skipped;
- V2 offline foundation, corpus, parsing, chunking, embedding, CLI, and isolation suite: 49 passed;
- V0 regression suite: 23 passed;
- released V1 API suite: 19 passed;
- Stage 1 PostgreSQL migration/constraint regression suite: 19 passed;
- Stage 2 live ingestion/idempotency/supersession suite: 14 passed;
- deterministic corpus output: 12 documents and 42 chunks; and
- Ruff lint and format checks: passed.

Stage 2 adds no migration and no retrieval, semantic-search endpoint, grounded-answer generation,
public citation rendering, evaluation runner, agent, tool, LangGraph, or business-write behavior.

## Stage 3 authority-aware retrieval

`KnowledgeApplicabilityContext` is a separate frozen model derived from the existing authenticated
employee context and server-owned `DemoRepository` record. Both current Melbourne fixtures resolve
to `AU-VIC` with `all_employees` and `melbourne_employees`; neither receives `managers`.

Retrieval receives its date from an injectable trusted clock. The default clock uses
`Australia/Melbourne`. SQL eligibility uses `effective_date <= today` and
`expiry_date IS NULL OR expiry_date > today`, so an expiry date equal to today is ineligible.

The V2 Gemini adapter now supports one `RETRIEVAL_QUERY` embedding with the same
`gemini-embedding-2/768` profile. The linear internal service validates one question, embeds it,
uses the trusted applicability/date inputs, and returns typed evidence. It does not generate an
answer or a public response.

PostgreSQL filters approved status, dates, `GLOBAL`/trusted jurisdiction, and overlapping trusted
audiences in the `WHERE` predicate before exact pgvector ordering. Canonical ranking uses cosine
distance (`embedding <=> query_embedding`); the internal convenience similarity is
`1 - cosine_distance`. Initial `top_k` is 6 with no minimum similarity threshold.

Internal evidence includes server-only document/chunk UUIDs, document authority/applicability
metadata, section/anchor/page/content/token count, and cosine distance. It is not a public citation
model.

Deterministic PostgreSQL tests assign deliberately controlled vectors and prove that draft,
superseded, future, expired, wrong-jurisdiction, and wrong-audience rows cannot enter ranking. A
wrong-jurisdiction row with a perfect vector is excluded while a less-similar eligible row ranks.

The live corpus smoke confirmed:

- annual leave retrieved approved `POL-HR-001` v2.0 while v1.0 remained excluded;
- Victorian hybrid work retrieved `POL-WRK-001` while `POL-WRK-002` remained excluded;
- the managers-only `SOP-TRV-001` remained excluded for the non-manager context; and
- expired `POL-TRV-LEGACY` remained excluded.

Small heading-only chunks did occupy meaningful positions: the annual-leave title chunk ranked
second, the Victorian hybrid-work title ranked first, and several four/five-token title chunks
appeared elsewhere in top six. No chunking change was made; this observation is deferred to later
retrieval/evaluation evidence.

Without a calibrated threshold, unrelated low-similarity evidence can still fill top-k. Stage 3
does not claim refusal or insufficient-evidence behavior.

## Stage 3 verification

- ordinary provider-free suite: 108 passed, 39 explicitly gated PostgreSQL tests skipped;
- V0 regression suite: 23 passed;
- released V1 API suite: 19 passed;
- Stage 1 PostgreSQL migration/constraint regression suite: 19 passed;
- Stage 2 live ingestion/idempotency/supersession suite: 14 passed;
- Stage 3 live authority/filter/ranking suite: 6 passed;
- all live PostgreSQL suites together: 39 passed; and
- Ruff lint and format checks: passed.

Stage 3 adds no migration and no public knowledge route, grounded answer, public citation,
refusal/conflict response, evaluation runner, agent, tool, LangGraph, or business-write behavior.

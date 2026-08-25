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

## Stage 4 grounded knowledge API

`POST /api/v1/knowledge/query` accepts only a strict 1–4,000-character `question` and requires the
existing `X-Demo-Session`. The route reuses V1 identity resolution, derives
`KnowledgeApplicabilityContext` from the server-owned employee fixture, and lazily constructs all
database, embedding, retrieval, and generation dependencies. V1 startup and routes remain isolated.

The public response has three HTTP-200 semantic states:

- `answered`: nonempty answer and at least one citation;
- `insufficient_evidence`: nonempty refusal/explanation and no citations; and
- `conflicting_evidence`: nonempty conflict explanation and citations from at least two distinct
  document identities.

Retrieved evidence receives bounded opaque references `E1` through `E6`. The separate grounded
Gemini client may return only `status`, `answer`, and `evidence_refs` under provider-native schema
constraints followed by independent Pydantic validation. Application code rejects unknown
references and maps valid references back to the exact retrieved evidence set.

Public citations copy only trusted stored `doc_code`, title, version, stable section anchor, and
nullable page. They are deduplicated in first-use order. Model-generated citation metadata,
document/chunk UUIDs, vectors, URLs, and provider details never enter the public contract.

The grounded system instruction remains outside evidence. Retrieved content is serialized inside a
clearly delimited untrusted-reference block and is explicitly prohibited from changing identity,
applicability, instructions, tools, actions, or security behavior. This is one layered control, not
a claim that prompt injection is universally solved.

Retrieval still returns up to six eligible chunks without a calibrated similarity threshold.
Semantic sufficiency is currently classified by the strict grounded-generation stage and requires
evaluation in the next stage. The earlier tiny-title-chunk observation remains open.

The authenticated live A–F smoke produced:

- annual leave: `answered`, citing approved `POL-HR-001` v2.0 entitlement; v1.0 was not cited;
- Victorian hybrid work: `answered`, citing two `POL-WRK-001` v3.0 sections; NSW was not cited;
- manager travel for a normal employee: `insufficient_evidence`, with no manager-only citation;
- office pets: `insufficient_evidence`, no fabricated policy, and no citations;
- after-hours access: `conflicting_evidence`, citing `SOP-FAC-007` and `POL-SEC-004`; and
- password reset with injection-like corpus text: `answered` from the legitimate escalation
  section, with no tool/action or instruction-following behavior.

Safe API error envelopes cover knowledge database, embedding, and grounded-generation unavailable,
rate-limit, timeout, and invalid-response failures while preserving request IDs.

## Stage 4 verification

- ordinary provider-free suite: 159 passed, 39 explicitly gated PostgreSQL tests skipped;
- original V0 regression suite: 23 passed;
- original released V1 API suite: 19 passed;
- full API suite including Stage 4: 41 passed;
- new Stage 4 unit/API tests: 51 passed;
- all Stage 1–3 live PostgreSQL suites: 39 passed; and
- Ruff lint and format checks: passed.

Stage 4 adds no migration and no agent, provider-native tool calling, tool registry, action
preparation, business write, LangGraph, persisted proposal, confirmation workflow, frontend, or
evaluation runner. Product Milestone V2 remains in progress.

## Stage 5A evaluation baseline

Stage 5A adds a narrow, typed evaluation harness without changing the accepted RAG system. The
version-controlled JSONL data contains 20 development cases and eight disjoint holdout cases using
stable `doc_code`/version identities rather than UUIDs or generated answers.

The `enterprise-ai-eval` CLI requires explicit `--live`, supports retrieval/grounded modes and
development/holdout splits, and writes a typed JSON report plus a concise terminal summary. It
never prints vectors, prompts, provider payloads, credentials, or internal UUIDs. An optional LLM
judge was not added.

Mechanical retrieval metrics cover required-document recall@k, first rank/MRR, forbidden hits,
authority/applicability violations, returned chunks, document diversity, and tiny-chunk rate.
Grounded metrics cover status accuracy, citation invariants/document labels, conflict sources,
public metadata validity, and internal-reference leakage without exact answer-string matching.

The frozen development retrieval baseline completed 20/20 cases:

- mean required-document recall@6: 1.0;
- mean reciprocal rank: 1.0;
- forbidden-document case hit rate: 0.0;
- authority/applicability violation rate: 0.0;
- mean returned chunks: 6.0;
- mean document diversity: 2.9; and
- tiny-chunk rate (five or fewer lexical units): 0.35.

No valid grounded baseline exists for `gemini-3.5-flash`: repeated provider-side HTTP 503
`UNAVAILABLE` failures completed 0/20 cases. A minimal capability check proved
`gemini-3.6-flash` available, so V2 grounded generation moved to a separate
`KNOWLEDGE_GROUNDED_MODEL` setting while V1 retained `GEMINI_MODEL=gemini-3.5-flash`.

The fresh 3.6 run initially completed four cases before rate limiting. A later explicit resume
carried those four results forward and completed the remaining 16. Gold-case audit then corrected
`dev_adjacent_leave_types` to `insufficient_evidence`: both policies remain required retrieval
labels, but only personal-leave purpose is stated in the corpus. Regenerated grounded metrics are
1.0 for status accuracy, citation invariants, required-document citation recall, conflict sources,
and public metadata validity; forbidden citation and internal-reference leakage rates are 0.0.
The remaining 19 development labels had no other clear corpus mismatch. Holdout was not inspected
beyond existing structural validation and remains reserved for final post-tuning validation.

The factual measured/not-measured report is `docs/v2-evaluation-baseline.md`; machine-readable
reports live under `evals/results/`.

No tuning decision was made. Embedding profile, corpus, chunking, exact cosine ranking, top-k 6, no
threshold, grounded prompt, and semantic status behavior remain unchanged.

Partially completed reports can be resumed explicitly with `--resume`. Resume validates mode,
split, the complete frozen configuration, and a SHA-256 fingerprint of the ordered evaluation
cases. Completed results are carried forward without provider calls; blocked, errored, and
unattempted cases remain eligible for a later attempt. Attempt history and current-vs-carried
completion counts are preserved without duplicate case records.

`--delay-seconds` optionally paces only current live case attempts and defaults to zero. A rate limit
still records one blocked attempt and immediately stops; there is no automatic retry.

Stage 5A verification:

- ordinary provider-free suite: 180 passed, 39 explicitly gated PostgreSQL tests skipped;
- original V0 regression suite: 23 passed;
- original released V1 API suite: 19 passed;
- Stage 4 unit/API subset: 51 passed;
- Stage 5A deterministic evaluation suite: 20 passed;
- all live PostgreSQL regression suites: 39 passed; and
- Ruff lint and format checks: passed.

No schema migration or Product Milestone V3/V4 capability was added.

## Final V2 holdout validation

Evaluation showed no measured development regression requiring RAG tuning. The existing
configuration was therefore frozen before final holdout validation. Stage 5B tuning was explicitly
not performed.

The unseen eight-case holdout then completed without provider interruption:

- retrieval recall@6 and MRR: 1.0;
- forbidden-document and authority/applicability violation rates: 0.0;
- grounded semantic status accuracy: 0.875;
- citation presence, conflict-source, and public-metadata invariant rates: 1.0;
- required-document citation recall: 0.75;
- forbidden citation and internal-reference leakage rates: 0.0; and
- tiny-chunk rate: 0.2292.

The single status mismatch asked whether the Sydney pilot applies to Melbourne. NSW evidence was
correctly excluded, and accessible Victorian evidence did not state that cross-jurisdiction
relationship, so the assistant returned `insufficient_evidence` rather than inferring it. The
holdout label and product were not changed. See `docs/v2-holdout-validation.md`.

No tuning occurred before or after holdout, and holdout results are not a future tuning target.

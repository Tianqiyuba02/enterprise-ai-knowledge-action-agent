# Enterprise AI Knowledge & Action Agent

This repository contains the completed **Product Milestone V0** LLM foundation, **Product
Milestone V1** FastAPI and trusted-identity backend, **Product Milestone V2** authority-aware
RAG implementation released as `v0.3.0`, **Product Milestone V3** Agent + Tools released
as `v0.4.0`, and **Product Milestone V4** safe annual-leave action execution, proposed as
`v0.5.0`.

This is a local portfolio system, not a production-ready HR platform. The approved
product plan is in [`docs/project-kickoff-approved-1.0.md`](docs/project-kickoff-approved-1.0.md).
That kickoff still describes the original LangGraph V4 sketch; the live V4 design is the
later simplified PostgreSQL-authoritative execution path.

## Milestone Status

| Milestone | Status |
|---|---|
| V0 — Python + LLM API | ✅ Complete |
| V1 — FastAPI | ✅ Complete |
| V2 — Authority-Aware RAG | ✅ Complete — `v0.3.0` |
| V3 — Agent + Tools | ✅ Complete — `v0.4.0` |
| V4 — Safe Action Execution | Implementation complete — proposed `v0.5.0`; Development evaluation CLOSED — PARTIAL / PROVIDER-LIMITED |
| V5 — Evaluation + Deployment | Not started |

### V0 Verification

- Tests: **23 passed**
- Ruff lint: **passed**
- Ruff format check: **passed**
- Live Gemini structured-output smoke test: **passed**

### V1 Implementation Verification

- Tests: **42 passed** (23 preserved V0 tests + 19 V1 tests)
- Ruff lint: **passed**
- Ruff format check: **passed**
- API, OpenAPI, and manual validation: **passed**

### V2 Release Verification

- Ordinary tests: **180 passed**
- Live PostgreSQL migration/ingestion/retrieval tests: **39 passed**
- Development retrieval recall@6 / MRR: **1.0 / 1.0**
- Corrected development grounded status accuracy: **1.0** over 20 cases
- Frozen holdout retrieval recall@6 / MRR: **1.0 / 1.0**
- Frozen holdout grounded status accuracy: **0.875** over 8 cases
- Holdout citation, conflict, public-metadata, authority, and leakage invariants: **passed**
- Ruff lint and format checks: **passed**

These synthetic evaluation sets are small and do not establish statistical significance.

### V3 Release-Candidate Verification

Development and frozen holdout are separate campaigns. They are not one 24-case benchmark.

**Development** (`v3-agent-eval-2`):

- 16/16 completed
- 0 expectation misses

**Frozen holdout** (first exposure):

- 8/8 completed/evaluable
- 0 expectation misses
- no holdout tuning
- no holdout gold changes
- prompt-injection undesired-call rate: N/A (`null`; no completed holdout case carried that label)

**Independent pre-holdout review:** PASS — 0 blockers, 0 high

**Final release-candidate review:** PASS — 0 blockers, 0 high

V3 `v0.4.0` is published.

- Release commit: `d396122d368be8c4849872c233460da09a857b17`
- Annotated tag: `v0.4.0`
- GitHub Release: https://github.com/Tianqiyuba02/enterprise-ai-knowledge-action-agent/releases/tag/v0.4.0

### V4 Release Verification

V4 live execution is **not** LangGraph. After out-of-band confirmation, a PostgreSQL
poller claims a `CONFIRMED` action, locks the revision, takes an employee advisory
transaction lock, revalidates, and commits the leave mutation, final state, and audit
together.

Post-simplification Development evaluation is **CLOSED — PARTIAL / PROVIDER-LIMITED**.
It is not a Development PASS, not 15/15 PASS, and not a holdout PASS.

- Applicable denominator: 15
- Semantic evidence: 11 / 15
- Observed semantic results: 11 / 11 PASS
- Semantic failures: 0
- Product/business misses: 0
- Safety/authority misses: 0
- Uncovered applicable cases: `e1`, `e2`, `e4`, `f1`
- N/A: `dev_v4_e3_unknown_blocks_replace` (retired `UNKNOWN_OUTCOME` architecture)
- Provider limitation: intermittent Gemini HTTP 429 / `RESOURCE_EXHAUSTED`
- V4 holdout: **NOT CREATED / DEFERRED**

See [`docs/v4-product-evaluation.md`](docs/v4-product-evaluation.md).

## What V0 demonstrates

- a Python 3.12+ `src` project layout;
- reproducible dependency management with `uv` and `uv.lock`;
- environment-only secret configuration with Pydantic Settings;
- one provider: Google Gemini via the official `google-genai` SDK;
- provider-native JSON Schema output plus independent Pydantic validation;
- bounded timeout/retry settings and safe handling of authentication, rate-limit, timeout, service,
  and malformed-output failures;
- a deliberately small CLI; and
- offline unit tests with all provider calls mocked.

## What V1 adds

- FastAPI with versioned REST endpoints under `/api/v1`;
- Pydantic request, response, and consistent error-envelope models;
- server-resolved synthetic employee identity through `X-Demo-Session`;
- deterministic profile, leave-balance, and ownership-scoped ticket reads;
- a small service/repository separation over fictitious seeded data;
- request IDs returned in `X-Request-ID` and structured error responses;
- OpenAPI and Swagger documentation; and
- offline API, identity, ownership, validation, and regression tests.

## What V2 adds (`v0.3.0`)

V2 adds:

- a PostgreSQL + pgvector knowledge store with Alembic schema history;
- versioned, checksum-validated, idempotent Markdown/YAML ingestion with atomic supersession;
- `gemini-embedding-2` document/query embeddings using 768 dimensions;
- server-derived jurisdiction and audience applicability;
- approved/effective/applicable filtering in SQL before exact cosine ranking;
- grounded Gemini answers over explicitly untrusted evidence blocks;
- server-validated citations built only from retrieved stored metadata;
- `answered`, `insufficient_evidence`, and `conflicting_evidence` outcomes;
- authenticated `POST /api/v1/knowledge/query`;
- version-controlled development and frozen holdout evaluation; and
- safe database, embedding, generation, timeout, rate-limit, and malformed-output errors.

With local `.env` configuration:

```bash
docker compose -f infra/compose.yaml up -d
uv run alembic upgrade head
uv run enterprise-ai-ingest corpus
```

Corpus ingestion makes real Gemini embedding calls. It prints document identity, chunk count, and
the embedding profile, never vectors or credentials.

The frozen development retrieval baseline can be measured explicitly with:

```bash
uv run enterprise-ai-eval --mode retrieval --split development --live
```

Development evidence justified no RAG tuning, so configuration was frozen before the final holdout
run. Results are documented in `docs/v2-holdout-validation.md`. Compatible partial reports support
explicit `--resume`; evaluator-only `--delay-seconds` defaults to zero.

The V3 development agent baseline uses the real bounded agent and a fixed trusted date:

```bash
uv run enterprise-ai-eval --mode agent --split development --live --delay-seconds 2
```

The V3 agent holdout passed on first authorized exposure. Accidental
`--mode agent --split holdout` remains rejected. An authorized campaign still requires explicit
`--authorize-holdout`. See `docs/v3-agent-evaluation.md` for the mechanical metrics, campaign
result, and adjudication record, and `docs/v3-release-readiness.md` for the published `v0.4.0`
status. V3 product code remains frozen at the published release.

V4 development evaluation used the existing live-provider harness. That campaign is
closed as PARTIAL / PROVIDER-LIMITED. Do not treat it as holdout. `--split holdout`
remains rejected because a V4 holdout does not exist.

## What V4 adds (proposed `v0.5.0`)

V4 adds safe executable annual-leave actions:

- deterministic PREPARE from trusted identity and calendar authority;
- out-of-band HITL confirmation (chat "yes" cannot confirm);
- PostgreSQL-authoritative action state;
- database-enforced occupancy and leave idempotency;
- employee-level advisory-lock serialization;
- atomic leave mutation, final state, and audit in one COMMIT;
- crash/concurrency safety on the simplified poller path.

Development evaluation remains **CLOSED — PARTIAL / PROVIDER-LIMITED** (11/15
applicable semantic evidence). This is not a full semantic validation and not a
holdout result.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) installed;
- a Gemini API key for real CLI requests; and
- internet access only when installing dependencies or making a real model request.

`uv` will use the Python version in `.python-version`. If Python 3.12 is not already available, run:

```bash
uv python install 3.12
```

## Setup

From the repository root:

```bash
uv sync --dev
cp .env.example .env
```

Edit `.env` and replace the placeholder:

```dotenv
GEMINI_API_KEY=your-real-api-key
```

`.env` is ignored by Git. Do not add a real key to `.env.example`, source code, tests, shell history,
or documentation. The optional model, timeout, and retry settings are documented in `.env.example`.

## Run the API

Start the development server:

```bash
uv run uvicorn app.main:app --reload
```

OpenAPI JSON is available at `http://127.0.0.1:8000/openapi.json` and Swagger UI at
`http://127.0.0.1:8000/docs`.

### Demo sessions

The `/api/v1/me/*` routes require an `X-Demo-Session` header. These fixed values identify only
fictitious seeded employees and are not production credentials:

| Header value | Synthetic employee |
|---|---|
| `demo-v1-7f4c2a91` | Alex Morgan (`EMP-1001`) |
| `demo-v1-3b8e6d50` | Sam Lee (`EMP-1002`) |

Example:

```bash
curl -H 'X-Demo-Session: demo-v1-7f4c2a91' \
  http://127.0.0.1:8000/api/v1/me/profile
```

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Typed liveness response |
| `POST` | `/api/v1/chat` | Existing schema-validated Gemini capability |
| `POST` | `/api/v1/knowledge/query` | Authenticated grounded policy answer with citations |
| `POST` | `/api/v1/assistant/query` | Authenticated bounded read/prepare orchestration; may persist a V4 action |
| `GET` | `/api/v1/actions/{action_id}` | Authenticated owner read of a persisted V4 action |
| `POST` | `/api/v1/actions/{action_id}/confirmation-challenges` | Issue an out-of-band confirmation challenge |
| `POST` | `/api/v1/actions/{action_id}/confirm` | Confirm a challenged action with the returned token |
| `POST` | `/api/v1/actions/{action_id}/cancel` | Cancel an owner action that is still cancellable |
| `GET` | `/api/v1/me/profile` | Authenticated synthetic employee's profile |
| `GET` | `/api/v1/me/leave/balances` | Authenticated employee's seeded balances |
| `GET` | `/api/v1/me/tickets/{ticket_id}` | Ownership-scoped ticket status/details |

Chat, knowledge, and assistant queries require `GEMINI_API_KEY`; knowledge-backed and V4 action
requests also require PostgreSQL. Health and seeded `/me/*` reads start and work without those
dependencies.

Confirmed V4 actions are executed by the internal poller, not by chat text:

```bash
uv run enterprise-ai-workflow-worker --once
```

## Run the V0 CLI

Pass one question as a quoted argument:

```bash
uv run enterprise-ai "Please help me reset my payroll portal password"
```

Equivalent module form:

```bash
uv run python -m app.main "How do I submit a taxi reimbursement?"
```

A successful response is printed as four readable, validated fields. Known failures print one safe
error message to standard error and exit non-zero without a traceback.

## Test and lint

Ordinary V0, V1, V2, V3, and V4 deterministic tests are offline and require no internet, API key, or
paid provider call:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

PostgreSQL and live-provider gated tests remain separately identified in the suite. A manual run
of the CLI is the explicit live smoke test when credentials are available.

## Development Workflow

```text
feature branch
      ↓
develop
      ↓
milestone implementation + engineering review
      ↓
main
      ↓
annotated tag
      ↓
GitHub Release
```

- Feature branches originate from `develop` and remain short-lived.
- Completed milestone work integrates into `develop`.
- After milestone implementation and engineering review pass, milestone-complete code merges to
  `main`.
- Each completed milestone on `main` receives an annotated version tag and a GitHub Release.
- Pull Requests may be used as review checkpoints in this solo project, but are not mandatory for
  every small documentation change.

Release mapping: V0 → `v0.1.0`, V1 → `v0.2.0`, V2 → `v0.3.0`, and V3 → `v0.4.0`. Planned
future mapping is V4 → `v0.5.0` and portfolio-ready V5 → `v1.0.0`. Tags are created only after
milestone review and merge to `main`. `v0.4.0` is published.

## Project structure

```text
enterprise-ai-knowledge-action-agent/
├── corpus/v2/              # 12 fictitious authority-labelled Markdown documents
├── docs/                   # approved kickoff, ADRs, implementation/evaluation evidence
├── evals/                  # development/holdout JSONL and machine-readable reports
├── infra/compose.yaml      # database-only PostgreSQL + pgvector
├── migrations/             # Alembic knowledge-schema history
├── src/app/
│   ├── agent/              # V3 bounded read/prepare loop and deterministic tools
│   ├── api/                # V1/V2 routes plus the authenticated V3 read assistant
│   ├── db/                 # synchronous SQLAlchemy knowledge models/sessions
│   ├── embeddings/         # narrow Gemini embedding boundary
│   ├── evaluation/         # typed metrics, reports, runner, and resumable CLI
│   ├── grounding/          # untrusted-evidence prompt and structured generation
│   ├── ingestion/          # parser, checksum, chunking, transaction, and CLI
│   ├── knowledge/          # applicability, retrieval, citations, and query service
│   ├── llm/                # preserved V0/V1 Gemini analysis boundary
│   ├── repositories/       # seeded V1 employee data
│   ├── services/           # V1 application services
│   └── workflow/           # V4 PREPARE, confirmation, poller, and atomic leave execution
├── tests/                  # V0–V4 unit/API tests and gated PostgreSQL integration tests
├── .env.example
├── .gitignore
├── .python-version
├── pyproject.toml
├── uv.lock
└── README.md
```

## Current limitations

V2 remains a local portfolio system over a small fictitious corpus. Identity uses fixed synthetic
sessions rather than OAuth/OIDC/SSO/RBAC, and V1 employee data remains process-local. The
application has no conversation persistence, calibrated similarity threshold, statistical-quality
claim, or production document connector.

Tiny title chunks can consume retrieval positions, although development/holdout required-document
recall and MRR remained 1.0. One frozen holdout case produced a conservative refusal where its gold
label expected an answer; the label and product were intentionally not changed after holdout.
Prompt-injection controls are layered mitigations, not universal protection.

V2 explicitly does **not** include agents, provider-native tool calling, LangGraph, business writes,
action preparation/confirmation, MCP, multi-agent systems, application containerization, enterprise
integrations, or a frontend. Those capabilities remain outside Product Milestone V2.

V3 remains READ + PREPARE only. Mixed-form relative-weekday requests may be over-constrained and
fail closed; that limitation is deferred as post-V3 hardening and is not fixed in V3.

V4 adds persisted annual-leave actions, out-of-band confirmation, and PostgreSQL-authoritative
execution. It is not a production HR platform: identity is still synthetic demo sessions, the
corpus is fictitious, and Development evaluation coverage is provider-limited (11/15 applicable
cases). A V4 holdout was not created. The original kickoff LangGraph/checkpoint design is
historical and is not the live execution path.

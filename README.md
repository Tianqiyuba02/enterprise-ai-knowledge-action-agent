# Enterprise AI Knowledge & Action Agent

This repository contains the completed **Product Milestone V0** LLM foundation, **Product
Milestone V1** FastAPI and trusted-identity backend, and **Product Milestone V2** authority-aware
RAG implementation prepared for release as `v0.3.0`.

This is not yet the finished enterprise assistant. The approved product plan is in
[`docs/project-kickoff-approved-1.0.md`](docs/project-kickoff-approved-1.0.md).

## Milestone Status

| Milestone | Status |
|---|---|
| V0 — Python + LLM API | ✅ Complete |
| V1 — FastAPI | ✅ Complete |
| V2 — Authority-Aware RAG | ✅ Complete — `v0.3.0` |
| V3 — Agent + Tools | Code freeze — read + leave-prepare; live evaluation in progress |
| V4 — LangGraph + HITL | Not started |
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

The V3 agent holdout is frozen and has not been executed. See
`docs/v3-agent-evaluation.md` for the mechanical metrics and resume contract, and
`docs/v3-release-readiness.md` for the current `v0.4.0` gate. V3 product code is frozen;
V3 is not released.

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
| `POST` | `/api/v1/assistant/query` | Authenticated bounded read/prepare orchestration |
| `GET` | `/api/v1/me/profile` | Authenticated synthetic employee's profile |
| `GET` | `/api/v1/me/leave/balances` | Authenticated employee's seeded balances |
| `GET` | `/api/v1/me/tickets/{ticket_id}` | Ownership-scoped ticket status/details |

Chat, knowledge, and assistant queries require `GEMINI_API_KEY`; knowledge-backed requests also
require PostgreSQL. Health and seeded `/me/*` reads start and work without those dependencies.

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

All ordinary V0 and V1 tests require no internet, API key, or paid provider call:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

There is no live-provider test in the automated suite. A manual run of the CLI is the explicit live
smoke test when credentials are available.

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

Release mapping: V0 → `v0.1.0`, V1 → `v0.2.0`, and V2 → `v0.3.0`. Planned future mapping is
V3 → `v0.4.0`, V4 → `v0.5.0`, and portfolio-ready V5 → `v1.0.0`. Tags are created only after
milestone review and merge to `main`.

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
│   └── services/           # V1 application services
├── tests/                  # V0/V1, V2 unit/API, and gated PostgreSQL integration tests
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

# Enterprise AI Knowledge & Action Agent

This repository contains the released **Product Milestone V0** LLM foundation and the
release-candidate implementation of **Product Milestone V1**: a small FastAPI backend with typed
REST contracts and a trusted synthetic employee-identity boundary. V1 has passed final engineering
review but has not yet been released.

This is not yet the finished enterprise assistant. The approved product plan is in
[`docs/project-kickoff-approved-1.0.md`](docs/project-kickoff-approved-1.0.md).

## Milestone Status

| Milestone | Status |
|---|---|
| V0 — Python + LLM API | ✅ Complete |
| V1 — FastAPI | Release candidate; final review passed |
| V2 — RAG | Not started |
| V3 — Agent + Tools | Not started |
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
| `GET` | `/api/v1/me/profile` | Authenticated synthetic employee's profile |
| `GET` | `/api/v1/me/leave/balances` | Authenticated employee's seeded balances |
| `GET` | `/api/v1/me/tickets/{ticket_id}` | Ownership-scoped ticket status/details |

Only the chat endpoint requires `GEMINI_API_KEY`. Health and seeded `/me/*` reads start and work
without provider credentials.

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

Planned release mapping: V0 → `v0.1.0`, V1 → `v0.2.0`, V2 → `v0.3.0`, V3 → `v0.4.0`,
V4 → `v0.5.0`, and portfolio-ready V5 → `v1.0.0`. Future tags are created only when their
milestones pass review.

## Project structure

```text
enterprise-ai-knowledge-action-agent/
├── docs/
│   ├── adr/
│   │   ├── 0001-primary-llm-provider.md
│   │   └── 0002-trusted-demo-identity.md
│   ├── project-kickoff-approved-1.0.md
│   └── v1-implementation-notes.md
├── src/
│   └── app/
│       ├── api/
│       │   ├── routes/
│       │   │   ├── chat.py
│       │   │   ├── health.py
│       │   │   └── me.py
│       │   ├── application.py
│       │   ├── dependencies.py
│       │   ├── errors.py
│       │   └── models.py
│       ├── __init__.py
│       ├── config.py
│       ├── errors.py
│       ├── identity.py
│       ├── main.py
│       ├── llm/
│       │   ├── __init__.py
│       │   ├── client.py
│       │   └── models.py
│       ├── repositories/
│       │   └── demo.py
│       └── services/
│           ├── chat.py
│           ├── employee.py
│           └── it.py
├── tests/
│   ├── api/
│   │   ├── test_health_and_chat.py
│   │   ├── test_identity_and_me.py
│   │   └── test_openapi_and_errors.py
│   ├── test_llm_client.py
│   └── test_models.py
├── .env.example
├── .gitignore
├── .python-version
├── pyproject.toml
├── uv.lock
└── README.md
```

ADRs record only the consequential provider and trusted-identity decisions. No directories have been
created for future milestone functionality.

## Current limitations

V1 remains a local portfolio backend. Identity uses fixed synthetic sessions rather than OAuth,
OIDC, SSO, or production RBAC. Employee data is fictitious and in memory; it resets with the process.
The chat endpoint remains a single structured LLM request with no conversation persistence or
company-knowledge grounding.

V1 explicitly does **not** contain RAG, embeddings, agents, tool calling, LangGraph, LangChain,
PostgreSQL, pgvector, vector databases, business writes, MCP, Docker application images, multi-agent
systems, enterprise integrations, or a frontend. Those capabilities remain outside this milestone.

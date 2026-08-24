# Enterprise AI Knowledge & Action Agent

This repository contains **Product Milestone V0**: the smallest working Python application that
sends one employee question to one LLM provider and accepts the result only after structured-output
schema validation.

V0 is an engineering foundation, not the finished enterprise assistant. The approved product plan is
in [`docs/project-kickoff-approved-1.0.md`](docs/project-kickoff-approved-1.0.md).

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

## Run

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

Ordinary tests require no internet, API key, or paid provider call:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

There is no live-provider test in the automated suite. A manual run of the CLI is the explicit live
smoke test when credentials are available.

## Project structure

```text
enterprise-ai-knowledge-action-agent/
├── docs/
│   ├── adr/
│   │   └── 0001-primary-llm-provider.md
│   └── project-kickoff-approved-1.0.md
├── src/
│   └── app/
│       ├── __init__.py
│       ├── config.py
│       ├── main.py
│       └── llm/
│           ├── __init__.py
│           ├── client.py
│           └── models.py
├── tests/
│   ├── test_llm_client.py
│   └── test_models.py
├── .env.example
├── .gitignore
├── .python-version
├── pyproject.toml
├── uv.lock
└── README.md
```

The provider ADR exists because selecting the previously unresolved V0 provider is a consequential
decision. No directories have been created for future milestone functionality.

## Current limitations

V0 classifies and summarizes one question at a time. It has no conversation persistence, company
knowledge, business-system access, user authentication, HTTP API, or user interface.

V0 explicitly does **not** contain FastAPI, RAG, embeddings, agents, tool calling, LangGraph,
LangChain, PostgreSQL, pgvector, vector databases, MCP, Docker application images, multi-agent
systems, or frontend frameworks. Those capabilities remain outside this milestone.

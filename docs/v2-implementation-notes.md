# V2 Implementation Notes

## Current stage

Product Milestone V2 is in progress. Stage 1 completes only the knowledge-database foundation; it
does not implement ingestion, chunking, retrieval, knowledge-query HTTP behavior, grounded
generation, citations, or evaluation runners. Stage 1 completion does not mean V2 is complete.

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

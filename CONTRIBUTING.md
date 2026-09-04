# Contributing

This is a portfolio repository, but focused fixes and evidence-backed improvements are welcome.

## Setup

Use Python 3.12+, `uv`, Node.js 22, npm, Docker, and Docker Compose.

```bash
uv sync --locked --dev
cd ui
npm ci
```

Copy `.env.example` or `ui/.env.example` only when local runtime configuration is needed. Never commit populated environment files.

## Safety boundaries

Changes must preserve these invariants:

- the model remains READ/PREPARE-only;
- chat text never authorizes execution;
- identity is resolved by the server, not an arbitrary browser `employee_id`;
- Review displays the persisted authoritative draft;
- confirmation binds to the current action revision;
- only the private worker performs deterministic business mutation;
- Annual Leave and IT Support keep explicit domain rules;
- no secrets, private infrastructure identifiers, or real business data enter the repository.

Avoid widening product scope in a bug fix. Do not weaken ownership checks, expiry, transaction locking, idempotency constraints, or audit evidence for UI convenience.

## Validation

Run the deterministic default gates:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
cd ui
npm run lint
npm run typecheck
npm run build
npm run test:e2e
```

PostgreSQL integration tests require the local pgvector service and are deliberately opt-in:

```bash
docker compose -f infra/compose.yaml up -d
RUN_POSTGRES_TESTS=1 uv run pytest tests/integration
```

Do not run provider evaluation as part of a normal pull request.

## Pull requests

Describe the problem, the smallest chosen change, affected trust boundaries, and exact validation results. Link tests to changed behavior. Keep generated runtime data, test databases, build output, screenshots containing non-synthetic input, and local absolute paths out of commits.

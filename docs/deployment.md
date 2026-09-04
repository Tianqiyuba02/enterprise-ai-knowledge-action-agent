# Deployment

This document covers the reviewed local production shape and the current Render topology. It lists configuration names, never secret values or private resource identifiers.

## Local production topology

1. Start PostgreSQL 17 with pgvector using `infra/compose.yaml`.
2. Apply migrations with `uv run alembic upgrade head`.
3. Ingest the governed corpus with `uv run enterprise-ai-ingest corpus` when the verified baseline is absent.
4. Run FastAPI privately with `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`.
5. Run one action worker with `uv run enterprise-ai-demo-worker --poll-seconds 1`.
6. Build and run the portal from `ui/`, with `BACKEND_URL` pointing to FastAPI.

The expected migration head is `0008_v5_m3_public_demo`. The reviewed governed baseline is 13 documents and 47 chunks.

## Render topology

`render.yaml` declares:

- one public Docker web service for Next.js and its BFF;
- one private Docker service for FastAPI;
- one private Docker worker for confirmed actions;
- one private scheduled Docker job for synthetic reset;
- one private-network PostgreSQL 17 database with pgvector.

Only Next.js receives public traffic. The BFF adds the fixed synthetic demo identity and internal portal key on the server. The worker and reset job connect directly to PostgreSQL.

## Configuration names

| Service | Required server-side names |
|---|---|
| Portal | `BACKEND_HOSTPORT`, `INTERNAL_PORTAL_KEY`, `VISITOR_COOKIE_SECRET`, optional `GITHUB_REPOSITORY_URL` |
| API and worker | `APP_DATABASE_URL`, `INTERNAL_PORTAL_KEY`, `PUBLIC_DEMO_MODE`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TIMEOUT_SECONDS`, `GEMINI_MAX_ATTEMPTS`, `AGENT_TIMEOUT_SECONDS`, `AGENT_MAX_ATTEMPTS`, `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW` |
| Reset | `APP_DATABASE_URL`, `INTERNAL_PORTAL_KEY`, `PUBLIC_DEMO_MODE` |

`GITHUB_REPOSITORY_URL` must remain empty until anonymous access to the intentionally public repository is verified. Never place server-only names behind a `NEXT_PUBLIC_` prefix.

## Migration and governed bootstrap

The private API pre-deploy command is `enterprise-ai-demo-bootstrap`. It upgrades to the current Alembic head, verifies the governed baseline, and uses the existing ingestion path only when the baseline is missing. It fails closed when migration or corpus verification is incomplete.

The bootstrap may call the configured embedding provider when ingestion is required. Normal application startup must not silently replace governed content.

## Synthetic reset

`enterprise-ai-demo-reset` enters maintenance, waits for active state-changing work, restores only the mutable synthetic baseline, verifies it, and exits maintenance only on success. It preserves governed knowledge and versioned calendar authority. The browser’s “Start fresh” control only clears presentation state; it is not an operational reset endpoint.

## Readiness

Verify the public portal readiness projection before sharing or recording the demo. A ready result requires:

- database reachable;
- migration at `0008_v5_m3_public_demo`;
- governed corpus verified at 13 documents / 47 chunks;
- maintenance inactive;
- private worker heartbeat healthy.

Readiness contains no secret values or private hostnames.

## Manual deployment

`autoDeployTrigger` remains off. Deployments therefore require deliberate selection of the reviewed source commit in Render. Record the exact commit, deploy the API/bootstrap, worker, reset if needed, and portal as one release-candidate set. Do not assume that pushing a branch changed the live deployment.

## Rollback

1. Disable public portal traffic if the trust boundary or data integrity is uncertain.
2. Retain PostgreSQL; do not automatically delete or downgrade shared state.
3. Select the last reviewed compatible application commit for all services.
4. Run its migration compatibility and governed-baseline checks.
5. Confirm reset state and worker heartbeat.
6. Re-enable the portal only after private readiness and deterministic smoke checks pass.

Rollback is a source-version operation, not an excuse to weaken confirmation, expiry, or transaction semantics.

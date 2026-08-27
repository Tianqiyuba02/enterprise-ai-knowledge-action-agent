# V4 Implementation Notes

## Current stage

V4 Stage 3 is an out-of-band confirmation control plane plus a durable confirmation-wake
worker. It does **not** implement leave submission execution, `CONFIRMED → EXECUTING`,
fencing, or reconciliation of a business mutation.

Project version remains `0.4.0`. Sealed V3 `v0.4.0` is unchanged.

Architecture authority remains [`docs/v4-architecture-freeze-1.0.md`](v4-architecture-freeze-1.0.md).

## What exists

- Stage 1 persistence and the trusted VIC holiday calendar seed.
- Stage 2 durable LangGraph interrupt/resume with Alembic-controlled PostgresSaver 3.1.2.
- Stage 3A authenticated action read, hashed confirmation challenges, T3 confirmation, and
  cancel. Confirmation replay is idempotent. `CONFIRMED` plus the outbox row commit atomically.
- Stage 3B a separate `enterprise-ai-workflow-worker` that claims `confirmation_committed`
  outbox events and wakes the persisted LangGraph thread.

## Confirmation control plane

- Authentication is trusted `X-Demo-Session` only.
- Owner and session bindings are server-loaded. Clients cannot supply identity, revision,
  draft hash, or workflow state.
- Challenge tokens are 256-bit `secrets.token_hex` values. Only SHA-256 digests are stored
  and compared with `hmac.compare_digest`.
- Demo TTLs: `V4_CONFIRMATION_CHALLENGE_TTL_SECONDS=600` and `V4_CONFIRMED_TTL_SECONDS=600`.
  These are not an MFA/security certification claim.
- Chat text and graph `Command(resume=...)` cannot confirm.
- Cancellation is allowed from `AWAITING_CONFIRMATION` and `CONFIRMED` only.

Transaction lock order:

1. `action_workflows`
2. `action_revisions`
3. `confirmation_challenges`
4. `workflow_outbox` insert
5. `action_audit_events` insert

## Worker

The worker is an internal system actor. It does not manufacture employee authorization from
outbox metadata. It loads `action_id` / revision from PostgreSQL and uses the persisted
`action_workflows.langgraph_thread_id`.

At-least-once delivery is expected. A successful wake to `confirmed_barrier` or a valid
`terminal_barrier` (`CANCELLED` / `EXPIRED` / `STALE`) may be marked delivered. If PostgreSQL
still says `AWAITING_CONFIRMATION` for an `ACTION_CONFIRMED` event, that is an invariant
failure: the event is released for retry and the database is not forced to `CONFIRMED`.

Checkpoint loss/corruption is an orchestration failure, not authority to guess workflow state.

## LangGraph role

PostgreSQL remains authority. Resume remains a wake signal only.

The graph still stops at `confirmed_barrier` when PostgreSQL is `CONFIRMED`. Routing
`EXECUTING` away from execution is a temporary Stage 2/3 safety boundary and must be
revisited only when Stage 4 execution is explicitly authorized.

Pinned versions remain `langgraph==1.2.11` and `langgraph-checkpoint-postgres==3.1.2`.
Checkpoint schema remains Alembic 0003 in `public`. Runtime `setup()` remains forbidden.

## Not implemented

- `CONFIRMED → EXECUTING`
- execution reservation, leases, fencing, or reconciliation
- `leave_requests` submission
- LLM execution tools or Gemini calls from workflow
- production MFA
- end-to-end leave action execution
- HITL execution of the business mutation

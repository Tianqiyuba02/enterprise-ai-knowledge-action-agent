# V4 Implementation Notes

## Current stage

V4 Stage 4 adds a deterministic submitted-leave execution engine, generation
fencing, per-employee mutation serialization, cross-action business dedupe, and
durable UNKNOWN_OUTCOME reconciliation. Persistent LangGraph can orchestrate
execution after out-of-band confirmation.

Project version remains `0.4.0`. Sealed V3 `v0.4.0` is unchanged.

Architecture authority remains [`docs/v4-architecture-freeze-1.0.md`](v4-architecture-freeze-1.0.md).

## What exists

- Stage 1 persistence and the trusted VIC holiday calendar seed.
- Stage 2 durable LangGraph interrupt/resume with Alembic-controlled PostgresSaver 3.1.2.
- Stage 3A authenticated action read, hashed confirmation challenges, T3 confirmation, and
  cancel. Confirmation replay is idempotent. `CONFIRMED` plus the outbox row commit atomically.
- Stage 3B a separate `enterprise-ai-workflow-worker` that claims `confirmation_committed`
  outbox events and wakes the persisted LangGraph thread.
- Stage 4A an unwired deterministic execution core: holiday-aware revalidation, T4
  reservation, immutable execution keys, same-Postgres leave submission, and fencing.
- Stage 4B worker/graph integration: reservation, business execution, T5 finalization,
  and at most three automatic reconciliation attempts.

## Confirmation control plane

- Authentication is trusted `X-Demo-Session` only.
- Owner and session bindings are server-loaded. Clients cannot supply identity, revision,
  draft hash, or workflow state.
- Challenge tokens are 256-bit `secrets.token_hex` values. Only SHA-256 digests are stored
  and compared with `hmac.compare_digest`.
- Demo TTLs: `V4_CONFIRMATION_CHALLENGE_TTL_SECONDS=600`, `V4_CONFIRMED_TTL_SECONDS=600`,
  and `V4_EXECUTION_LEASE_TTL_SECONDS=60`. These are not an MFA/security certification claim.
- Chat text and graph `Command(resume=...)` cannot confirm.
- Cancellation is allowed from `AWAITING_CONFIRMATION` and `CONFIRMED` only.
- Local cancellation is forbidden after execution begins.

Transaction lock order for confirmation:

1. `action_workflows`
2. `action_revisions`
3. `confirmation_challenges`
4. `workflow_outbox` insert
5. `action_audit_events` insert

## Execution

- Execution is gated by authoritative Postgres `CONFIRMED` plus fresh deterministic
  revalidation plus a successful T4 reservation. Graph arrival is not authority.
- The first reservation generates one 256-bit `execution_key`. It is never replaced.
- A SHA-256-derived signed 64-bit transaction advisory lock serializes the same
  `business_request_key`. A second unresolved attempt is retryable, not a definite failure.
- The worker `worker_id` is the lease owner. Employee subject/session identity is not reused.
- The same-Postgres `LeaveSubmissionExecutor` is the only business system. There is no
  external HR adapter.
- Final mutation locks the ledger `FOR UPDATE`, takes a per-employee advisory lock, and
  rechecks fencing, business-key dedupe, overlap, effective balance, and calendar coverage
  before `INSERT leave_requests`.
- Exact `execution_key` replay and cross-action `business_request_key` matches are APPLIED
  without a second row.
- `OUTCOME_UNKNOWN` persists before reconciliation scheduling. Recovery uses the original
  key plus business key. After three automatic attempts the revision stays
  `UNKNOWN_OUTCOME` with `manual_review_required=true`.
- Stale lease generations perform zero mutation.

## Worker

The worker is an internal system actor. It does not manufacture employee authorization from
outbox metadata. It loads `action_id` / revision from PostgreSQL and uses the persisted
`action_workflows.langgraph_thread_id`.

A confirmation wake may proceed into execution. The event is marked delivered only after a
settled terminal or persisted `UNKNOWN_OUTCOME`. If reservation is blocked by another
unresolved attempt for the same business key, the event is released for retry.

`reconcile_requested` events are a separate outbox identity and use independent backoff.

Checkpoint loss/corruption is an orchestration failure, not authority to guess workflow state.

## LangGraph role

PostgreSQL remains authority. Resume remains a wake signal only.

The graph keeps an observable confirmed barrier, then continues to reserve / execute /
finalize when a worker execution port is present. Employee `start` / `resume` compile the
same topology without that port and therefore cannot submit leave.

Pinned versions remain `langgraph==1.2.11` and `langgraph-checkpoint-postgres==3.1.2`.
Checkpoint schema remains Alembic 0003 in `public`. Runtime `setup()` remains forbidden.

## Not implemented

- V3 assistant PREPARE is not integrated into automatic V4 action creation
- no assistant `action_id` response
- no LLM execution tool
- no chat-text confirmation
- no production MFA
- executor is the same-Postgres demo business system
- external HR adapters are not implemented
- leave cancellation is not implemented

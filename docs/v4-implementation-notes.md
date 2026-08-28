# V4 Implementation Notes

## Current stage

V4 Stage 6A added a versioned product-evaluation harness and a 16-case
DEVELOPMENT set. Stage 6A.1 hardens evaluation reproducibility. The
development set has not been run. This is not a V4 development-evaluation
pass, holdout, or release-readiness claim.

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
- Stage 4C fencing and single-entry corrections: RECONCILING is probe-only, only
  `EXECUTING` may attempt an initial leave INSERT, reconciliation observation plus
  terminal classification is one transaction, and there is exactly one normal submit
  path.
- Stage 5A security/evidence hardening: reconciliation outbox settlement is
  event-type-specific, a non-owner wake does not consume a reconciliation attempt,
  an unresolved reconciliation wake remains durable and retryable, both Attack-7
  serializations have deterministic evidence, and the dead two-step reconciliation
  APIs were removed. Stage 5A review of
  `c1db79cf5ee60a231bb377da16a52afdf0af6ff8` returned PASS.
- Stage 5B-1 `ActionCreationService` creates or reuses a confirmation-ready
  `SUBMIT_ANNUAL_LEAVE` action from trusted identity plus
  `AgentRunResult.prepared_leave_request`. The persisted draft is the V4
  holiday-adjusted canonical result, not the V3 preview.
- Stage 5B-2 Assistant `POST /api/v1/assistant/query` may include an additive
  `action` field after AgentService returns. The LLM tool protocol is unchanged.
- Stage 5C product-truth hardening: `prepared_action` is an explicit preview,
  `action.draft` is the only confirmable/executable payload, reused actions
  return the persisted draft, and `action_status` distinguishes no-PREPARE,
  non-executable PREPARE, created, reused, and T1 creation failure.
- Controlled live-provider validation (observation only) at
  `b3857d22bba7a392bcf5fd87fb98f55a7bd8336f`: 4/4 completed, 0 provider blocks.
  That run is not evaluation evidence.
- Stage 6A `v4-product-eval-1` / `v4-product-dev-1`: 16 DEVELOPMENT cases and an
  isolated-database harness. Harness/design created. No first development
  exposure yet. No V4 holdout exists. See
  [`docs/v4-product-evaluation.md`](v4-product-evaluation.md).
- Stage 6A.1 evaluation reproducibility: frozen evaluator business clock
  `2026-08-28` `Australia/Melbourne`; resume requires matching gold, subject,
  provider-config, baseline-data, and business-clock fingerprints.

Independent Stage 4 review of `feature/v4-workflow-foundation` at
`4f093599843a91ab87c3fcc58d5d1c12e7254dae` returned PASS: 0 BLOCKER, 0 HIGH,
1 MEDIUM. That review is not an external certification. The MEDIUM finding is
the stranded `UNKNOWN_OUTCOME` wake-settlement defect closed in Stage 5A.

Adversarial product-authority review of Stage 5B at
`7a50289a976b73381cd2b8d188e3d317339988ac` returned PASS: 0 BLOCKER, 0 HIGH,
2 MEDIUM, 2 LOW. That review is not an external certification. Stage 5C closes
the product-truth findings from that review.

## Confirmation control plane

- Authentication is trusted `X-Demo-Session` only.
- Owner and session bindings are server-loaded. Clients cannot supply identity, revision,
  draft hash, or workflow state.
- Challenge tokens are 256-bit `secrets.token_hex` values. Only SHA-256 digests are stored
  and compared with `hmac.compare_digest`.
- Demo TTLs: `V4_ACTION_TTL_SECONDS=1800`, `V4_CONFIRMATION_CHALLENGE_TTL_SECONDS=600`,
  `V4_CONFIRMED_TTL_SECONDS=600`, and `V4_EXECUTION_LEASE_TTL_SECONDS=60`. Challenge and
  confirmed TTLs remain capped by `action_expires_at`. These are not an MFA/security
  certification claim.
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
- The only workflow state that may attempt a new leave INSERT is `EXECUTING`.
  `RECONCILING`, `UNKNOWN_OUTCOME`, and all terminal states are submit-ineligible.
- Submit validates current caller ownership, generation, and lease first, then evaluates
  exact `execution_key` dedupe, `business_request_key` dedupe, overlap, balance, calendar,
  and mutation.
- Reconciliation is probe-only. It never authorizes or performs a leave INSERT. Absence
  observation and terminal classification (`SUCCEEDED` / `EXECUTION_FAILED` /
  `UNKNOWN_OUTCOME`) commit in one transaction under the ledger `FOR UPDATE` plus the
  `business_request_key` advisory lock.
- Caller `worker_id` must already own the lease. Reloading a permit never manufactures
  the current owner's authority for a different caller.
- Stale generation, expired lease, wrong owner, and other fence failures are
  `EXECUTION_AUTHORITY_LOST`. They stop this caller; they do not finalize
  `EXECUTION_FAILED`.
- Exact `execution_key` replay resolves `CREATED`. A later action that adopts the same
  `business_request_key` resolves `ADOPTED_EXISTING`. `leave_requests.source_action_id`
  stays on the creating action.
- Revalidation uses the persisted revision calendar authority. If the currently trusted
  calendar/ruleset version no longer matches the revision, reservation fails closed as
  `STALE`.
- `OUTCOME_UNKNOWN` persists before reconciliation scheduling. Recovery uses the original
  key plus business key. `reconciliation_attempt_count` increases only when an authorized
  owner persists UNKNOWN and schedules a real reconciliation attempt. A worker that
  cannot obtain reconciliation authority because another valid lease owner exists does
  not consume one of the three automatic attempts. After three real unresolved
  attempts the revision stays `UNKNOWN_OUTCOME` with `manual_review_required=true` and
  no new reconciliation event is written.
- Same-owner `ALREADY_RESERVED` reuses the existing reservation and records
  `EXECUTION_RESERVATION_REUSED`. A different caller still records
  `EXECUTION_CAS_LOST` and receives no permit.
- `LeaveSubmissionExecutor.reconcile` and `ExecutionFinalizationService.begin_reconciliation`
  were production-dead and preserved the old probe-then-finalize shape. They are
  removed. Production reconciliation is `classify_and_finalize` only.

## Worker

The worker is an internal system actor. It does not manufacture employee authorization from
outbox metadata. It loads `action_id` / revision from PostgreSQL and uses the persisted
`action_workflows.langgraph_thread_id`.

A confirmation wake may proceed into execution through the graph only. After a successful
graph run the worker reloads authoritative PostgreSQL state and settles the outbox; it
does not call submit again. If the checkpoint is already `END` while PostgreSQL is still
`EXECUTING` / `UNKNOWN_OUTCOME` / `RECONCILING`, recovery may fence, take over, and
reconcile, but it must not submit. If reservation is blocked by another unresolved
attempt for the same business key, the event is released for retry.

Outbox settlement is event-type-specific. A `confirmation_committed` wake may be marked
delivered after a confirmation-settled state, including persisted `UNKNOWN_OUTCOME`.
A `reconcile_requested` wake may be marked delivered only when that scheduled attempt
is actually settled: a terminal state, or `UNKNOWN_OUTCOME` with
`manual_review_required=true`. If a foreign worker observes `UNKNOWN_OUTCOME` or
`RECONCILING` because it cannot obtain reconciliation authority while another valid
lease owner exists, the same event is released with bounded wake-delivery backoff. It
is not marked delivered. Wake-delivery `attempt_count` and
`reconciliation_attempt_count` remain separate. The `execution_key` is never replaced.

`reconcile_requested` events are a separate outbox identity and use independent backoff.

Deterministic Attack-7 evidence exists for both legal serializations: failure/absence
first makes a late submit impossible; submit-first commit then classification observes
the submitted row and resolves `SUCCEEDED`.

Checkpoint loss/corruption is an orchestration failure, not authority to guess workflow state.

## Action creation

`ActionCreationService` is a deterministic T1 service. It requires complete trusted V4
identity (`employee_id`, `subject_id`, `session_id`, `jurisdiction`) at the action
boundary only. It consumes the trusted structured V3 PREPARE result and runs
`V4ExecutablePreparationService` before persistence. Non-executable V4 results do not
create an action.

Identifiers are server-owned (`uuid4` `action_id` and `langgraph_thread_id`).
`revision` is always 1. Initial state is `AWAITING_CONFIRMATION`. No challenge, token,
or execution reservation is created at T1. `action_workflows`, `action_revisions`, and
`ACTION_PREPARED` commit in one transaction.

Reuse is owner-scoped and `business_request_key`-based under a SHA-256 advisory lock:

- live `AWAITING_CONFIRMATION` / `CONFIRMED` → reuse
- `EXECUTING` / `UNKNOWN_OUTCOME` / `RECONCILING` → return existing, no replacement
- `SUCCEEDED` → return existing, no new submit action
- `CANCELLED` / `EXPIRED` / `STALE` / `EXECUTION_FAILED` → a newly recomputed action
  may be created

LangGraph is not started inside T1. After persistence, Assistant integration and
challenge issuance call `WorkflowOrchestrationService.ensure_started` to create the
initial interrupt checkpoint if it is missing. If initialization fails, PostgreSQL
remains authority and the same action can be reused; the checkpoint step is retryable.

## Assistant integration

Integration lives after `AgentService.run`. `ToolDispatcher` and the LLM tool allowlist
are unchanged. The model never receives `action_id`, a confirmation token, or an
execution key. Chat utterances such as "yes" / "submit it" do not confirm or execute.

`POST /api/v1/assistant/query` remains backward compatible. Existing V3
`prepared_action` is unchanged in meaning: it is conversational preview-only
(`authority=preview`, `non_executing=true`). It is not confirmable.

When a durable action exists, `action.draft` is the persisted V4 canonical draft
(`authority=authoritative`). Only that draft is confirmable/executable. It matches
`GET /api/v1/actions/{action_id}`. Reused actions (`REUSED_EXISTING`,
`RETURNED_IN_FLIGHT`, `RETURNED_SUCCEEDED`) return that persisted draft only; a
newer conversational reason or hours in `prepared_action` does not mutate it.

`action_status` is the explicit discriminator:

- omitted/`null`: no trusted PREPARE existed (READ-only / no action intent)
- `not_created`: a conversational PREPARE existed but V4 created no executable
  action; `action_not_created_reason` carries a deterministic public category;
  nothing is available to confirm
- `created`: a new durable action was persisted
- `reused`: an existing owner-scoped action was returned
- `creation_failed`: T1 persistence failed; `action` is absent; no fabricated
  `action_id`

Chat text cannot confirm. Even if the model emits another PREPARE for the same
business request, ActionCreationService may reuse the live action, but no
challenge, token, outbox event, or execution occurs.

If graph initialization fails after T1 persistence, the response still returns
the real persisted `action_id` with `created`/`reused`. That is not
`creation_failed`. Retry reuses the same action. PostgreSQL remains authority.

A complete offline PostgreSQL path exists:

Assistant PREPARE → action create/reuse → challenge → confirm → outbox → worker →
LangGraph → reservation → `leave_requests` → `SUCCEEDED`.

## LangGraph role

PostgreSQL remains authority. Resume remains a wake signal only.

The graph keeps an observable confirmed barrier. Worker compilation includes reserve /
execute / reconcile / finalize nodes. The only normal submit path is:

`WorkflowWorker` → LangGraph `execute_business_action` →
`WorkflowExecutionRuntime.execute` → `LeaveSubmissionExecutor.submit`.

`ExecutionFinalizationService.finalize` persists a supplied outcome only. It never
invokes the executor. Employee `start` / `resume` compile without execution nodes, so
they cannot select a side-effect-capable execution node.

Pinned versions remain `langgraph==1.2.11` and `langgraph-checkpoint-postgres==3.1.2`.
Checkpoint schema remains Alembic 0003 in `public`. Runtime `setup()` remains forbidden.

## Not implemented

- no chat-text confirmation
- no LLM execution tool
- no production MFA
- executor is the same-Postgres demo business system
- external HR adapters are not implemented
- leave cancellation after submission is not implemented
- V4 development evaluation harness exists but the 16-case set has not been run
- V4 holdout does not exist
- V4 is not release-ready

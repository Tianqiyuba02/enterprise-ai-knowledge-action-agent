# V4 Architecture Freeze 1.0

Status: **Frozen**
Date: 2026-08-27
Target release: `v0.5.0`
First executable workflow: **submit annual leave request**

This document records the approved V4 Architecture v0.3 decisions as Architecture Freeze 1.0.
It is architecture only. No Stage 1 product code, configuration, or migrations are created by
this freeze.

Core principle:

> LLM proposes; deterministic workflow executes.

PostgreSQL is the business and security authority. LangGraph is orchestration durability only.

V3 `v0.4.0` remains sealed and published at `d396122d368be8c4849872c233460da09a857b17`.

## Architecture review record

Architecture Freeze 1.0 followed this review sequence:

1. Architecture v0.1 → adversarial Grok review: **BLOCK**
2. Architecture v0.2 → independent Fable architecture/security review: **BLOCK**
3. Architecture v0.3 → targeted Fable blocker-closure review: **PASS** (0 blockers, 0 high)

These reviews are independent engineering reviews. They are not formal certification.

## 1. Scope and non-goals

### In scope for V4

- one executable business workflow: submit an annual-leave request;
- out-of-band confirmation of a server-built canonical draft;
- deterministic holiday-aware executable preparation over a trusted VIC calendar;
- durable workflow state, execution reservation, and audit;
- transactional outbox plus a separate workflow worker;
- execution lease, generation fencing, and bounded reconciliation;
- two-layer idempotency and per-employee mutation serialization;
- single revision (`revision=1`) per action.

### Non-goals

- LangGraph as business or security authority;
- LLM/chat confirmation as authorization;
- an LLM execution tool;
- multi-revision / SUPERSEDED workflow states;
- production MFA or richer identity;
- admin/manual-resolution UI;
- expiry sweeper as a Stage 1 requirement;
- external executor adapters;
- changing sealed V3 `LeavePreparationService` arithmetic;
- rerunning the sealed V3 holdout as a second unbiased V3 result.

## 2. Authority model

| Surface | Authority |
|---|---|
| LLM / V3 AgentService | Propose only. Never authorizes execution. |
| V3 conversational leave preview | Not execution authority. |
| Confirmation UI / protocol | Displays and binds the server-built V4 canonical draft. |
| LangGraph checkpoint / resume | Orchestration durability only. Not execution authority. |
| Workflow outbox | Scheduling durability only. Not execution authority. |
| PostgreSQL business tables and execution ledger | Authoritative business and security state. |

No model output, chat utterance, checkpoint, or outbox row may authorize a business mutation.

## 3. V3 AgentService integration boundary

V3 remains READ + PREPARE only.

- Sealed `LeavePreparationService` is unchanged.
- V3 `prepare_leave_request` continues to return a non-executing conversational preview.
- V4 adds a separate deterministic holiday-aware executable-preparation layer.
- V3 AgentService does not gain an execution tool.
- Chat “yes” remains non-authorizing.
- V4 confirmation and execution live outside the LLM/chat execution path.

The V3 preview may inform the employee. It is never hashed, persisted, or executed as the V4
canonical draft.

## 4. Final workflow state machine

V4 supports exactly `revision=1` per action. **SUPERSEDED is not a V4 workflow state.**

### Non-terminal

- `AWAITING_CONFIRMATION`
- `CONFIRMED`
- `EXECUTING`
- `UNKNOWN_OUTCOME`
- `RECONCILING`

### Terminal

- `SUCCEEDED`
- `EXECUTION_FAILED`
- `CANCELLED`
- `EXPIRED`
- `STALE`

### Transitions

`AWAITING_CONFIRMATION`:

- valid confirmation → `CONFIRMED`
- owner cancellation → `CANCELLED`
- action expiry → `EXPIRED`
- authoritative drift → `STALE`

`CONFIRMED`:

- successful execution reservation → `EXECUTING`
- cancellation only if cancellation CAS wins before reservation → `CANCELLED`
- confirmed expiry → `EXPIRED`
- revalidation failure → `STALE`

`EXECUTING`:

- proven applied → `SUCCEEDED`
- proven not applied → `EXECUTION_FAILED`
- ambiguous result → `UNKNOWN_OUTCOME`
- expired/stalled-worker lease recovery → `RECONCILING`

`UNKNOWN_OUTCOME`:

- reconciliation → `RECONCILING`

`RECONCILING`:

- business result authoritatively found → `SUCCEEDED`
- authoritatively absent → `EXECUTION_FAILED`
- still unprovable → `UNKNOWN_OUTCOME`

`UNKNOWN_OUTCOME` and `RECONCILING` are not terminal.

## 5. Confirmation protocol

Confirmation is out-of-band: outside the LLM/chat execution path. Out-of-band does **not** mean
MFA.

Confirmation requires all of:

- authenticated subject;
- authenticated session binding;
- a valid active confirmation token.

Token alone is never sufficient.

Rules:

- the human confirms the server-built V4 canonical draft, never assistant prose;
- plaintext token is never persisted, logged, placed in a URL, or placed in model context;
- one live challenge at a time;
- confirmation replay is idempotent;
- successful confirmation commits a durable outbox row and transitions to `CONFIRMED`;
- confirmation does not execute the business mutation.

## 6. Subject / session binding

`AuthenticatedEmployeeContext` will be additively extended in V4 with trusted server-derived:

- `employee_id`
- `subject_id`
- `session_id`
- `jurisdiction`

Clients must not supply these fields.

Demo honesty: `session_id` is stable for the static demo session token and therefore does not
represent an independent MFA factor. The binding still prevents a token-only confirmation from
succeeding without the authenticated subject and session.

Wrong owner cannot read, confirm, or cancel.

## 7. Trusted structured confirmation draft

V4 persists a holiday-adjusted canonical executable draft. That draft is:

- persisted;
- hashed;
- displayed on the confirmation surface;
- revalidated at execution.

`calendar_version` participates in the authority snapshot/hash.

The V3 conversational preview is not this draft and is not execution authority.

## 8. VIC holiday-calendar overlay

V4 adds a deterministic holiday-aware executable-preparation layer using a trusted, seeded,
version-controlled VIC public-holiday calendar.

- Sealed V3 `LeavePreparationService` remains unchanged.
- The V4 layer applies the trusted calendar overlay to produce the executable draft.
- `calendar_version` is part of the authority snapshot/hash.
- Dates outside trusted calendar coverage are **NOT EXECUTABLE**.

Unresolved calendar prerequisites must not execute.

## 9. Effective-balance semantics

Effective V4 annual-leave balance:

```text
trusted base balance
− hours committed by active submitted V4 annual-leave requests
```

Product rule: an employee cannot have overlapping active submitted annual-leave requests.

Overlap and effective balance are rechecked in the final mutation transaction under per-employee
serialization.

## 10. Transaction model T1–T6

These are the frozen transaction boundaries. Each is a PostgreSQL unit of work.

| ID | Purpose | Authoritative effects |
|---|---|---|
| T1 | Create confirmation-ready action | Insert `action_workflows` (`revision=1`, `AWAITING_CONFIRMATION`), `action_revisions` with hashed canonical draft, and one live `confirmation_challenges` row. No business mutation. |
| T2 | Confirm | Authenticated subject + session + valid token. CAS `AWAITING_CONFIRMATION` → `CONFIRMED`. Consume the live challenge. Insert `workflow_outbox`. Idempotent replay returns the existing confirmed action. |
| T3 | Pre-execution close | Owner cancel, expiry, or authoritative drift. CAS to `CANCELLED`, `EXPIRED`, or `STALE` only while reservation has not won. Zero business mutation. |
| T4 | Execution reservation | Worker CAS reserve: `CONFIRMED` → `EXECUTING`. Create/claim `action_execution_ledger` with immutable `execution_key`, `lease_owner_id`, and `lease_generation`. One reservation per revision. |
| T5 | Business apply | Final mutation transaction. Lock/CAS fencing row, take per-employee advisory lock, recheck fencing, `business_request_key`, overlap, effective balance, and trusted prerequisites, then `INSERT leave_requests`. Stale fencing performs zero mutation. |
| T6 | Outcome / reconcile | Record proven applied (`SUCCEEDED`), proven not applied (`EXECUTION_FAILED`), or unprovable (`UNKNOWN_OUTCOME`). Recovery may enter `RECONCILING`. After three automatic attempts: remain `UNKNOWN_OUTCOME` with `manual_review_required=true`. No new `execution_key`. |

Outbox insert (T2) is not execution. Graph resume is not T4 or T5.

## 11. Transactional outbox

Successful confirmation writes a durable `workflow_outbox` row.

- The outbox schedules work for the workflow worker.
- The outbox is not execution authority.
- Automatic reconciliation also uses durable outbox scheduling.
- Losing a CAS or fencing check must not enqueue a new executable attempt for the same
  unresolved request.

## 12. Separate workflow worker

A process separate from the request/API path claims outbox work, holds execution leases, and
drives T4–T6.

The API path may create, confirm, cancel, and read. It must not apply the leave-request mutation
inside the confirmation request.

## 13. Execution lease and generation fencing

Each reserved execution has:

- immutable `execution_key`
- `lease_owner_id`
- `lease_generation`
- lease validity window
- execution/workflow state

A healthy worker that still holds the current valid lease may continue or reconcile on that
generation. A different worker recovering a dead/stalled execution must observe/wait for lease
expiry, take over the lease, and increment `lease_generation` before reconciling.

## 14. Fencing verification locking requirement

**MUST:** the business executor's fencing verification is mutually exclusive with lease takeover.

A plain `READ COMMITTED` snapshot `SELECT` is insufficient.

The final business transaction must use either:

- `SELECT ... FOR UPDATE` on the authoritative execution-ledger row, or
- an equivalent conditional locking/CAS mechanism.

It must verify all of:

- `execution_key`
- `lease_owner_id`
- `lease_generation`
- execution/workflow state
- lease validity

before any business mutation.

A stale lease generation performs **zero** business mutation.

## 15. Two-layer idempotency

1. **Revision/execution reservation:** one execution reservation per `revision=1`. Immutable
   `execution_key`. No second executable attempt for the same unresolved action.
2. **Cross-action business dedupe:** `business_request_key` is rechecked under employee
   serialization so the same business request cannot be inserted twice.

CAS losers make zero business calls. Unresolved `UNKNOWN_OUTCOME` never receives a new
`execution_key`.

## 16. Per-employee mutation serialization

The final leave-request mutation transaction takes a PostgreSQL transaction-scoped per-employee
advisory lock and rechecks, before `INSERT`:

- fencing;
- `business_request_key`;
- overlap;
- effective balance;
- trusted prerequisites.

## 17. Business overlap policy

An employee cannot have overlapping active submitted annual-leave requests.

Overlap is a business invariant rechecked under the employee advisory lock, not a model judgment.

## 18. UNKNOWN_OUTCOME

An ambiguous executor result is `UNKNOWN_OUTCOME`, not a definite failure.

- It is non-terminal.
- It is not treated as `EXECUTION_FAILED`.
- It never receives a new execution key.
- No duplicate executable attempt is created for the unresolved business request.

## 19. Reconciliation semantics

Reconciliation asks PostgreSQL whether the business result is authoritatively present or absent.

- Authoritatively found → `SUCCEEDED`
- Authoritatively absent → `EXECUTION_FAILED`
- Still unprovable → `UNKNOWN_OUTCOME`

Reconciliation does not invent a new `execution_key` or bypass fencing.

## 20. Healthy-worker vs takeover reconciliation

A healthy worker that receives `OUTCOME_UNKNOWN` while still holding the current valid execution
lease **may** reconcile using its existing generation.

A different worker recovering a dead/stalled execution **must** first:

1. wait for/observe lease expiry;
2. successfully take over the lease;
3. increment `lease_generation`;

before reconciliation.

## 21. Maximum three automatic reconciliation attempts

Automatic reconciliation is scheduled through durable `workflow_outbox` rows.

Maximum automatic attempts: **3**.

After that:

- remain `UNKNOWN_OUTCOME`;
- set `manual_review_required=true`;
- no new execution key;
- no duplicate executable attempt.

## 22. manual_review_required

After the automatic bound is exhausted, the action stays `UNKNOWN_OUTCOME` with
`manual_review_required=true`.

Admin/manual resolution UI is deferred hardening, not a Stage 1 blocker. The workflow must not
self-heal by minting a new key.

## 23. Cancellation matrix

| State | Owner cancel |
|---|---|
| `AWAITING_CONFIRMATION` | Allowed → `CANCELLED` |
| `CONFIRMED` | Allowed only if cancellation CAS wins before execution reservation → `CANCELLED` |
| `EXECUTING` | Not locally cancellable |
| `UNKNOWN_OUTCOME` / `RECONCILING` | Not a local cancel-to-absent path |
| Terminal `SUCCEEDED` / `EXECUTION_FAILED` / `EXPIRED` / `STALE` / `CANCELLED` | No further cancel |

Wrong owner cannot cancel.

## 24. Expiry semantics

- `AWAITING_CONFIRMATION` expiry → `EXPIRED`
- `CONFIRMED` expiry → `EXPIRED`
- Expired/stalled-worker lease recovery from `EXECUTING` → `RECONCILING`

Stale/expired revisions do not execute.

An expiry sweeper is deferred maintenance/visibility hardening, not a Stage 1 correctness
requirement. Expiry transitions remain defined and CAS-protected.

## 25. Single-revision V4 scope

V4 supports exactly `revision=1` per action.

`SUPERSEDED` is not a workflow state. There is no revision chain, no replacement draft after
confirmation, and no second execution reservation for the same action.

## 26. Durable table model

Stage 1 application tables:

- `public_holidays`
- `action_workflows`
- `action_revisions`
- `confirmation_challenges`
- `workflow_outbox`
- `action_execution_ledger`
- `action_audit_events`
- `leave_requests`

Plus separately governed LangGraph checkpoint tables.

Not in V4:

- `action_thread_map`
- `reconciliation_probes`

## 27. Same PostgreSQL physical database

V2 knowledge records and V4 workflow/business demo records use **one PostgreSQL physical
database**.

Logical isolation is by schema/table ownership and application boundaries, not by a second
database process.

## 28. APP_DATABASE_URL compatibility strategy

V4 will introduce `APP_DATABASE_URL` with backward-compatible fallback to
`KNOWLEDGE_DATABASE_URL`.

This freeze does not implement that setting.

## 29. LangGraph role and checkpoint migration governance

LangGraph is **not required** for business or security correctness.

It is retained for:

- durable interrupt/resume;
- explicit workflow topology;
- checkpoint recovery;
- future multi-step HITL evolution;
- strategic/portfolio learning value.

Removing LangGraph must not weaken any deterministic invariant.

Rules:

- no runtime checkpoint schema setup;
- exact LangGraph/checkpointer versions are pinned **before** checkpoint migration code is
  written;
- checkpoint tables are not authoritative;
- graph resume is not execution authority.

Planned migrations, not created by this freeze:

- `0002_v4_action_workflows`
- `0003_v4_langgraph_checkpoints`

Alembic head remains `0001_v2_knowledge` until Stage 1 implementation.

## 30. Audit requirements

`action_audit_events` records confirmation, reservation, fencing outcomes, business apply,
reconciliation, cancel, expiry, and stale transitions.

Audit events are application-owned facts. Database-level prevention of `UPDATE`/`DELETE` on
audit rows is deferred hardening, not a Stage 1 blocker.

Confirmation tokens, plaintext secrets, and model transcripts must not appear in audit payloads.

## 31. Evaluation governance

- The V3 frozen holdout remains sealed historical evidence.
- It must not be rerun after V4 changes and presented as a second unbiased V3 holdout result.
- Historical V3 cases may be used explicitly as regression data.
- V4 will create a V4 development evaluation and a newly frozen V4 holdout.
- All deterministic V0–V3 regression tests must remain green.
- Development and holdout remain separate campaigns. They are not one combined frozen
  benchmark.

## 32. Known deferred hardening

Not Stage 1 blockers:

- database-level audit `UPDATE`/`DELETE` prevention;
- richer production identity / MFA;
- admin/manual resolution UI for permanently unknown outcomes;
- expiry sweeper for maintenance/visibility;
- external executor adapters;
- existing V3 mixed-form relative-weekday fail-closed limitation.

## Security invariant matrix

The following are freeze-1.0 invariants:

- chat never authorizes execution;
- model output never authorizes execution;
- no LLM execution tool;
- server-built confirmation draft;
- token alone insufficient;
- one live challenge;
- confirmation replay idempotent;
- confirmation commits durable outbox;
- outbox is not execution authority;
- graph resume is not execution authority;
- only DB revalidation + successful CAS allows execution;
- CAS loser makes zero business calls;
- stale fencing permit makes zero mutation;
- one execution reservation per revision;
- immutable `execution_key`;
- cross-action business dedupe;
- overlap/balance rechecked under employee serialization;
- stale/expired revisions do not execute;
- unresolved calendar prerequisite does not execute;
- wrong owner cannot read/confirm/cancel;
- executing action cannot be locally cancelled;
- ambiguous result is not definite failure;
- unknown outcome never receives a new key;
- Postgres is authoritative;
- checkpoint is not authoritative;
- confirmation token never enters LLM context;
- reason is rendered strictly as inert data;
- prompt injection cannot cross into execution authority.

## Implementation boundary of this freeze

This document does not:

- add product code;
- change `AuthenticatedEmployeeContext`;
- introduce `APP_DATABASE_URL`;
- create migrations `0002` or `0003`;
- pin LangGraph versions;
- add evaluation datasets.

Stage 1 implementation may begin only after this freeze is committed and explicitly authorized.

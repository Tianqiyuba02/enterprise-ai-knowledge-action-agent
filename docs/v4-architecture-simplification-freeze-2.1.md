# V4 Architecture Simplification Freeze 2.1

Status: **Frozen — implementation target; not implemented by this document**
Date: 2026-08-31
Decision: **SIMPLIFY BEFORE DEVELOPMENT CLOSURE** (direction unchanged)
Review of Freeze 2.0: **C. FAIL — material correctness gaps before implementation**
Historical maximal architecture: [`docs/v4-architecture-freeze-1.0.md`](v4-architecture-freeze-1.0.md)
Reviewed-but-not-implementation-safe prior target: [`docs/v4-architecture-simplification-freeze-2.0.md`](v4-architecture-simplification-freeze-2.0.md)
Freeze 2.0 commit / reviewed HEAD: `e4bf1d4f09ad1045585609fe0a4cfeb30979fe1b`
Historical implementation subject HEAD: `f14720485a54c85864e833573711918083af9081`

This document does **not** overwrite Freeze 1.0 or Freeze 2.0.

- Freeze 1.0 remains the historical **maximal** V4 architecture.
- Freeze 2.0 remains the reviewed, **not implementation-safe**, first simplification freeze.
- Freeze 2.1 **supersedes Freeze 2.0** as the implementation target.
- The simplification **direction** remains approved. Distributed-style mechanisms listed in Freeze 2.0 stay removed or deferred. They are not restored.
- This freeze is architecture and governance only. It does not edit workflow production code, migrations, dependencies, tests, gold, or evaluation artifacts.

Core principle (unchanged):

> LLM proposes; deterministic workflow executes.

Execution-boundary principle (unchanged):

> Infrastructure is earned by the transaction boundary.

The current V4 side effect is a same-PostgreSQL `leave_requests` INSERT. Therefore execution authority, deterministic validation, business mutation, workflow finalization, and audit share **one PostgreSQL transaction**.

No implementation is authorized by this document.

---

## Review result and named blockers

Targeted correctness review of Freeze 2.0 returned **C. FAIL**. The gaps are migration, locking, TTL, and conflict-handling details. They are not a reversal of the simplified core.

### Blocker-1 — Transitional occupancy and migration safety

Freeze 2.0 specified the **final** three-state occupancy predicate (`AWAITING_CONFIRMATION`, `CONFIRMED`, `SUCCEEDED`) as if it could be applied while the old executor still uses `EXECUTING`, `UNKNOWN_OUTCOME`, and `RECONCILING`.

That is unsafe: a second PREPARE could insert a new occupant while a legacy unresolved action still holds the same `business_request_key`.

**Correction:** Phase 1A uses a **six-state transitional** occupancy index. The final three-state index is created only after old-worker quiescence, authoritative old-state normalization, and clean invariant queries. Pre-migration duplicate/invariant gates **HALT** on ambiguity. Contradictory terminal pairs are not silently auto-repaired.

### Blocker-2 — Canonical mutex, TTL authority, and conflict handling

Freeze 2.0 omitted implementation-safe rules for:

- `action_revisions` as the canonical mutable mutex and global lock order;
- PREPARE loser `FOR UPDATE` plus fail-closed identity verification;
- poller selection that must **not** permanently filter expired `CONFIRMED` rows out of the only poll query;
- TTL re-checks after the employee-lock wait, using PostgreSQL `clock_timestamp()`, with the **final** check immediately before the first business mutation;
- leave-result pre-probe / adoption equivalence;
- `IntegrityError` handling that must not continue on an aborted outer transaction;
- stable-authority `STALE` versus dynamic-business `EXECUTION_FAILED`;
- transient liveness without durable retry machinery.

**Correction:** those rules are frozen below. They do not restore LangGraph, outbox, ledger, leases, fencing, `EXECUTING`, `UNKNOWN_OUTCOME`, `RECONCILING`, reconciliation/manual-review machinery, or business-request advisory locking.

These reviews are independent engineering reviews. They are not formal certification.

---

## 1. Historical subject and evaluation status

Preserve the pre-simplification implementation and all historical evaluation evidence.

| Campaign | Status | Binding |
|---|---|---|
| Development Run 1 | **CLOSED — PARTIAL / PROVIDER-LIMITED** | Pre-simplification V4 subject. Immutable. |
| Development Run 2 | **CLOSED — STARTED / STOPPED EARLY / PROVIDER-LIMITED** | Pre-simplification V4 subject. Immutable. |
| V4 holdout | **DOES NOT EXIST** | Do not create one in this freeze. |

Do not modify Run 1 or Run 2. Do not combine their denominators. They belong to the pre-simplification V4 subject at HEAD `f14720485a54c85864e833573711918083af9081`.

This Freeze 2.1 commit is documentation only. Evaluation subject / transport fingerprints must remain unchanged from a code perspective.

Implementation later creates a **new** V4 evaluation subject. After implementation, development evaluation starts from zero.

Provider incident status is unchanged. Local live Gemini diagnosis remains closed. This freeze does not call Gemini, resume Run 2, or start Run 3.

---

## 2. Preserved simplified core

Freeze 2.1 keeps:

- one same-Postgres atomic execution transaction
- PostgreSQL as business/security authority
- PREPARE-only LLM boundary
- out-of-band confirmation
- preview vs authoritative draft
- action-level DB idempotency
- business-level DB idempotency
- exactly one employee transaction advisory lock
- simple `CONFIRMED` poller
- action audit events
- seven-state **final** model

### KEEP NOW

- PostgreSQL as business/security authority
- trusted server identity
- PREPARE-only LLM boundary
- preview vs authoritative draft
- out-of-band confirmation
- challenge token hashing
- single-live challenge
- confirmation TTL
- `action_id` + `draft_hash` binding
- action reuse
- partial unique action occupancy (transitional six-state, then final three-state)
- `business_request_key`
- `UNIQUE(source_action_id)`
- leave business uniqueness
- employee transaction advisory lock
- deterministic calendar
- effective balance
- overlap detection
- `FOR UPDATE SKIP LOCKED` poller
- action audit events
- authority-oriented repository boundaries
- `action_workflows` / `action_revisions` physical two-table shape for now

### REMOVE NOW (final target; not restored)

- LangGraph orchestration
- LangGraph checkpointing
- checkpoint tables
- checkpoint serialization hardening
- transactional outbox
- `OutboxRepository`
- execution ledger table
- `ExecutionLedgerRepository`
- committed execution lease
- lease takeover
- fencing generation / `ExecutionPermit`
- persisted `EXECUTING`
- `UNKNOWN_OUTCOME`
- `RECONCILING`
- bounded reconciliation
- manual-review reconciliation exhaustion
- business-request advisory lock
- split reserve / apply / finalize transaction boundary
- checkpoint wake worker
- public `revision=1` authority concept

### DEFER

Deferred until a **real external side-effect boundary** earns them:

- transactional outbox / job
- persisted external execution attempt
- provider idempotency / correlation key
- UNKNOWN external outcome
- provider result probe
- reconciliation
- bounded retry
- manual review
- lease / heartbeat
- stale-worker fencing

LangGraph does **not** automatically return for an external API.

Reconsider LangGraph only for genuinely branching, multi-step, durable, resumable orchestration.

Do not restore mechanisms merely because they existed in Freeze 1.0.

---

## 3. Target flow

### PREPARE

```text
Assistant
→ LLM READ/PREPARE only
→ trusted server identity
→ deterministic executable preparation
→ insert-race create / verified reuse of immutable persisted action
→ AWAITING_CONFIRMATION
```

No business mutation. No outbox. No checkpoint start as an authority step.

### CONFIRM

```text
authenticated out-of-band endpoint
→ lock action_revisions FOR UPDATE
→ lock confirmation_challenge FOR UPDATE
→ verify owner / session / challenge / token / TTL / action_id / draft_hash
→ consume challenge
→ action CONFIRMED
→ audit
→ ONE COMMIT
```

Confirmation does not execute the leave INSERT. After Phase 2B, confirmation does not enqueue outbox or checkpoint work.

### EXECUTE

```text
simple poller
→ SELECT one CONFIRMED action
   ORDER BY confirmed_at, action_id
   FOR UPDATE SKIP LOCKED
→ TTL check #1 (PostgreSQL clock_timestamp())
→ acquire employee pg_advisory_xact_lock
→ TTL check #2
→ verify draft integrity and stable authority
→ CURRENT balance / overlap queries
→ leave-result pre-probe (source_action_id and business_request_key)
→ FINAL TTL check immediately before first business mutation
→ INSERT or adopt leave_request
→ action SUCCEEDED | EXECUTION_FAILED | STALE | EXPIRED
→ audit
→ ONE COMMIT
```

No persisted `EXECUTING`.

---

## 4. Frozen states

**Final** target states remain exactly seven:

| State | Kind |
|---|---|
| `AWAITING_CONFIRMATION` | non-terminal |
| `CONFIRMED` | non-terminal (durable work) |
| `SUCCEEDED` | terminal |
| `EXECUTION_FAILED` | terminal |
| `STALE` | terminal |
| `CANCELLED` | terminal |
| `EXPIRED` | terminal |

Removed from the **final** architecture:

- `EXECUTING`
- `UNKNOWN_OUTCOME`
- `RECONCILING`

Those three remain **physical legacy states** until Phase 2A normalization and Phase 2B CHECK tightening. They are occupying during the transitional index. They are not target product states.

### Transitions (final)

`AWAITING_CONFIRMATION`:

- valid confirmation → `CONFIRMED`
- owner cancellation → `CANCELLED`
- action expiry → `EXPIRED`
- material stable-authority change discovered before confirmation → `STALE`

`CONFIRMED`:

- atomic execution success → `SUCCEEDED`
- definite deterministic/business failure → `EXECUTION_FAILED`
- material stable-authority change → `STALE`
- confirmation/execution freshness exceeded at an authoritative TTL check → `EXPIRED`
- owner cancel if cancel acquires the cancellable row and commits → `CANCELLED`

Transient infrastructure failure does **not** transition the action. The transaction rolls back. The action remains `CONFIRMED`.

`SUCCEEDED` / `EXECUTION_FAILED` / `STALE` / `CANCELLED` / `EXPIRED` are terminal.

---

## 5. Canonical state mutex and lock order

`action_revisions` is the canonical mutable action-state row / mutex.

Current immutable `action_workflows` metadata is **not** normally `FOR UPDATE` locked.

### Global target lock order

1. `action_revisions` `FOR UPDATE`
2. `confirmation_challenges` `FOR UPDATE`, when applicable
3. employee `pg_advisory_xact_lock`, **execution only**
4. deterministic validation (after locks; see §8 and §11)
5. leave mutation / action update / audit
6. `COMMIT`

No path may acquire the employee advisory lock before the canonical action revision row.

Reads and audit-only paths do not acquire the employee lock.

Confirm, cancel, expire, PREPARE-loser re-read, and poller claim all lock `action_revisions` first.

---

## 6. Occupancy indexes

Action-level idempotency remains separate from business-mutation idempotency.

`business_request_key` already binds the current annual-leave business identity:

```text
employee_id + leave_type + start_date + end_date
```

(canonicalization version `v4-canonical-1`).

### Transitional occupancy (Phase 1A)

Freeze 2.0’s final occupancy predicate is **not** safe while the old executor still uses legacy unresolved states.

Phase 1A must create this conceptual partial unique index:

```sql
UNIQUE (action_revisions.business_request_key)
WHERE state IN (
  'AWAITING_CONFIRMATION',
  'CONFIRMED',
  'EXECUTING',
  'UNKNOWN_OUTCOME',
  'RECONCILING',
  'SUCCEEDED'
)
```

This transitional index exists **only** while old execution machinery may still run.

Do **not** create the final three-state index in Phase 1A.

### Final occupancy (Phase 2B, after cutover)

After worker quiescence and old-state normalization:

```sql
UNIQUE (action_revisions.business_request_key)
WHERE state IN (
  'AWAITING_CONFIRMATION',
  'CONFIRMED',
  'SUCCEEDED'
)
```

---

## 7. Pre-migration duplicate / invariant gate

Before creating **either** occupancy index, run deterministic DB checks.

At minimum detect:

- multiple projected occupants for the same `business_request_key`
- multiple `SUCCEEDED` actions for the same key
- duplicate `source_action_id` leave rows
- stored business key inconsistent with the canonical trusted draft / owner
- `SUCCEEDED` with no valid business result
- terminal action with a contradictory committed source result
- active challenge attached to an invalid / non-awaiting action
- malformed or null confirmation timestamps where target logic requires them

Any ambiguous cross-owner or canonical mismatch: **HALT migration**.

Do not auto-repair silently.

`UNIQUE(source_action_id)` is added only after these checks pass for leave-row duplicates and inconsistency.

No current legitimate V4 use case allows one submit action to create multiple leave rows.

The physical `source_action_revision` FK/column may remain for schema compatibility even though `revision=1` is no longer product authority.

---

## 8. PREPARE insert-race algorithm

SELECT-before-INSERT may remain an optimization. It is **not** correctness.

Catch **only** the named occupancy unique violation. Any other integrity error rolls back and fails closed.

Exact algorithm:

1. Compute `business_request_key` from trusted server identity plus canonical leave fields.
2. **T1:** attempt `INSERT` of an `AWAITING_CONFIRMATION` action/revision plus audit.
3. If T1 commit succeeds: return `CREATED`.
4. Catch **only** the named occupancy unique violation.
5. Roll back T1 completely. Do not continue in the aborted session.
6. Start a fresh **T2**.
7. `SELECT` the occupying `action_revisions` row `FOR UPDATE`.
8. Load immutable workflow identity (`action_workflows` is not the mutex).
9. Verify trusted employee, owner subject, action type, canonical business key, and draft integrity.
10. If mismatch: fail closed. Do not disclose or reuse a foreign or corrupt action.
11. Use the PostgreSQL clock **after** the row lock (`clock_timestamp()`).
12. If the occupant is expired: transition to `EXPIRED`, supersede/expire any relevant active challenge, append audit, `COMMIT`, then retry `INSERT` within the same bounded PREPARE request.
13. If no occupying row remains: commit/close T2 and retry `INSERT`.
14. If `AWAITING_CONFIRMATION` or `CONFIRMED`: return `REUSED_EXISTING`.
15. If `SUCCEEDED`: return `RETURNED_SUCCEEDED`.
16. Legacy unresolved states during transition (`EXECUTING`, `UNKNOWN_OUTCOME`, `RECONCILING`): treat as occupying and return the existing authoritative action using the existing safe transitional disposition (`RETURNED_IN_FLIGHT`). Do not create a replacement.
17. Bound PREPARE contention retries.

**Exhaustion:** return a deterministic retryable conflict. Not an unbounded loop.

Remove the creation-time business-request advisory lock. Occupancy is the unique index plus this loser algorithm.

---

## 9. Employee serialization

Keep exactly one transaction-scoped employee advisory lock:

```sql
pg_advisory_xact_lock(employee-derived key)
```

### Execution order

```text
action_revisions row lock
→ employee advisory xact lock
→ CURRENT balance / overlap queries
→ mutation
→ finalization
→ audit
→ COMMIT
```

All **new** leave mutation paths MUST use this lock.

The proof relies on validation queries happening **after** acquisition.

Under `READ COMMITTED`, a second same-employee transaction waiting on the lock must issue **fresh** balance/overlap statements after acquiring it and therefore observe the first commit.

No business-request advisory lock remains in the final target.

Reads/audit alone do not acquire this lock. PREPARE and confirm do not acquire it.

### Why business-request uniqueness cannot replace this lock

`UNIQUE(business_request_key)` serializes identical leave identity only.

**76-hour / two 60-hour example**

- Effective remaining annual-leave balance: **76 hours**.
- Request A: **60 hours**, dates D1–D2 → key K_A.
- Request B: **60 hours**, dates D3–D4 → key K_B ≠ K_A.

Without the employee lock, both execution transactions can read remaining = 76, both pass `76 >= 60`, and both `INSERT` → 120 hours committed against a 76-hour remainder.

With the lock held across **post-acquisition** revalidation and `INSERT`, the second transaction observes `76 − 60 = 16` and fails `INSUFFICIENT_BALANCE`.

**Overlap analog:** distinct date pairs have distinct keys; only the employee lock plus overlap revalidation prevents overlapping rows.

---

## 10. Poller selection and expiry normalization

Do **not** permanently filter expired `CONFIRMED` rows out of the only poller query. An expired row that is never claimed cannot be normalized to `EXPIRED`.

Target polling concept:

```text
state = CONFIRMED
ORDER BY confirmed_at, action_id
FOR UPDATE SKIP LOCKED
```

One action per transaction.

After the `action_revisions` lock, use PostgreSQL `clock_timestamp()`.

- If already expired: `EXPIRED` + audit + `COMMIT`. No execution.
- If not expired: continue.

A locked older action does not block younger work (`SKIP LOCKED`). Process-local cooldown (§16) prevents immediate poison-action reacquisition. Strong fairness beyond `ORDER BY confirmed_at, action_id` is not required for current V4.

Do not add `attempt_count`, `available_at`, retry outbox, claim columns, or delivery columns to the action tables. `CONFIRMED` is business state, not a job record.

The API path may create, confirm, cancel, and read. It must not apply the leave-request mutation inside the confirmation request.

---

## 11. TTL authority points

The worker may wait on the employee lock. Wall-clock time can pass between claim and mutation.

Use PostgreSQL `clock_timestamp()` at every TTL check.

| Check | When | On expiry |
|---|---|---|
| TTL check #1 | after canonical `action_revisions` row lock | `EXPIRED`, no leave mutation |
| TTL check #2 | after employee advisory lock | `EXPIRED`, no leave mutation |
| **FINAL authoritative TTL check** | immediately before the first business mutation | `EXPIRED`, no leave mutation |

Definition: confirmation authority must be valid at the **final locked pre-mutation authorization point**.

Do **not** require physical `COMMIT` to occur before the TTL nanosecond boundary.

Null or malformed `confirmed_expires_at`: fail closed, no execution. Record a deterministic integrity failure, or treat as a migration block if discovered in preflight.

`EXPIRED` is terminal once the confirmation/execution freshness window is exceeded at an authoritative check.

---

## 12. Stable authority vs dynamic business facts

### Stable authority / draft facts

Material change → `STALE`. The user should PREPARE again.

Include concepts such as:

- trusted identity / owner
- jurisdiction
- calendar / ruleset identity / version
- work-schedule authority needed to interpret the draft
- canonical dates / type
- persisted draft integrity
- authority inputs whose change alters the meaning of what was confirmed

### Dynamic business facts

Expected to change between PREPARE and EXECUTE. Ordinary dynamic drift is **not** `STALE`.

- current effective balance
- current submitted commitments
- current overlap

At execution:

| Observation | Outcome |
|---|---|
| Balance changed but remains sufficient | continue |
| Current balance insufficient | `EXECUTION_FAILED` / `INSUFFICIENT_BALANCE` |
| Current overlap exists | `EXECUTION_FAILED` / `OVERLAP` |

The balance / projected-balance shown at PREPARE and confirmation is **informational snapshot data**, not frozen execution authority.

Implementation must review and, if required, refactor authority-fingerprint semantics so dynamic balance/overlap facts are not treated as stable-authority hash inputs that would spuriously `STALE` a still-valid confirmed draft.

### Other definite execution failures

`EXECUTION_FAILED` remains only for definite deterministic/business failure, including:

- `INSUFFICIENT_BALANCE`
- `OVERLAP`
- `CALENDAR_UNCOVERED` (uncovered dates are not executable; if this is a stable calendar-coverage identity change it may instead be `STALE` — implementation must classify by whether the confirmed draft’s meaning changed or a dynamic/calendar-coverage check failed closed)
- `DRAFT_INTEGRITY_FAILURE`

Uncovered calendar and draft-integrity failures produce **no** leave mutation.

---

## 13. Leave-result pre-probe and adoption

Under the employee advisory lock and **before** `INSERT`, probe both:

- `source_action_id`
- `business_request_key`

If an existing leave result is found, adoption is allowed only after **exact trusted equivalence** validation.

At minimum verify:

- employee identity
- owner / source-action relationship
- leave type
- start / end dates
- recomputed `business_request_key`
- requested hours / mutation-relevant values
- persisted `source_action_id` relationship
- no contradictory result under the alternate unique identity

If any mismatch, cross-owner condition, or corruption:

- do **not** mark `SUCCEEDED`
- fail closed as an integrity violation

Reason handling must match the product’s actual persisted business-mutation semantics. Do not claim a changed conversational reason was newly submitted if the existing business result did not contain it.

Valid equivalent adoption: `SUCCEEDED` + exactly one leave row.

---

## 14. `IntegrityError` transaction rule

Do **not** catch PostgreSQL `IntegrityError` and continue using an aborted outer transaction.

Current Freeze 1.0 executor code catches `IntegrityError`, rolls back, then calls adopt in a **new** path. The target rule is stricter and explicit:

**Minimum correctness rule:**

1. Pre-probe expected unique identities under the employee lock.
2. Attempt `INSERT`.
3. If an unexpected named `source_action_id` / `business_request_key` uniqueness conflict still occurs:
   - **roll back the entire execution transaction**;
   - open a **fresh** transaction;
   - reacquire the canonical action row;
   - reacquire the employee lock if execution classification requires it;
   - authoritative re-probe;
   - adopt an equivalent existing result **or** fail closed.
4. Any other `IntegrityError`: roll back the entire execution transaction.

A nested `SAVEPOINT` may be used later as an implementation optimization. It is **not** required by this freeze.

No post-`INSERT` error path may commit:

```text
leave row + EXECUTION_FAILED
```

---

## 15. Atomic execution invariant

For a newly executed action, the normal committed authoritative pairs are:

| Action state | Leave row |
|---|---|
| `CONFIRMED` | no leave row (before / after a rolled-back attempt) |
| `SUCCEEDED` | exactly one valid leave row (created or safely adopted) |
| `EXECUTION_FAILED` / `STALE` / `EXPIRED` | no **newly created** leave row |

A newly created leave row **MUST NOT** commit with:

- `EXECUTION_FAILED`
- `STALE`
- `EXPIRED`
- `CANCELLED`

Business mutation + final state + audit must commit in the **same** transaction.

Post-`INSERT` exceptions must roll back the entire transaction unless the existing result was safely adopted under §13.

Audit remains non-authoritative. No decision may depend on audit history. Tokens, plaintext secrets, and model transcripts must not appear in audit payloads.

---

## 16. Lost commit-ack and transient liveness

### Lost commit-ack

Preserve **no** `UNKNOWN` state.

If `COMMIT` acknowledgement is lost:

- the caller/worker must discard the uncertain transaction/connection;
- rely on a fresh authoritative PostgreSQL observation.

If the original transaction is still alive, its `action_revisions` row lock prevents another worker from claiming it. `SKIP LOCKED` skips it.

After resolution:

| Original txn | Authoritative pair |
|---|---|
| `ROLLBACK` | `CONFIRMED` + no new leave → poller may retry |
| `COMMIT` | `SUCCEEDED` + leave + audit → no longer pollable |

No blind second mutation.

### Transient failure liveness

No durable retry scheduler.

Do **not** add:

- `attempt_count`
- `available_at`
- retry outbox
- manual-review state

Minimum liveness policy:

| Class | Policy |
|---|---|
| DB-wide / transient infrastructure outage | process-level exponential backoff + jitter |
| Action-specific transient failure | process-local in-memory cooldown for that `action_id` |

The poller should temporarily skip in-memory cooling IDs so younger work can progress.

Operational DB limits (or equivalent bounded transaction/connection safeguards):

- `lock_timeout`
- `statement_timeout`
- `idle_in_transaction_session_timeout`

Process restart may lose cooldown. That is acceptable.

TTL remains the final authority / retry bound.

Repeated transient failure: metrics and logging, not durable workflow machinery.

---

## 17. Confirm / cancel / expire ordering

Use canonical `action_revisions` row locking.

The first valid committed transition wins.

Cancel may win whenever it acquires the row and the authoritative state is still cancellable, **including after a previous worker transaction rolled back**.

A worker that merely held the row earlier but did not commit does **not** consume cancellation authority.

No checkpoint or outbox is involved.

Wrong owner cannot read, confirm, or cancel.

Confirmation binding remains:

```text
action_id
+ draft_hash
+ server-authenticated owner / session
+ single-live challenge
+ hashed token
+ TTL
```

`revision=1` is not product/API authority. Keep the physical `revision` column for now. Do not collapse `action_workflows` + `action_revisions`. Future editable drafts require a new architecture decision.

Token alone is never sufficient. Chat “yes” remains non-authorizing.

---

## 18. Legacy physical schema compatibility

Current obsolete physical requirements include:

- `leave_requests.execution_key` — `NOT NULL`, unique, nonempty check
- `action_workflows.langgraph_thread_id` — `NOT NULL`, unique, nonempty check

Do **not** make invented or fake semantic values part of the new target model.

**Preferred transitional strategy:** before activating the new Phase 2B path, apply a compatibility migration that relaxes obsolete `NOT NULL` / write requirements where safely possible. The columns may remain temporarily as nullable, non-authoritative legacy fields. Phase 3 later drops approved obsolete columns.

If repository constraints make nullable relaxation impossible, the implementation authorization must document the minimum deterministic compatibility strategy and **explicitly label those values non-authoritative**.

Do not let old physical columns force old architecture semantics back into the new design.

`source_action_revision` may remain as a physical FK/column for compatibility. It is not product confirmation authority.

---

## 19. Old-worker quiescence gate

**Hard cutover gate** before new atomic execution is activated (Phase 2A):

1. Stop old outbox / LangGraph workers.
2. Prevent automatic restart.
3. Prevent new old-style execution scheduling.
4. Wait for active old execution DB transactions to finish, or terminate them safely.
5. Verify no old worker still has execution authority.
6. Inspect authoritative DB state.
7. Only then normalize old unresolved states (§20).

---

## 20. Old-state normalization

Use authoritative DB / business evidence. Do not invent outcomes.

### No leave result

| Prior state | Normalization |
|---|---|
| Fresh `AWAITING_CONFIRMATION` | remain `AWAITING_CONFIRMATION` |
| Expired `AWAITING_CONFIRMATION` | `EXPIRED` |
| Fresh `CONFIRMED` | remain `CONFIRMED` |
| Expired `CONFIRMED` | `EXPIRED` |
| Legacy `EXECUTING` / `UNKNOWN_OUTCOME` / `RECONCILING`, fresh, no mutation | `CONFIRMED` |
| Legacy `EXECUTING` / `UNKNOWN_OUTCOME` / `RECONCILING`, authority TTL expired, no mutation | `EXPIRED` |

### Exact, trusted, owner-valid source leave result

A legacy unresolved action may normalize to `SUCCEEDED` only after full identity / business equivalence verification (§13).

### Contradictory terminal pairs — HALT

Any of the following must **HALT migration** for explicit adjudication. Do **not** silently auto-correct contradictory terminal history:

- `EXECUTION_FAILED` + committed source leave
- `CANCELLED` + committed source leave
- `EXPIRED` + committed source leave
- `STALE` + committed source leave
- `SUCCEEDED` without a valid leave result

Supersede / expire active challenges attached to actions that are no longer `AWAITING_CONFIRMATION` according to target challenge semantics.

After normalization, re-run **all** invariant / duplicate checks (§7).

---

## 21. Final state check and final index

Only after:

1. old workers quiesced;
2. old unresolved state normalized;
3. invariant queries clean;

may migration:

- replace the transitional occupancy index with the final occupancy index (`AWAITING_CONFIRMATION`, `CONFIRMED`, `SUCCEEDED`);
- tighten the state `CHECK` to:

```text
AWAITING_CONFIRMATION
CONFIRMED
SUCCEEDED
EXECUTION_FAILED
STALE
CANCELLED
EXPIRED
```

---

## 22. Attack-7 retirement

Freeze 1.0 / Stage 4C–5A Attack-7 class: split submit vs classify. Those tests remain until replacements are green:

- `test_reconciliation_absence_versus_concurrent_submit_cannot_diverge`
- `test_reconciling_window_cannot_admit_a_late_insert`
- `test_attack7_reconcile_absence_cannot_finalize_failed_after_concurrent_insert`
- `test_attack7_submit_wins_first_resolves_succeeded`

Expanded replacement scenarios (required before deleting Attack-7 tests):

| ID | Scenario | Guarantee |
|---|---|---|
| A | Failure after leave `INSERT` but before action `SUCCEEDED` update | Roll back all → `CONFIRMED` + no leave |
| B | Failure after `SUCCEEDED` update but before audit | Roll back all |
| C | Audit insert failure | Roll back all |
| D | Deterministic failure before `INSERT` | Terminal failure + no leave |
| E | Valid existing-result adoption | `SUCCEEDED` + exactly one leave row |
| F | Invalid / cross-owner adoption | never `SUCCEEDED` |
| G | Lost commit acknowledgement | resolves to one atomic authoritative pair |
| H | Invariant query | no newly executed action has leave row + `EXECUTION_FAILED` / `STALE` / `EXPIRED` / `CANCELLED` |

Also retain the Freeze 2.0 crash/race matrix scenarios 1–22 as replacement-test requirements before Phase 3 deletion, with these corrections applied (transitional occupancy, `FOR UPDATE` loser, TTL checks, adopt equivalence, no `UNKNOWN`).

---

## 23. Crash / race regression matrix (corrected)

Deletion is forbidden until replacement tests for this matrix are green.

| # | Scenario | Target guarantee |
|---|---|---|
| 1 | Concurrent same-key PREPARE | Transitional then final occupancy unique; loser `FOR UPDATE` + verified reuse |
| 2 | PREPARE after `SUCCEEDED` | Return `RETURNED_SUCCEEDED`; no second action |
| 3 | Repeated confirmation | Idempotent replay; stay `CONFIRMED` |
| 4 | Confirm vs cancel race | First committed transition wins |
| 5 | Confirm vs expiry race | First committed transition wins |
| 6 | Two workers same action | `FOR UPDATE SKIP LOCKED`; one claimer |
| 7 | Worker death before leave `INSERT` | Rollback; remain `CONFIRMED` |
| 8 | Worker death during transaction | Rollback; no split pair |
| 9 | Commit succeeds, ack lost | Fresh read is one atomic pair; no `UNKNOWN` |
| 10 | Same action replay | `UNIQUE(source_action_id)`; terminal skip |
| 11 | Two actions same business key | Occupancy + leave unique; adopt or fail closed |
| 12 | Two different same-employee requests exceeding balance | Employee lock + **post-lock** balance query |
| 13 | Two different overlapping same-employee requests | Employee lock + **post-lock** overlap query |
| 14 | TTL expires after claim / during employee-lock wait / immediately before mutation | Any authoritative TTL check → `EXPIRED`; zero mutation |
| 15 | Draft integrity failure | Fail closed; no mutation |
| 16 | Material stable-authority change | `STALE`; no mutation |
| 17 | Transient DB failure | Rollback; remain `CONFIRMED`; process backoff / cooldown |
| 18 | Cross-owner authority attempt | Fail closed; no disclose/reuse |
| 19 | Chat `yes` authority injection | Non-authorizing |
| 20 | Action-id / confirmation bypass | Full binding required |
| 21 | No LLM / public execute surface | No execution tool |
| 22 | No duplicate business mutation | Both unique identities plus adoption equivalence |
| 23 | PREPARE loser identity mismatch | Fail closed; no foreign reuse |
| 24 | Expired `CONFIRMED` not filtered from poller | Claim → normalize `EXPIRED` |
| 25 | Cancel after worker rollback | Cancel may still win |
| 26 | Unexpected unique conflict after pre-probe | Entire txn rollback; fresh re-probe |
| 27 | Invalid adoption | never `SUCCEEDED` |

---

## 24. Corrected implementation phases

Deletion is forbidden until Phase 2C replacement tests are green.

This freeze does **not** authorize starting Phase 1A.

### Phase 1A — Data / additive safety

- invariant / duplicate preflight (§7); HALT on ambiguity
- normalize immediately safe expired rows where appropriate
- add `UNIQUE(source_action_id)` after leave-row checks
- add the **transitional six-state** occupancy index
- implement INSERT-first PREPARE race semantics
- PREPARE loser `FOR UPDATE` / verification / expiry normalization
- retain compatibility with the still-running old executor
- **do not** activate new execution yet
- **do not** create the final three-state index yet

The system must remain test-green on the still-present Freeze 1.0 execution path.

### Phase 2A — Cutover quiescence

- stop / prevent old workers
- stop old scheduling / enqueue path
- resolve or terminate in-flight old execution transactions
- authoritative old-state normalization (§20)
- verify clean invariants

### Phase 2B — Schema / authority cutover

- relax obsolete physical `NOT NULL` / write requirements needed by the new path (§18)
- replace transitional index with final occupancy index
- tighten target state `CHECK`
- disable old outbox / checkpoint scheduling
- activate atomic `CONFIRMED` poller
- confirmation no longer enqueues old work

### Phase 2C — Atomic execution proof

Before deleting old infrastructure:

- replacement crash / race / security matrix must be green
- old Attack-7 tests remain until replacements prove the new structural guarantees (§22)

### Phase 3 — Delete obsolete infrastructure

Only after the Phase 2C gate:

- LangGraph
- checkpoints
- outbox
- ledger
- leases
- fencing
- recovery services
- old states
- dead repositories (`OutboxRepository`, `ExecutionLedgerRepository`)
- split finalization
- obsolete physical columns
- unused dependencies

Do **not** collapse `action_workflows` / `action_revisions`.

Keep repositories that encode authority or transactional boundaries:

- `WorkflowRepository`
- `LeaveQueryRepository`
- `LeaveCommandRepository`
- `ChallengeRepository`
- `AuditRepository`

---

## 25. No `btree_gist` now

Do not add `btree_gist` or exclusion-constraint complexity.

Employee serialization plus post-lock validation is sufficient for the current single mutation path.

Exclusion constraints remain future defense-in-depth only.

---

## 26. Deferred infrastructure return triggers

If a future action executes against an **external** system (for example ServiceNow), reconsider:

- outbox / durable job
- external idempotency key
- attempt record
- UNKNOWN external outcome
- probe / reconciliation
- bounded retry / manual review
- lease / heartbeat
- fencing where needed

An external HTTP call is not automatically a reason to restore LangGraph.

Do not restore mechanisms merely because they existed historically.

---

## 27. Evaluation governance

| Item | Rule |
|---|---|
| This Freeze 2.1 commit | Documentation only. Code fingerprints unchanged. |
| Historical Run 1 | CLOSED — PARTIAL / PROVIDER-LIMITED. Immutable. Old subject. |
| Historical Run 2 | CLOSED — STARTED / STOPPED EARLY / PROVIDER-LIMITED. Immutable. Old subject. |
| Denominators | Must not be combined. |
| After later implementation | **New** evaluation subject. New development evaluation starts from **zero**. |
| Then | Development Failure Analysis → Development Closure → independent pre-holdout security/architecture review → only then create/freeze V4 holdout → `v0.5.0` later. |
| Current V4 holdout | **DOES NOT EXIST** |

Do not present historical Run 1 or Run 2 as evidence for the post-simplification subject.

---

## 28. Portfolio story

The project initially implemented distributed-workflow mechanisms beyond the actual same-database side-effect boundary.

Adversarial review identified the mismatch. Freeze 2.0 recorded the simplification direction. Targeted correctness review found the direction approved and the migration/locking/TTL/conflict details not yet implementation-safe.

Freeze 2.1 keeps the simplified core and freezes those safety details.

Preserved:

- LLM action safety
- HITL authority
- business idempotency
- cross-request invariants
- crash safety
- synthetic atomicity (mutation + state + audit)
- auditability

Deferred infrastructure has explicit return conditions.

This is architecture evolution, not a Freeze 1.0 history to hide.

---

## 29. Security invariant matrix (target)

- chat never authorizes execution
- model output never authorizes execution
- no LLM execution tool
- server-built confirmation draft
- token alone insufficient
- one live challenge
- confirmation replay idempotent
- confirmation does not execute the mutation
- after Phase 2B, confirmation does not enqueue work
- only post-lock DB revalidation inside the claimed execution transaction allows mutation
- `FOR UPDATE SKIP LOCKED` loser makes zero business calls
- PREPARE loser verifies owner/key/draft or fails closed
- one leave row per `source_action_id`
- one leave row per `business_request_key`
- adopt only on exact trusted equivalence
- overlap / balance rechecked **after** employee lock
- stale / expired actions do not execute
- uncovered calendar does not execute
- wrong owner cannot read / confirm / cancel / reuse
- cancel may win after a rolled-back worker claim
- transient failure is not definite failure
- Postgres is authoritative
- no checkpoint authority
- confirmation token never enters LLM context
- prompt injection cannot cross into execution authority
- no newly created leave row commits with `EXECUTION_FAILED` / `STALE` / `EXPIRED` / `CANCELLED`

---

## 30. Implementation boundary of this freeze

This document does not:

- edit workflow production code;
- edit migrations;
- remove dependencies;
- modify tests;
- call Gemini;
- resume Run 2;
- start Run 3;
- create a V4 holdout;
- begin Phase 1A.

Implementation of Freeze 2.1 may begin only after this freeze is committed, one final delta correctness review is complete, and Project Controller explicitly authorizes implementation.

# V4 Architecture Simplification Freeze 2.0

Status: **Frozen — target architecture; not implemented by this document**
Date: 2026-08-31
Decision: **SIMPLIFY BEFORE DEVELOPMENT CLOSURE**
Historical maximal architecture: [`docs/v4-architecture-freeze-1.0.md`](v4-architecture-freeze-1.0.md)
Historical implementation subject HEAD: `f14720485a54c85864e833573711918083af9081`

This document does **not** overwrite Freeze 1.0.

- Freeze 1.0 remains the historical **maximal** V4 architecture (LangGraph, outbox, ledger, leases, fencing, `EXECUTING` / `UNKNOWN_OUTCOME` / `RECONCILING`).
- Freeze 2.0 is the **target** architecture for the next implementation subject.
- This freeze is architecture and governance only. It does not edit workflow production code, migrations, dependencies, tests, gold, or evaluation artifacts.

Core principle (unchanged):

> LLM proposes; deterministic workflow executes.

New execution-boundary principle:

> Infrastructure is earned by the transaction boundary.

The current V4 side effect is a same-PostgreSQL `leave_requests` INSERT. Therefore execution authority, deterministic validation, business mutation, workflow finalization, and audit share **one PostgreSQL transaction**.

---

## Architecture review record

Three independent architecture reviews preceded this freeze:

1. **Grok Bot** — current V4 is materially overengineered; proposed a Minimum Safe V4; incorrectly removed employee serialization.
2. **Codex** — confirmed the simplification direction; proved employee-level serialization is still required; proposed a same-database atomic execution transaction.
3. **Fable** — independently inspected the repository at HEAD `f147204`; confirmed the Codex direction with corrections; recommended **SIMPLIFY BEFORE DEVELOPMENT CLOSURE**.

Project Controller final decision: **SIMPLIFY BEFORE DEVELOPMENT CLOSURE**.

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

Simplification creates a **new** V4 evaluation subject. After implementation, development evaluation starts from zero. See §17.

Provider incident status is unchanged. Local live Gemini diagnosis remains closed. This freeze does not call Gemini, resume Run 2, or start Run 3.

---

## 2. Target flow

### PREPARE

```text
Assistant
→ LLM READ/PREPARE only
→ trusted server identity
→ deterministic executable preparation
→ create/reuse immutable persisted action
→ AWAITING_CONFIRMATION
```

No business mutation. No outbox. No checkpoint start as an authority step.

### CONFIRM

```text
authenticated out-of-band endpoint
→ verify owner / session / challenge / token / TTL / action_id / draft_hash
→ consume challenge
→ action CONFIRMED
→ audit
→ ONE COMMIT
```

Confirmation does not execute the leave INSERT. Confirmation does not enqueue outbox or checkpoint work.

### EXECUTE

```text
simple poller
→ SELECT one CONFIRMED action
   FOR UPDATE SKIP LOCKED
→ re-check confirmed TTL
→ acquire employee transaction advisory lock
→ verify draft integrity
→ deterministic revalidation:
     calendar
     authority / ruleset
     effective balance
     overlap
→ INSERT or adopt leave_request
→ action SUCCEEDED | EXECUTION_FAILED | STALE
→ audit
→ ONE COMMIT
```

No persisted `EXECUTING`.

---

## 3. Frozen states

Exactly these seven states:

| State | Kind |
|---|---|
| `AWAITING_CONFIRMATION` | non-terminal |
| `CONFIRMED` | non-terminal (durable work) |
| `SUCCEEDED` | terminal |
| `EXECUTION_FAILED` | terminal |
| `STALE` | terminal |
| `CANCELLED` | terminal |
| `EXPIRED` | terminal |

Removed from the **target** architecture:

- `EXECUTING`
- `UNKNOWN_OUTCOME`
- `RECONCILING`

### Transitions

`AWAITING_CONFIRMATION`:

- valid confirmation → `CONFIRMED`
- owner cancellation → `CANCELLED`
- action expiry → `EXPIRED`
- authoritative draft/authority drift discovered before confirmation → `STALE`

`CONFIRMED`:

- atomic execution success → `SUCCEEDED`
- definite deterministic/business failure → `EXECUTION_FAILED`
- authority/draft obsolete; user must PREPARE again → `STALE`
- confirmed/execution freshness window exceeded → `EXPIRED`
- owner cancel only if the cancel CAS wins before the poller holds the action row → `CANCELLED`

Transient infrastructure failure does **not** transition the action. The transaction rolls back. The action remains `CONFIRMED`.

`SUCCEEDED` / `EXECUTION_FAILED` / `STALE` / `CANCELLED` / `EXPIRED` are terminal.

---

## 4. KEEP NOW

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
- partial unique action occupancy
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

---

## 5. REMOVE NOW

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

---

## 6. DEFER

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

## 7. Confirmation binding

Current immutable draft confirmation authority:

```text
action_id
+ draft_hash
+ server-authenticated owner / session
+ single-live challenge
+ hashed token
+ TTL
```

`revision=1` is **not** required in the product/API authority model.

Keep the existing frozen `revision` column physically for now where removing it would cause unnecessary FK/schema churn. Do **not** collapse `action_workflows` + `action_revisions` in this refactor.

Future editable drafts require a new architecture decision.

Token alone is never sufficient. Chat “yes” remains non-authorizing. Clients must not supply owner, session, jurisdiction, or draft authority fields.

---

## 8. Action-level idempotency

Keep action-level idempotency **separately** from business-mutation idempotency.

For the current schema, the target occupancy constraint is a partial unique index equivalent to:

```sql
UNIQUE (action_revisions.business_request_key)
WHERE state IN (
  'AWAITING_CONFIRMATION',
  'CONFIRMED',
  'SUCCEEDED'
)
```

`business_request_key` already binds the current annual-leave business identity:

```text
employee_id + leave_type + start_date + end_date
```

(canonicalization version `v4-canonical-1`; see `business_request_key()`).

### PREPARE concurrency — insert-race, not SELECT-before-INSERT

Do not rely only on SELECT-before-INSERT.

```text
attempt INSERT
→ on unique race, fresh transaction
→ load occupying action
→ normalize expiry if required
→ return persisted authoritative action
```

### Exact loser re-read semantics

1. Compute `business_request_key` from trusted identity plus the annual-leave dates.
2. Attempt `INSERT` of `action_workflows` + `action_revisions` in `AWAITING_CONFIRMATION` with the `ACTION_PREPARED` audit, one transaction.
3. If the `INSERT` succeeds: commit and return `CREATED`.
4. If the `INSERT` fails with a unique violation of the occupying partial unique index:
   1. Roll back that transaction completely. Do not retry `INSERT` in the aborted session.
   2. Open a **fresh** transaction.
   3. `SELECT` the occupying revision  
      `WHERE business_request_key = :key`  
      `AND state IN ('AWAITING_CONFIRMATION', 'CONFIRMED', 'SUCCEEDED')`.
   4. If none is found (occupant left occupying states between the violation and the re-read): commit the empty observation and retry PREPARE from step 2 in a new attempt. Bound this local retry; do not loop unbounded.
   5. If occupying `AWAITING_CONFIRMATION` or `CONFIRMED` is past its freshness window: apply the existing expiry normalize to `EXPIRED`, write audit, commit, and return that persisted authoritative (now `EXPIRED`) action. A later PREPARE may create a replacement because `EXPIRED` is not occupying.
   6. If occupying is live `AWAITING_CONFIRMATION` or `CONFIRMED`: return the persisted authoritative action / draft (`REUSED_EXISTING`).
   7. If occupying is `SUCCEEDED`: return `RETURNED_SUCCEEDED`. Do not create a second submit action.
5. SELECT-before-INSERT is an optimization only. Occupancy authority is the unique index plus this loser re-read.

Remove the creation-time **business-request advisory lock**. Occupancy is enforced by the partial unique index.

---

## 9. Business-mutation idempotency

`leave_requests` must enforce:

```sql
UNIQUE (business_request_key)
UNIQUE (source_action_id)
```

Separate guarantees:

| Constraint | Guarantee |
|---|---|
| `UNIQUE(source_action_id)` | One action cannot create two business mutations. |
| `UNIQUE(business_request_key)` | Two actions cannot create the same logical leave request. |

Remove `execution_key` from the **target** business-mutation identity. It is a Freeze 1.0 ledger/reservation artifact, not a current same-database mutation identity.

Adopt-existing remains valid: if a leave row for the same `business_request_key` already exists, the executing action must not insert a second row. `source_action_id` stays on the creating action.

---

## 10. Employee serialization

Keep **exactly one** transaction-scoped employee advisory lock:

```sql
pg_advisory_xact_lock(employee-derived key)
```

Purpose: protect **cross-request aggregate invariants**, including:

- effective balance
- overlapping leave

Acquire the lock:

1. **after** the action row is claimed (`FOR UPDATE SKIP LOCKED`);
2. **before** final balance / overlap validation;

and hold it until transaction commit or rollback.

Remove business-request advisory locking from the target architecture.

### Why business-request uniqueness cannot replace employee serialization

`UNIQUE(business_request_key)` serializes **identical** leave identity (`employee`, `leave_type`, `start_date`, `end_date`). It does **not** serialize two different requests of the same employee.

**76-hour / two 60-hour example**

- Effective remaining annual-leave balance: **76 hours**.
- Request A: **60 hours**, dates D1–D2 → `business_request_key` K_A.
- Request B: **60 hours**, dates D3–D4 → `business_request_key` K_B ≠ K_A.

Without an employee lock, both execution transactions can read remaining = 76, both pass `76 >= 60`, and both `INSERT`. Result: **120 hours** committed against a **76-hour** remainder.

With `pg_advisory_xact_lock(employee)` held across revalidation and `INSERT`, the second transaction re-reads effective balance as `76 − 60 = 16`, fails `INSUFFICIENT_BALANCE`, and does not insert.

**Overlap analog**

- Request A: Monday–Wednesday.
- Request B: Tuesday–Thursday.

Different date pairs → different `business_request_key` values. Uniqueness of K_A / K_B does not prevent overlapping rows. The employee lock plus overlap revalidation does.

Grok Bot’s Minimum Safe V4 incorrectly dropped this lock. Freeze 2.0 keeps it.

---

## 11. Failure model

### `EXECUTION_FAILED`

Only a **definite** deterministic / business failure. Include `failure_kind` such as:

- `INSUFFICIENT_BALANCE`
- `OVERLAP`
- `CALENDAR_UNCOVERED`
- `DRAFT_INTEGRITY_FAILURE`

### `STALE`

Authority or draft has become obsolete. The user should PREPARE again. Example: persisted `calendar_version` / `ruleset_version` / authority snapshot no longer matches the currently trusted versions.

### Transient infrastructure failure

Do **not** transition the action.

- Transaction rolls back.
- Action remains `CONFIRMED`.
- Poller may retry later until expiry.

Examples: deadlock, connection loss, statement timeout, commit-ack loss (see §13).

### `EXPIRED`

Terminal once the confirmation / execution freshness window is exceeded.

No `UNKNOWN_OUTCOME`. No `RECONCILING`.

---

## 12. Poller model

The action row is durable work. There is **no** transactional outbox.

```text
poll:
  state = CONFIRMED
  and execution freshness / TTL valid
  ORDER BY confirmation time
  FOR UPDATE SKIP LOCKED
```

Rules:

- one action per transaction;
- multiple pollers may safely partition work;
- crash releases locks and rolls back;
- no committed execution lease;
- no fencing generation;
- no durable attempt counter required in current V4.

Do **not** rebuild an outbox inside the action table. Do not add claim/lease/delivery/attempt/wakeup columns to `action_workflows` or `action_revisions`. `CONFIRMED` is business state, not a job record.

The API path may create, confirm, cancel, and read. It must not apply the leave-request mutation inside the confirmation request.

---

## 13. Lost-commit-ack recovery

If the client or worker loses `COMMIT` acknowledgement:

- do **not** write an `UNKNOWN` state;
- open a fresh transaction and observe authoritative PostgreSQL state.

Because mutation + finalization + audit are atomic, the only committed pairs are:

| Action state | Leave row |
|---|---|
| `CONFIRMED` | no leave row for this action |
| `SUCCEEDED` | leave row present (`source_action_id` = this action, or adopted existing) |

Recovery:

1. If the action is still `CONFIRMED` and freshness is valid: another poller (or a retry) may claim it and execute.
2. If the action is `SUCCEEDED` and the leave row exists: treat as complete. Do not insert again (`UNIQUE(source_action_id)` / `UNIQUE(business_request_key)`).
3. If freshness has been exceeded while still `CONFIRMED` with no leave row: `EXPIRED`. Zero mutation.

The original transaction’s **row lock** (`SELECT … FOR UPDATE`) prevents another poller from executing around an unresolved in-flight transaction. `FOR UPDATE SKIP LOCKED` must skip that locked row, not wait-and-steal, and must not invent a second execution.

There is no classify-versus-submit window. Attack-7 class divergence is structurally impossible under this boundary. See §16.

---

## 14. Atomic transaction boundary

Infrastructure is earned by the transaction boundary.

Current side effect: same-PostgreSQL `leave_requests` INSERT.

Therefore **one** execution transaction contains:

1. claim one `CONFIRMED` action (`FOR UPDATE SKIP LOCKED`);
2. re-check confirmed TTL;
3. acquire employee `pg_advisory_xact_lock`;
4. verify draft integrity (`draft_hash` / payload);
5. deterministic revalidation (calendar, authority/ruleset, effective balance, overlap);
6. `INSERT` or adopt `leave_requests`;
7. set `SUCCEEDED` or `EXECUTION_FAILED` or `STALE`;
8. write the audit event;
9. **one** `COMMIT`.

Confirmation is a **separate** transaction (challenge consume + `CONFIRMED` + audit). PREPARE is a **separate** transaction (create/reuse + audit).

Removed split: Freeze 1.0 T4 reservation / T5 apply / T6 outcome-reconcile.

---

## 15. Audit

Audit remains **non-authoritative**.

Every authoritative transition and its audit event must commit in the **same** transaction.

No decision may depend on audit history.

Confirmation tokens, plaintext secrets, and model transcripts must not appear in audit payloads.

---

## 16. Crash / race regression matrix

This list is frozen **before any deletion is allowed**. Implementation Phase 3 must not start until replacement tests for this matrix are green.

| # | Scenario | Target guarantee | Replacement test |
|---|---|---|---|
| 1 | Concurrent same-key PREPARE | Partial unique occupancy; loser re-reads and returns the persisted action | Two PREPARE threads, one `CREATED`, one reuse; one occupying row |
| 2 | PREPARE after `SUCCEEDED` | Occupying unique includes `SUCCEEDED`; return existing; no second action | PREPARE after success returns `RETURNED_SUCCEEDED`; no new revision |
| 3 | Repeated confirmation | Replay is idempotent; challenge already consumed; state stays `CONFIRMED` | Second confirm returns the confirmed action; no second challenge consume |
| 4 | Confirm vs cancel race | Exactly one CAS winner | One `CONFIRMED` or one `CANCELLED`; never both; zero leave rows if cancelled |
| 5 | Confirm vs expiry race | Exactly one CAS winner | One `CONFIRMED` or one `EXPIRED`; expired action does not execute |
| 6 | Two workers same action | `FOR UPDATE SKIP LOCKED`; one claimer; loser skips | Two pollers; one mutation; one terminal state |
| 7 | Worker death before leave `INSERT` | Rollback; action remains `CONFIRMED`; no leave row | Kill/fail before insert; row still `CONFIRMED`; later poller may execute |
| 8 | Worker death during transaction | Rollback releases row lock and advisory lock | In-flight fail; no `SUCCEEDED` without leave; no leave without `SUCCEEDED` |
| 9 | Commit succeeds, acknowledgement lost | Fresh read sees `SUCCEEDED`+leave **or** `CONFIRMED`+no leave | Replay/observe after silent commit; no `UNKNOWN`; no second insert |
| 10 | Same action replay | `UNIQUE(source_action_id)` plus state terminal | Second poll of `SUCCEEDED` is a no-op; one leave row |
| 11 | Two actions same business key | Occupancy unique plus `UNIQUE(leave_requests.business_request_key)` | Second occupying insert fails; adopt-existing; one leave row |
| 12 | Two **different** same-employee requests exceeding aggregate balance | Employee `pg_advisory_xact_lock` + effective-balance revalidation | 76-hour remainder; two 60-hour distinct keys; one `SUCCEEDED`, one `INSUFFICIENT_BALANCE` |
| 13 | Two **different** overlapping same-employee requests | Employee lock + overlap revalidation | Distinct keys, overlapping dates; one `SUCCEEDED`, one `OVERLAP` |
| 14 | Confirmed TTL expires after poll claim but before mutation | Re-check TTL after claim; `EXPIRED`; zero mutation | Claim then expire; state `EXPIRED`; no leave row |
| 15 | Draft integrity failure | Recompute hash; `EXECUTION_FAILED` / `DRAFT_INTEGRITY_FAILURE`; zero mutation | Tampered payload cannot insert |
| 16 | Stale authority / ruleset | Trusted calendar/ruleset mismatch → `STALE`; zero mutation | Old `calendar_version` cannot insert |
| 17 | Transient DB failure | Rollback; remain `CONFIRMED`; retry until expiry | Injected deadlock/timeout; no terminal failure; later success possible |
| 18 | Cross-owner authority attempt | Server identity; wrong owner cannot read/confirm/cancel/reuse | Foreign session denied; owner action unchanged |
| 19 | Chat `yes` authority injection | Chat is non-authorizing; no confirm/execute surface | Assistant `yes` does not confirm or insert |
| 20 | Action-id injection / confirmation bypass | Binding is owner+session+challenge+token+TTL+`action_id`+`draft_hash` | Foreign `action_id` / token-only / hash mismatch denied |
| 21 | No LLM / public execute surface | No execution tool; poller is internal | Tool allowlist unchanged; no public execute route |
| 22 | No duplicate business mutation | `UNIQUE(source_action_id)` and `UNIQUE(business_request_key)` | Replay and cross-action adopt produce one leave row |

### Mapping of tests that Freeze 1.0 infrastructure required

| Old failure mode / test class | New structural guarantee | Replacement |
|---|---|---|
| Outbox/confirm not atomic (`test_confirm_and_outbox_are_atomic`) | Confirm + challenge consume + audit are one commit; no outbox | Confirm atomicity without outbox insert |
| Graph resume as wake (`test_resume_is_wake_only_and_postgres_confirms`) | No graph; Postgres `CONFIRMED` is the only wake | Poller observes `CONFIRMED` only |
| Duplicate confirmation wake / second submit (`test_duplicate_confirmation_wake_does_not_create_a_second_submit`, `test_terminal_checkpoint_replay_never_enters_submit`) | No checkpoint; `UNIQUE(source_action_id)`; terminal skip | Replay after `SUCCEEDED` does not insert |
| Ledger duplicate / stale generation (`test_execution_ledger_duplicate_and_stale_generation`) | No ledger | Delete after Phase 2 replacements are green |
| `execution_key` uniqueness (`test_execution_key_cannot_be_duplicated`, `test_execution_key_replay_and_cross_action_dedupe`) | `UNIQUE(source_action_id)` + `UNIQUE(business_request_key)` | Replay/adopt tests without `execution_key` |
| Lease/fencing / `EXECUTION_AUTHORITY_LOST` | No lease; row lock + skip locked | Two-poller claim test |
| Reservation CAS (`test_confirmed_reserves_exactly_one_execution`) | One `FOR UPDATE` claimer | Two-poller / one-winner |
| Concurrent same business key at reservation (`test_concurrent_same_business_key_has_one_winner`) | Occupancy unique + leave unique | Same-key PREPARE + same-key execute adopt |
| Business-request advisory lock at PREPARE (`test_concurrent_identical_creation_produces_one_live_action`) | Partial unique + loser re-read | Same test, insert-race semantics |
| Reconciliation wake settlement / attempt counts | Removed | Delete after replacements |
| Checkpoint loss / corruption as orchestration failure | Removed; Postgres is the only durable workflow store | N/A — do not reintroduce checkpoint recovery tests |
| Manual outbox without confirm (`test_manual_outbox_without_confirm_is_not_used_to_authorize`) | No outbox to forge | Poller ignores non-`CONFIRMED` rows; no enqueue surface |

### Attack-7 replacement mapping

Freeze 1.0 / Stage 4C–5A Attack-7 class: **split submit vs classify**.

Historical interleaving:

- observe leave **absence**, then a concurrent `INSERT`, then finalize `EXECUTION_FAILED` while a leave row exists;
- or submit-first commit, then classify, which must observe the row and resolve `SUCCEEDED`.

Committed evidence tests (pre-simplification subject; do not rewrite in place):

- `test_reconciliation_absence_versus_concurrent_submit_cannot_diverge`
- `test_reconciling_window_cannot_admit_a_late_insert`
- `test_attack7_reconcile_absence_cannot_finalize_failed_after_concurrent_insert`
- `test_attack7_submit_wins_first_resolves_succeeded`

Those tests exist because mutation and outcome classification were **different transactions**.

**New structural guarantee:** leave `INSERT`/`adopt`, action terminal state, and audit are **one commit**. The pair `leave_row exists AND action == EXECUTION_FAILED` is unreachable. The pair `SUCCEEDED AND no leave row for that success` is unreachable (except adopt-existing, where the leave row already exists under the original `source_action_id`). There is no `RECONCILING` window and no late-submit path.

**Replacement tests (required before deleting Attack-7 tests):**

1. Worker death / rollback before commit → `CONFIRMED` + zero leave rows; later poller may succeed.
2. Lost commit-ack after success → fresh read is `SUCCEEDED` + one leave row; replay does not insert.
3. Two pollers on one `CONFIRMED` action → one `SUCCEEDED`, one skip; one leave row.
4. Invariant assertion: never `(leave_count > 0 AND state == EXECUTION_FAILED)` for the creating action; never `(state == SUCCEEDED AND creating action has no leave and no adopt)`.

Do not keep probe-then-finalize failpoints in the target design.

---

## 17. Evaluation governance

The simplification creates a **new** V4 evaluation subject.

| Item | Rule |
|---|---|
| Historical Run 1 | CLOSED — PARTIAL / PROVIDER-LIMITED. Immutable. Old subject. |
| Historical Run 2 | CLOSED — STARTED / STOPPED EARLY / PROVIDER-LIMITED. Immutable. Old subject. |
| Denominators | Must not be combined. |
| Subject fingerprint paths | Update after files are deleted or added. |
| After simplification | **New** development evaluation starts from **zero**. |
| Then | Development Failure Analysis → Development Closure → independent pre-holdout security/architecture review → only then create/freeze V4 holdout → `v0.5.0` later. |
| Current V4 holdout | **DOES NOT EXIST** |

Do not present historical Run 1 or Run 2 as evidence for the post-simplification subject.

---

## 18. Implementation phases

Deletion is forbidden until Phase 1 and Phase 2 replacement tests are green.

### Phase 1 — Additive safety constraints

Before deleting infrastructure:

- detect any existing duplicate occupying actions;
- add the partial unique action `business_request_key` index;
- add `UNIQUE(source_action_id)` on `leave_requests`;
- convert PREPARE to insert-race / loser re-read semantics;
- remove the creation-time business-request advisory lock.

The system must remain test-green on the still-present Freeze 1.0 execution path.

### Phase 2 — Atomic execution swap

- implement the new `CONFIRMED` poller;
- one action per transaction;
- row lock;
- employee transaction advisory lock;
- TTL check;
- deterministic revalidation;
- leave `INSERT` / adopt;
- final state;
- audit;
- one commit.

Confirmation **stops enqueueing** outbox / checkpoint work.

Replacement crash / race / security tests from §16 must pass **before** old mechanisms are deleted.

### Phase 3 — Infrastructure deletion

After the new path is proven:

- remove LangGraph;
- remove checkpointing;
- remove outbox;
- remove ledger;
- remove leases / fencing;
- remove old recovery states / services;
- remove split finalization;
- remove dead repositories;
- remove now-unused dependencies;
- schema migration drops obsolete tables / constraints / columns where approved.

Do **not** collapse `action_workflows` / `action_revisions`.

This freeze does not authorize starting Phase 1. Implementation requires a later Project Controller decision.

---

## 19. Repository decision

Do **not** broadly collapse the repository layer.

Keep repositories that encode authority or transactional boundaries:

- `WorkflowRepository`
- `LeaveQueryRepository`
- `LeaveCommandRepository`
- `ChallengeRepository`
- `AuditRepository`

Repositories belonging only to deleted infrastructure leave with it:

- `OutboxRepository`
- `ExecutionLedgerRepository`

---

## 20. No `btree_gist` now

Do **not** add `btree_gist` or exclusion-constraint complexity in the target freeze.

Employee serialization is sufficient for the current single mutation path.

Exclusion constraints are documented only as **future defense-in-depth** if production requirements or multiple mutation paths justify them. They are not a Freeze 2.0 implementation requirement.

---

## 21. Deferred infrastructure return triggers

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

Reconsider LangGraph only when orchestration is genuinely branching, multi-step, durable, and resumable.

Do not restore mechanisms merely because they existed historically.

---

## 22. Portfolio story

The project initially implemented distributed-workflow mechanisms — LangGraph checkpointing, transactional outbox, execution ledger, leases, fencing, and reconciliation — beyond the actual side-effect boundary, which is a same-database `leave_requests` INSERT.

Adversarial review identified the mismatch.

The design is simplified while preserving:

- LLM action safety
- HITL authority
- business idempotency
- cross-request invariants
- crash safety
- auditability

Deferred infrastructure has explicit return conditions (§21).

This is architecture evolution and evidence-based simplification. The Freeze 1.0 implementation is preserved as a historical subject, not framed as a mistake to hide.

---

## 23. Security invariant matrix (target)

- chat never authorizes execution
- model output never authorizes execution
- no LLM execution tool
- server-built confirmation draft
- token alone insufficient
- one live challenge
- confirmation replay idempotent
- confirmation does not execute the mutation
- confirmation does not enqueue work
- only DB revalidation inside the claimed execution transaction allows mutation
- `FOR UPDATE SKIP LOCKED` loser makes zero business calls
- one leave row per `source_action_id`
- one leave row per `business_request_key`
- overlap / balance rechecked under employee serialization
- stale / expired actions do not execute
- uncovered calendar does not execute
- wrong owner cannot read / confirm / cancel
- `CONFIRMED` may be cancelled only if cancel wins before the row claim
- transient failure is not definite failure
- Postgres is authoritative
- no checkpoint authority
- confirmation token never enters LLM context
- prompt injection cannot cross into execution authority

---

## 24. Implementation boundary of this freeze

This document does not:

- edit workflow production code;
- edit migrations;
- remove dependencies;
- modify tests;
- call Gemini;
- resume Run 2;
- start Run 3;
- create a V4 holdout;
- begin Phase 1.

Implementation of Freeze 2.0 may begin only after this freeze is committed and explicitly authorized.

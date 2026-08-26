# V3 / v0.4.0 Release Readiness

Status is based on repository evidence. V3 is **not released**. Product code is frozen at
`e93b5c1a476a4ed6983f60897839c016652971ba`.

## 1. Product implementation

**DONE**

- V3 architecture: provider-native bounded loop over existing V1/V2/prepare services
- deterministic READ tool registry: `knowledge_query`, `get_my_profile`,
  `get_my_leave_balances`, `get_my_ticket`
- trusted identity boundary via `AuthenticatedEmployeeContext`
- provider-native Agent loop with automatic function execution disabled
- safe result sealing (`untrusted_data`, citation cap, no raw protocol leakage)
- authenticated `POST /api/v1/assistant/query`
- PREPARE-only `prepare_leave_request`
- non-execution invariant (`non_executing=true`, no submit/persist/confirm tools)
- citation trust boundary (server-built V2 metadata only)
- Gemini continuation protocol: user → exact model candidate → user FunctionResponse
- V3-specific timeout isolation: `AGENT_TIMEOUT_SECONDS=60` per outer model round
- V3-specific retry isolation: `AGENT_MAX_ATTEMPTS=1` per model round
- leave provider-schema hygiene: provider `enum: ["annual"]`, local annual-only validation
- safe internal provider-failure taxonomy
- deterministic full test suite last recorded at freeze: 361 passed, 39 skipped
- Ruff lint and format checks passed at freeze
- no V3 database migration; Alembic head remains `0001_v2_knowledge`

## 2. Security / safety boundaries

**DONE**

- model/client tools cannot supply `employee_id`
- cross-user ticket access remains indistinguishable (`not_found_or_inaccessible`)
- tool outputs are marked and treated as untrusted data
- no tool capability beyond READ/PREPARE
- chat “yes” is not confirmation or authorization
- prepared leave actions are not persisted or executed in V3
- public API does not leak internal provider diagnostics
- citation metadata is server-built from trusted stored fields
- tool, model-round, and citation limits are enforced

## 3. Development evaluation

**IN PROGRESS**

- 16 total cases
- 5 completed
- 1 provider-blocked (`dev_agent_ticket_and_it_policy`)
- 10 not run
- 0 semantic/mechanical failures among completed cases
- semantic status accuracy `1.0` on completed/evaluable cases
- checkpoint preserved:
  `evals/results/v3-stage5a-development-agent-e93b5c1a476a4ed6983f60897839c016652971ba.json`
- frozen runtime recorded: `gemini-3.6-flash`, 60-second outer-round timeout, one SDK attempt
- `no_tuning_performed=true`

Exit criterion: all development cases evaluated sufficiently for review. Provider blocks are
resolved through compatible resume, not product tuning, unless a new deterministic defect appears.

## 4. Development review gate

**NOT STARTED / WAITING**

Must review after the development set is sufficiently complete:

- semantic status accuracy
- tool selection
- forbidden calls
- identity violations
- mutation invariants
- citations
- preparation structured accuracy
- non-executing invariant
- false execution claims
- prompt-injection behavior
- tool/model/citation bounds
- provider-blocked trace interpretation
- whether any gold corrections are justified

Do not approve yet.

## 5. Frozen holdout

**NOT STARTED**

- 8 frozen cases
- zero prior executions
- run only after development review approval
- no tuning after seeing holdout

## 6. Release hardening review

**NOT STARTED**

After successful evaluation, an independent review should cover:

- repo/security/adversarial review
- regression gates
- docs consistency
- release-note verification

Do not invoke that review until authorized.

## 7. Git/release procedure

**NOT STARTED** for the V3 release

When authorized:

`feature/v3-agent-tools` → `develop` → `main` → annotated tag `v0.4.0` → GitHub Release

No force push. Do not perform these actions until explicitly authorized.

## 8. Release blocker summary

V3 product implementation is frozen and deterministic gates pass.

`v0.4.0` is currently blocked on completion and review of the live development evaluation, then
the untouched frozen holdout and final release review.

Current provider 504/503 instability is classified as provider-blocked evaluation evidence, not a
confirmed product defect.

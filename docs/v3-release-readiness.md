# V3 / v0.4.0 Release Readiness

Status is based on repository evidence. V3 `v0.4.0` is **PUBLISHED**.

- Release commit: `d396122d368be8c4849872c233460da09a857b17`
- Annotated tag: `v0.4.0`
- GitHub Release: https://github.com/Tianqiyuba02/enterprise-ai-knowledge-action-agent/releases/tag/v0.4.0

Authoritative product behavior remains frozen at `2c05c8f9fe79b63e247dd6994e47176db8003763`.
This post-release documentation update occurs after the tagged artifact and is not part of
`v0.4.0`.

## 1. Product implementation

**DONE**

- V3 architecture: provider-native bounded loop over existing V1/V2/prepare services
- final scope remains READ + PREPARE only
- READ: `knowledge_query`, own profile, own leave balances, own ticket lookup
- PREPARE: deterministic non-executing annual-leave draft
- no business execution, mutation, confirmation execution, persisted action execution, or
  arbitrary employee identity selection
- the LLM proposes; deterministic application validates
- trusted identity boundary via `AuthenticatedEmployeeContext`
- provider-native Agent loop with automatic function execution disabled
- safe result sealing (`untrusted_data`, citation cap, no raw protocol leakage)
- authenticated `POST /api/v1/assistant/query`
- PREPARE-only `prepare_leave_request`
- relative-weekday grammar `"next <weekday>"` resolved from the trusted Melbourne date;
  incompatible model-proposed ISO dates are rejected without rewriting
- helpful knowledge fallback: unsupported execution requests may use READ `knowledge_query`
  for a trusted manual procedure, without claiming execution or mutating state
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

**PASS**

Final `v3-agent-eval-2` report:
`evals/results/v3-stage5a-development-agent-2c05c8f9fe79b63e247dd6994e47176db8003763.json`.

- fingerprint: `1b6fb7d7e7a813bae4d71e1459bf2d5e20ab611c6e9091f9bf4a556bf9ec3ee7`
- 16/16 completed
- 0 provider-blocked
- 0 evaluator/runtime errors
- 0 expectation misses
- semantic status accuracy `1.0`
- required-tool recall `1.0`
- tool-selection success `1.0`
- unnecessary-tool rate `0.0`
- forbidden-tool rate `0.0`
- identity violations `0`
- accepted `employee_id` violations `0`
- business mutations `0`
- citation integrity metrics passed
- prepared-action metrics passed
- non-executing invariant `1.0`
- false execution claims `0`
- prompt-injection undesired-call rate `0.0`
- tool/model/citation bound violations `0`
- frozen runtime: `gemini-3.6-flash`, timeout 60, max attempts 1, `ThinkingLevel.MINIMAL`
- `no_tuning_performed=true`

Historical `v3-agent-eval-1` evidence remains readable and must not be resumed:
`evals/results/v3-stage5a-development-agent-e93b5c1a476a4ed6983f60897839c016652971ba.json`.

## 4. Independent pre-holdout review

**PASS**

- reviewed HEAD: `c1eb121cb8c6bcd812c483fc9819c6361ce47936`
- blockers: 0
- high findings: 0

Medium backlog finding, not fixed: relative-weekday enforcement can over-constrain mixed-form
date requests. It fails closed and is deferred as post-V3 hardening.

## 5. Frozen holdout evaluation

**PASS**

First authorized exposure:
`evals/results/v3-stage5b-holdout-agent.json`.

- fingerprint: `b68a78f687b81040e265aef6d934d4879b3180405159cb4d5ed10ad923ba4d58`
- 8/8 attempted
- 8/8 completed/evaluable
- 0 provider-blocked
- 0 evaluator/runtime errors
- 0 expectation misses
- no tuning
- no holdout gold modification
- no PRODUCT FAILURE
- no HOLDOUT SPEC DRIFT
- semantic status accuracy `1.0`
- required-tool recall `1.0`
- tool-selection success `1.0`
- unnecessary-tool rate `0.0`
- forbidden-tool rate `0.0`
- identity violations `0`
- accepted `employee_id` violations `0`
- business mutations `0`
- required citation recall `1.0`
- forbidden citation hit rate `0.0`
- citation metadata validity `1.0`
- citation bound violations `0`
- prepared-action presence accuracy `1.0`
- preparation structured accuracy `1.0`
- non-executing invariant `1.0`
- forbidden prepared identifiers `0`
- false execution claims `0`
- tool/model/citation bound violations `0`

Prompt-injection undesired-call rate was `null` because no completed holdout case carried that
dedicated applicable label. That is not a `0.0` score.

Accidental `--split holdout` remains rejected. Authorized invocation still requires
`--live --authorize-holdout`.

## 6. Final release-candidate review

**PASS**

Reviewed RC: `0131bac1ab393513c1242d02d1e9a48c775c4fe1` on `feature/v3-agent-tools`.

- blockers: 0
- high findings: 0

## 7. Release preparation

**DONE**

Version, README, and readiness metadata were prepared on `feature/v3-agent-tools` before
publication.

## 8. Merge / release

**DONE / PUBLISHED**

- feature → develop: **DONE**
- develop → main: **DONE**
- Annotated tag `v0.4.0`: **DONE**
- GitHub Release: **PUBLISHED**
- `v0.4.0`: **PUBLISHED**

Publication path completed:

`feature/v3-agent-tools` → `develop` → `main` → annotated tag `v0.4.0` → GitHub Release

`v0.4.0` remains anchored to `d396122d368be8c4849872c233460da09a857b17`.

## 9. Final published status

| Gate | Status |
|---|---|
| Implementation | DONE |
| Security / hardening | PASS |
| Development evaluation | PASS — 16/16 |
| Independent pre-holdout review | PASS |
| Frozen holdout | PASS — 8/8 first exposure |
| Final RC review | PASS |
| Release preparation | DONE |
| feature → develop | DONE |
| develop → main | DONE |
| Annotated tag `v0.4.0` | DONE |
| GitHub Release | PUBLISHED |

Medium backlog finding, not fixed: relative-weekday enforcement can over-constrain mixed-form
date requests. It fails closed and remains deferred as post-V3 hardening.

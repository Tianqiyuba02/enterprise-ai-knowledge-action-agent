# V4 Product Evaluation

V4 has its own evaluation lifecycle. It does not reuse the V3 8-case frozen
holdout and does not claim an unbiased V4 result until a later independent
holdout exists.

## Controlled live validation (observation only)

Round 1 at `b3857d22bba7a392bcf5fd87fb98f55a7bd8336f`:

- 4/4 provider rounds completed
- 0 provider blocks
- no code, prompt, test, or gold changes

Observed live branches:

1. executable PREPARE → durable action `CREATED`
2. READ-only → no action
3. chat "Yes, submit it." → no confirmation/execution
4. PREPARE preview → deterministic V4 `NOT_CREATED` on the VIC AFL holiday

This was observation only. It is **not** V4 development evaluation evidence.

## Governance

```
development set
→ iterative tuning allowed
→ development closure
→ independent pre-holdout review
→ new frozen V4 holdout
→ one first exposure
→ RC review
```

The current 16 cases are marked `DEVELOPMENT`. They may later be inspected,
debugged, and tuned against. They must never be described as holdout.

No V4 holdout file exists. Holdout design happens only after development
closure plus independent pre-holdout review.

## Evaluator

- Evaluator version: `v4-product-eval-1`
- Development-set version: `v4-product-dev-1`
- Subject: Assistant request → AgentService → trusted tool result →
  deterministic V4 action behavior, plus out-of-band confirmation/worker/business
  outcome on explicitly marked E2E cases
- Scoring is deterministic. There is no LLM judge.

Stage 6A created the harness and the 16 DEVELOPMENT cases. There has been no
first development exposure yet. This is not a development-evaluation pass.

Three observation layers are stored separately:

- model / agent
- product / authority
- business outcome

Exact internal tool names are unavailable in the current observation contract.
`tool_trace_available` is always `false` and `tool_names` is `null`. That is not
the same as observing that no tools were called. Prepare-expectation scoring
uses the trusted `prepared_action` observation.

Provider-blocked cases are reported, excluded from semantic scoring, and never
treated as automatic semantic failures.

## Evaluation identities and resume

Resume is allowed only when all of these match:

- `evaluator_version`
- `development_set_version`
- development gold / dataset fingerprint
- `evaluation_subject_fingerprint` (agent instruction, tool allowlist,
  Agent/orchestration/provider/dispatcher/tool/Assistant/V4 product, and
  evaluator scoring code)
- `provider_config_fingerprint` (safe model, thinking level, timeout, attempts)
- `baseline_data_fingerprint` (isolated corpus, holiday rows, DemoRepository
  fixtures; embeddings are stored only as digests)
- evaluation business-clock identity (`v4-product-dev-1`, `2026-08-28`,
  `Australia/Melbourne`)

A docs-only change does not invalidate resume. A material Agent/product code
change, provider-config change, or baseline-content change does.

Secrets (`GEMINI_API_KEY`, credentials, confirmation tokens) are never
fingerprinted or written into result JSON.

## Evaluation business clock vs database operational clock

`v4-product-dev-1` injects `V4DevelopmentBusinessClock` at evaluator
construction. The trusted business date is `2026-08-28` in
`Australia/Melbourne`. It is used for Agent context and knowledge
authority-date interpretation.

It does not replace PostgreSQL `clock_timestamp()` used for row `created_at`,
action/challenge/lease TTLs, or audit timestamps. There are no public fake-date
headers and no client-spoofable production clock overrides.

## Safety metrics

The canonical zero-tolerance inventory has exactly seven rates:

1. `confirmation_bypass_violation_rate`
2. `action_authority_violation_rate`
3. `unauthorized_execution_violation_rate`
4. `duplicate_live_action_violation_rate`
5. `duplicate_business_mutation_violation_rate`
6. `non_executable_action_creation_violation_rate`
7. `wrong_owner_authority_violation_rate`

This 16-case DEVELOPMENT set has no labeled wrong-owner cases, so
`wrong_owner_authority_violation_rate` is `null` / N/A, not 0%.

## Isolated database

Live and PostgreSQL evaluator runs use a disposable database. Alembic upgrades
through `0003_v4_langgraph_checkpoints`. The V2 corpus is copied from the
normal development database without re-embedding. Baseline must be 12 documents,
42 chunks, `AU-VIC-2026-v1`, and 14 statewide holiday rows. After populate, the
evaluator computes `baseline_data_fingerprint` over document/chunk identity and
content, authority metadata, embedding digests, holiday rows, and trusted
DemoRepository fixtures. Counts alone are not sufficient. The normal
development database is a read-only source and is not reset.

## B1–B4 PREPARE decision

B1–B4 require a trusted PREPARE so the V4 authority layer is exercised. A model
refusal is a model-behavior miss. The workflow remains safe if no action is
created. Those two facts are recorded separately.

## F1

The evaluator is the out-of-band client. It issues a challenge, confirms with
the returned token, and runs the worker. The plaintext token exists only in
evaluator memory and is never written to result JSON or sent back to the model.

## Running

The CLI exists but Stage 6A must not execute the live 16-case set:

`enterprise-ai-eval --mode v4-product --split development --live`

`--split holdout` is rejected. A V4 holdout does not exist.

This document does not claim a V4 development pass. No first development run
has been executed.

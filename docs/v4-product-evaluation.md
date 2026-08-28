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

Three observation layers are stored separately:

- model / agent
- product / authority
- business outcome

Provider-blocked cases are reported, excluded from semantic scoring, and never
treated as automatic semantic failures. The runner is resumable when evaluator
version, development-set version, and fingerprints match.

## Isolated database

Live and PostgreSQL evaluator runs use a disposable database. Alembic upgrades
through `0003_v4_langgraph_checkpoints`. The V2 corpus is copied from the
normal development database without re-embedding. Baseline must be 12 documents,
42 chunks, `AU-VIC-2026-v1`, and 14 statewide holiday rows. The normal
development database is not the working dataset and is not reset.

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

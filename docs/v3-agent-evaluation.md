# V3 agent evaluation methodology

Stage 5A measures the real provider-native V3 agent without changing product behavior. Unit and API
tests remain the deterministic code-safety gate; this evaluator measures live model/tool selection
and orchestration.

## Frozen inputs

- trusted date: `2026-08-26`, injected through the application-owned clock;
- agent model: `gemini-3.6-flash`;
- outer-agent timeout: 60 seconds per outer model round / provider attempt for new runs;
- outer-agent attempts: one total SDK attempt per model round for new runs;
- bounds: 5 tool attempts and 7 model rounds;
- development: 16 cases, fingerprint
  `1b6fb7d7e7a813bae4d71e1459bf2d5e20ab611c6e9091f9bf4a556bf9ec3ee7`
  (supersedes historical
  `c8c8822bb4a6b7c6c3058d2c68328ec2c94a5e6b956459688c797e5f11c6bf7a`; old development reports
  remain evidence and cannot resume);
- holdout: 8 cases, fingerprint
  `b68a78f687b81040e265aef6d934d4879b3180405159cb4d5ed10ad923ba4d58`;
- evaluator schema: `v3-agent-eval-2` for new reports; and
- demo fixtures: `alex` and `sam`, resolved by the harness to trusted
  `AuthenticatedEmployeeContext` values.

The case datasets were created together before the development baseline. Accidental
`--mode agent --split holdout` remains rejected. Stage 5B required explicit
`--authorize-holdout` for the one frozen holdout campaign. That campaign is complete.

Development categories are:

| Category | Cases |
|---|---:|
| simple read | 3 |
| knowledge | 1 |
| multi-tool | 2 |
| prepare | 4 |
| authorization/safety | 2 |
| no-tool | 1 |
| execution boundary | 1 |
| prompt injection | 1 |
| conversational confirmation | 1 |

The prepare set includes relative-date interpretation, weekend exclusion, insufficient balance, and
a valid past date. Relative weekday interpretation is a product rule: `"next <weekday>"` is the
first occurrence of that weekday strictly after the trusted Australia/Melbourne date. The
application resolves that grammar deterministically and rejects incompatible model-proposed ISO
dates before draft creation. The past-date case asserts only structural preparation and does not
infer HR approval. Prepared drafts remain non-executing and expose the explicit date.

## Execution architecture

Live agent evaluation constructs the same `GeminiAgentClient`, `AgentService`, immutable registry,
`ToolDispatcher`, V1 services, V2 PostgreSQL-backed knowledge service, and deterministic
`LeavePreparationService` used by the product. The fixed clock is supplied to both agent date
context and knowledge retrieval.

`RecordingToolDispatcher` wraps the real dispatcher. It records only bounded safe arguments, the
safe/canonical tool name, result status, typed data kind, and whether the original trusted context
was preserved. It does not change dispatch, authorization, provider-visible data, the public API, or
the model transcript.

Each case snapshots synthetic employee profiles, leave balances, and tickets before and after the
run. Identity violations, accepted `employee_id` arguments, business mutations, citation overflow,
and tool/model bound violations are deterministic release-blocking invariants.

## Mechanical grading

No LLM judge is used. The report contains transparent mechanical measurements:

- semantic public-status accuracy for completed/evaluable cases;
- required-tool recall;
- tool-selection success: all required tools observed and no forbidden call observed;
- forbidden and unnecessary tool-call rates;
- trusted-context and business-mutation counts;
- required and forbidden structured citation measurements and metadata validity;
- structured prepared-action presence and field accuracy;
- `non_executing=true` and forbidden identifier checks;
- narrow lexical false-execution-claim checks defined in each applicable case;
- prompt-injection undesired forbidden-call count;
- mean tool attempts; and
- tool, model-round, and citation-bound violation counts.

Metrics with no applicable cases are `null` (`N/A`), not fabricated as perfect scores. Model prose
never supplies citation or prepared-action truth.

## Provider blocking and resume

Provider-blocked cases are distinct from completed semantic/model failures. They are recorded with
safe `provider_failure` diagnostics and excluded from semantic/model-success scoring. A provider
availability failure is evidence; it does not convert the case into a pass or a semantic fail, and
it does not terminate the rest of the split. Later independent development cases continue in the
same invocation. Each case receives at most one evaluation attempt per invocation.
`--resume` carries completed compatible cases forward, retries blocked/error cases while preserving
attempt history, and continues previously unrun cases without duplicate result rows.

New reports use evaluator schema `v3-agent-eval-2`. Historical `v3-agent-eval-1` reports remain
readable as evidence but cannot be resumed under the new control-flow semantics. The final
development baseline started fresh under `v3-agent-eval-2`; the 5/16 `v3-agent-eval-1`
checkpoint was not migrated.

Provider-blocked rows may include an optional safe `provider_failure` diagnostic (`kind`,
allowlisted exception class, optional HTTP code, optional sanitized symbolic status). Messages,
bodies, headers, and request IDs are not stored. Historical reports that omit the field remain
readable. The field is not a resume-compatibility parameter.

Resume compatibility includes the frozen configuration compared by the runner: agent model, outer
timeout, max attempts, trusted date, tool/model bounds, tool-registry fingerprint, demo fixture
version, knowledge settings, corpus identity/counts, evaluator schema, split, and dataset
fingerprint.

Historical reports created before reliability isolation omit timeout/attempt fields and resolve to
the inherited 30-second agent timeout and two-attempt retry policy. Those reports cannot be resumed
into the frozen 60-second, one-attempt configuration.

## Final development baseline

Final `v3-agent-eval-2` development report:
`evals/results/v3-stage5a-development-agent-2c05c8f9fe79b63e247dd6994e47176db8003763.json`.

Fingerprint: `1b6fb7d7e7a813bae4d71e1459bf2d5e20ab611c6e9091f9bf4a556bf9ec3ee7`.

Frozen runtime: `gemini-3.6-flash`, `AGENT_TIMEOUT_SECONDS=60`, `AGENT_MAX_ATTEMPTS=1`,
`ThinkingLevel.MINIMAL`.

- 16/16 completed
- 0 provider-blocked
- 0 evaluator/runtime errors
- 0 not-run
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
- no gold-label correction after this baseline
- no tuning performed

## Historical development checkpoints

The historical `v3-agent-eval-1` freeze-HEAD development report is
`evals/results/v3-stage5a-development-agent-e93b5c1a476a4ed6983f60897839c016652971ba.json`.
It remains evidence only and must not be resumed under `v3-agent-eval-2`.

Historical factual status under the previous stop-on-block control flow:

- 16 development cases;
- 5 completed;
- 1 provider-blocked (`dev_agent_ticket_and_it_policy`, 7 preserved attempts);
- 10 not run;
- semantic status accuracy `1.0` on completed/evaluable cases;
- no observed semantic/mechanical failures among those five;
- no gold-label correction;
- no tuning performed.

Case 6 later demonstrated successful selection of `get_my_ticket` and `knowledge_query`. Observed
provider failures included HTTP 504 `DEADLINE_EXCEEDED` and HTTP 503 `UNAVAILABLE`. Those remain
historical provider-blocked evidence, not completed semantic failures.

## Development command

```bash
uv run enterprise-ai-eval \
  --mode agent \
  --split development \
  --live \
  --delay-seconds 2
```

Compatible `v3-agent-eval-2` partial runs use the same command with `--resume` against a new-schema
report. Do not `--resume` the historical `v3-agent-eval-1` checkpoint.

## Holdout activation

This is a CLI/control-plane activation only. Report structure, scoring, result statuses, attempt
history, and resume compatibility remain `v3-agent-eval-2`. The passing development baseline is
not invalidated.

Frozen holdout: 8 cases, fingerprint
`b68a78f687b81040e265aef6d934d4879b3180405159cb4d5ed10ad923ba4d58`. First authorized
exposure completed the campaign.

Accidental invocation remains rejected:

```bash
uv run enterprise-ai-eval --mode agent --split holdout --live
```

Authorized campaign command:

```bash
uv run enterprise-ai-eval \
  --mode agent \
  --split holdout \
  --live \
  --authorize-holdout \
  --delay-seconds 2
```

`--authorize-holdout` is valid only with `--split holdout`. The CLI accepts only the frozen
holdout fingerprint above.

### Final holdout result

First-exposure report: `evals/results/v3-stage5b-holdout-agent.json`.

- 8/8 attempted
- 8/8 completed/evaluable
- carried `0`
- completed_now `8`
- 0 provider-blocked
- 0 evaluator/runtime errors
- 0 not-run
- 0 expectation misses
- no tuning
- no holdout gold modification
- no PRODUCT FAILURE
- no HOLDOUT SPEC DRIFT

Aggregate holdout metrics:

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

### Holdout campaign and resume

One frozen holdout campaign:

- the initial authorized invocation attempts all 8 cases;
- provider-blocked cases are recorded and excluded from semantic scoring;
- later cases continue in the same invocation;
- each case is attempted at most once per invocation.

If all 8 are evaluable, the campaign is complete.

If one or more cases are provider-blocked, a compatible `--resume` may retry only those
incomplete cases and carry completed cases forward. Resume is allowed only when every frozen
compatibility field remains identical: product behavior, evaluator schema, dataset fingerprint,
model, timeout, max attempts, thinking, tool registry, corpus identity, and the other existing
`v3-agent-eval-2` fields. No product, config, or evaluator change may occur between initial
holdout exposure and that provider-only resume.

A semantic or mechanical holdout miss must never cause tuning and rerunning the holdout.

### Adjudication

Holdout outcomes are adjudicated as:

- **PASS** — current product satisfies the frozen expectation.
- **PRODUCT FAILURE** — current product violates an approved current product or safety
  specification.
- **HOLDOUT SPEC DRIFT** — a frozen expectation conflicts with a product specification that was
  explicitly approved before holdout exposure. Preserve the raw frozen result. Do not silently
  rewrite the frozen holdout. Do not tune the product to pass the old expectation. Document
  adjudication separately.
- **PROVIDER BLOCK** — no semantic judgment; eligible only for compatible provider-only resume.

First-exposure adjudication: **PASS**. No PRODUCT FAILURE. No HOLDOUT SPEC DRIFT. No provider
block required a resume.

Pre-existing approved product changes that were eligible for drift adjudication if a conflict
appeared: relative weekday convention, deterministic relative-weekday enforcement, and helpful
knowledge fallback. None required drift adjudication.

### Independent pre-holdout review

Reviewed HEAD: `c1eb121cb8c6bcd812c483fc9819c6361ce47936`.

Verdict: **PASS**. Blockers: 0. High findings: 0.

Medium backlog finding, not fixed: relative-weekday enforcement can over-constrain mixed-form
date requests because a matched relative weekday may constrain both prepare endpoints. This
fails closed and is deferred as post-V3 hardening.

# V3 agent evaluation methodology

Stage 5A measures the real provider-native V3 agent without changing product behavior. Unit and API
tests remain the deterministic code-safety gate; this evaluator measures live model/tool selection
and orchestration.

## Frozen inputs

- trusted date: `2026-08-26`, injected through the application-owned clock;
- agent model: `gemini-3.6-flash`;
- outer-agent timeout: 60 seconds for new runs;
- bounds: 5 tool attempts and 7 model rounds;
- development: 16 cases, fingerprint
  `c8c8822bb4a6b7c6c3058d2c68328ec2c94a5e6b956459688c797e5f11c6bf7a`;
- holdout: 8 cases, fingerprint
  `b68a78f687b81040e265aef6d934d4879b3180405159cb4d5ed10ad923ba4d58`;
- evaluator schema: `v3-agent-eval-1`; and
- demo fixtures: `alex` and `sam`, resolved by the harness to trusted
  `AuthenticatedEmployeeContext` values.

The case datasets were created together before the development baseline. Stage 5A refuses an agent
holdout CLI invocation. The holdout has not been run or used for tuning.

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
a valid past date. The past-date case asserts only structural preparation and does not infer HR
approval.

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

Provider rate limits and unavailability are `provider_blocked`, separate from semantic failures.
The runner stops after the first provider-blocked case to avoid repeated calls and writes a partial
report. `--resume` carries completed cases forward, retries blocked/error cases, and continues
unattempted cases without duplicate results.

Resume rejects mismatched dataset fingerprint, split, agent model, trusted date, bounds, tool
registry fingerprint, evaluator schema, demo fixture version, knowledge configuration, or corpus
identity. It also rejects a different effective outer-agent timeout.

Historical reports created before timeout isolation omit that field and resolve to the inherited
30-second agent timeout. The separately named corrected-continuation 5/16 checkpoint remains
historical evidence only; it cannot be resumed under the 60-second configuration.

## Development command

```bash
uv run enterprise-ai-eval \
  --mode agent \
  --split development \
  --live \
  --delay-seconds 2
```

Compatible partial runs use the same command with `--resume`. The default report is
`evals/results/v3-stage5a-development-agent.json`.

The holdout remains frozen until a separately authorized Stage 5B.

# V3 Implementation Notes

## Current stage

Product Milestone V3 — Agent + Tools is under **code freeze** on `feature/v3-agent-tools`.
Deterministic product code is frozen at `e93b5c1a476a4ed6983f60897839c016652971ba`. Later commits
may add evaluation evidence, evaluator-harness control-flow, or documentation. Frozen product
behavior is unchanged.

V3 is **not released**. Live development evaluation passed. The frozen holdout has **not** been
run. Stage 5B activates an explicit `--authorize-holdout` CLI path without changing product
behavior or `v3-agent-eval-2` report semantics. Target release remains `v0.4.0` after holdout and
release review. See [`docs/v3-release-readiness.md`](v3-release-readiness.md).

## Frozen V3 design

The LLM proposes and selects tools. Deterministic application code validates arguments, injects
trusted identity, and performs only allowed READ and PREPARE operations.

### Scope

READ tools:

- `knowledge_query`
- `get_my_profile`
- `get_my_leave_balances`
- `get_my_ticket`

PREPARE tool:

- `prepare_leave_request`

V3 does not support execution, persisted prepared actions, chat-message confirmation as
authorization, LangGraph, or a HITL execution workflow. Those belong to V4.

### Bounded agent loop

- Provider-native Gemini function calling on `gemini-3.6-flash`.
- Automatic function execution is disabled.
- `ToolRegistry` / `ToolDispatcher` is the business boundary.
- Maximum 5 tool calls and 7 model rounds per user turn.
- Single-turn, in-memory interaction; no conversation persistence.
- Identity and applicability come only from server-trusted context.
- Tool results are treated as untrusted data and cannot become instructions.

Relative weekday convention: `"next <weekday>"` means the first occurrence of that weekday
strictly after the trusted Australia/Melbourne current date. The application resolves this supported
grammar from the trusted Melbourne date and exposes the ISO date as trusted model context. Before
PREPARE, incompatible model-proposed ISO dates are rejected without rewriting them.
`LeavePreparationService` arithmetic remains unchanged. A V3 PREPARE output remains non-executing
and exposes the resulting explicit date.

Helpful knowledge fallback: when a user asks V3 to perform an unsupported action, the assistant
must state that it cannot perform and did not perform the action. It may use `knowledge_query` for
a trusted manual procedure or next steps when that information is relevant. Guidance must remain
distinct from execution. This does not add an execution capability or mutate business state.

Post-V3 hardening backlog, not a pre-holdout blocker: the current relative-weekday constraint can
over-constrain mixed-form date requests because a matched relative weekday may constrain both
prepare endpoints. This fails closed. Do not change the frozen product implementation for it.

Public surface: authenticated `POST /api/v1/assistant/query`.

### Gemini continuation protocol

Native continuation uses this history shape:

1. original user content;
2. the exact original model candidate `Content`;
3. user-role `Content` containing typed `FunctionResponse` parts.

The original model candidate is retained, including part order. Function-call IDs are preserved when
the provider supplies them. Thought signatures stay inside the original model parts. Multiple
function responses for one model turn are returned together in one user `Content`. An earlier
`role="tool"` continuation was a protocol defect and was corrected.

### Frozen outer-agent reliability policy

V3 outer-agent settings:

- `AGENT_TIMEOUT_SECONDS = 60`
- `AGENT_MAX_ATTEMPTS = 1`
- `ThinkingLevel.MINIMAL`

The 60-second timeout applies to **one outer model round / provider attempt**. It is not a total
Agent-turn deadline. A complete interaction may include several model rounds. V3 uses one SDK
attempt per model round.

V1 generation, V2 grounded generation, and V2 embeddings remain:

- `GEMINI_TIMEOUT_SECONDS = 30`
- `GEMINI_MAX_ATTEMPTS = 2`

The policies differ because V3 is an interactive multi-round agent. Automatic retry amplification
can produce unacceptable user-facing latency.

### Provider failure diagnostics

Internal classification kinds:

- `invalid_request`
- `authentication_or_permission`
- `timeout`
- `cancelled`
- `rate_limited`
- `provider_unavailable`
- `transport_error`
- `unknown_provider_error`

Safe diagnostic fields: `kind`, allowlisted `exception_class`, optional HTTP status, optional
sanitized symbolic status. Exception messages, HTTP bodies, headers, request IDs, prompts, tool
payloads, and thought signatures are not persisted or exposed.

The public assistant API remains coarse: rate limits map to `assistant_model_rate_limited`, and
other provider failures map to `assistant_model_unavailable`. Internal diagnostics do not appear in
the public response.

### Leave preparation schema hygiene

Local validation still accepts only `annual`. The provider-visible FunctionDeclaration schema emits
`enum: ["annual"]` instead of `const: "annual"`. Application semantics did not change.

### Evaluation methodology

Development and holdout are separate. Development may be iterated and reviewed. Holdout stays
frozen until a development review is accepted. Provider-blocked cases are distinct from completed
semantic/model failures. Reports record `no_tuning_performed`.

Resume compatibility includes the frozen configuration currently compared by the runner: agent
model, outer timeout, max attempts, trusted date, tool/model bounds, tool-registry fingerprint,
demo fixture version, knowledge settings, corpus identity/counts, evaluator schema, split, and
dataset fingerprint. Optional `provider_failure` diagnostics are not a compatibility parameter.

New reports use evaluator schema `v3-agent-eval-2`. A provider-blocked case is recorded and
excluded from semantic scoring, then the runner continues to later cases in the same invocation.
Historical `v3-agent-eval-1` reports remain readable but cannot be resumed.

### Current development status

Development dataset: 16 cases, fingerprint
`1b6fb7d7e7a813bae4d71e1459bf2d5e20ab611c6e9091f9bf4a556bf9ec3ee7`.
This supersedes historical development fingerprint
`c8c8822bb4a6b7c6c3058d2c68328ec2c94a5e6b956459688c797e5f11c6bf7a` after an intentional
product-spec correction that allows READ knowledge guidance for unsupported execution requests.
Historical development reports remain evidence and cannot be resumed under the new fingerprint.

Historical `v3-agent-eval-1` checkpoint
`evals/results/v3-stage5a-development-agent-e93b5c1a476a4ed6983f60897839c016652971ba.json`
(evidence only; do not resume):

- 5 completed
- 1 provider-blocked (7 Case 6 attempts)
- 10 not run

Completed/evaluable cases:

- semantic status accuracy `1.0`
- no observed semantic/mechanical failures among those five
- forbidden-tool rate `0`
- identity violations `0`
- business mutations `0`
- required citation recall `1.0` on applicable completed cases
- no gold-label correction proposed
- no tuning performed

Development evaluation is not complete. The next live development baseline must start fresh under
`v3-agent-eval-2`.

`dev_agent_ticket_and_it_policy` (Case 6) remains provider-blocked. Prior attempts have shown
successful round-1 selection of both `get_my_ticket` and `knowledge_query`, and both tools have
executed successfully. Other attempts were blocked before dispatch. Observed provider failures
include HTTP 504 `DEADLINE_EXCEEDED` and HTTP 503 `UNAVAILABLE`. These are classified as
provider-blocked, not deterministic semantic or product failures. The root cause is not claimed to
be localized inside the provider.

### Holdout status

Frozen holdout: 8 cases, fingerprint
`b68a78f687b81040e265aef6d934d4879b3180405159cb4d5ed10ad923ba4d58`. **0 executed.** Accidental
`--split holdout` remains rejected. Authorized Stage 5B invocation requires `--authorize-holdout`
and the frozen fingerprint. The holdout has not been executed.

## Historical stage notes

The sections below record how V3 was built. They are not the current freeze status.

## Provider capability

The existing `google-genai` 2.19.0 SDK and current credential were used with the isolated candidate
`gemini-3.6-flash`.

Measured provider-native behavior:

- selected one synthetic ticket function for an appropriate prompt;
- returned parseable `ticket_id: TKT-1001`;
- made no function call for an unrelated arithmetic prompt;
- selected `get_my_profile` from profile/ticket declarations for a profile prompt;
- selected `get_ticket` with the correct argument for a ticket prompt; and
- left automatic function execution disabled.

One provider deadline occurred between checks. A single bounded follow-up completed the remaining
capability checks. No synthetic function was executed and no business state was touched.

## Frozen Stage 0 contracts

- Agent model: `AGENT_MODEL=gemini-3.6-flash`
- Initial tool-call budget: 5 per user turn
- Runtime allowlist: four read-only contracts
- Identity/applicability: injected from trusted V1/V2 context
- Tool results: untrusted data
- Execution tools: absent
- Public endpoint direction: `POST /api/v1/assistant/query`
- LangGraph: deferred to V4

ADR 0005 records the full trust, read/prepare, loop, endpoint, and V4 deferral decisions.

## Stage 1 deterministic read-tool layer

The immutable registry contains exactly `knowledge_query`, `get_my_profile`,
`get_my_leave_balances`, and `get_my_ticket`. Each contract owns a strict Pydantic argument model,
read classification, provider-safe description, and fixed handler key. Provider declarations are
generated only from these trusted contracts.

`ToolDispatcher` receives `AuthenticatedEmployeeContext` from application code, validates a
provider-neutral name/arguments object, and calls the existing employee, IT, or V2 knowledge
service directly. Knowledge applicability remains server-derived. Cross-user and absent tickets
produce the same `not_found_or_inaccessible` envelope.

Success data is narrowed into profile, leave, ticket, or knowledge models without employee IDs or
internal document/chunk IDs. Every result is marked `untrusted_data=true`. Validation,
not-found/inaccessible, temporary/provider availability, budget, and internal failures are bounded
typed data; raw infrastructure details are discarded.

There is no automatic chaining, model invocation, public route, or SDK dispatch in Stage 1.

## Recommended Stage 2 scope

Implement the bounded provider request/response adapter and one linear model→validated call→manual
dispatch→typed result→model cycle with hard budget enforcement and mocked loop tests. Add the
authenticated assistant route only after that internal loop passes.

Leave calculation, typed preparation tools, and multi-tool user journeys should follow in later V3
stages. Execution, confirmation, proposal persistence, idempotency, audit, and LangGraph remain V4.

## Stage 0 verification

- ordinary provider-free suite: 184 passed, 39 explicitly gated PostgreSQL tests skipped;
- V3 Stage 0 contract/configuration tests: 4 passed;
- Ruff lint and format checks: passed; and
- Alembic head remains `0001_v2_knowledge` with no V3 migration.

Released V1/V2 routes, services, corpus, evaluation data, prompts, model boundaries, and persistence
were not modified.

## Stage 1 verification

- ordinary provider-free suite: 205 passed, 39 explicitly gated PostgreSQL tests skipped;
- all V3 configuration/registry/dispatch tests: 25 passed;
- Ruff lint and format checks: passed; and
- Alembic head remains `0001_v2_knowledge` with no V3 migration.

No agent loop, public assistant route, prepare tool, write path, proposal, confirmation, LangGraph,
or V4 behavior was added.

## Stage 1.1 pre-agent boundary hardening

Before provider-visible transcripts are introduced:

- malformed/hostile requested names are reduced to `unknown_tool`; only canonical names or safe
  lowercase/underscore unknown names can be echoed;
- provider `args=None` normalizes to `{}` only at the provider adapter boundary, while strict local
  models still reject missing required ticket/question fields;
- `ToolResultStatus` is the single result vocabulary; the unused divergent error enum was removed;
- knowledge answers have an independent strict 4,000-character bound;
- registry mutation raises `TypeError`;
- every provider parameter schema is asserted equal to the local Pydantic schema;
- applicability failures and ticket newline edge cases return bounded envelopes; and
- the transcript-visible outer result shape is locked by deterministic tests.

### Stage 2 transcript entry requirements

Tool results are untrusted data. Stage 2 must use one trusted serializer with explicit data framing,
must never interpolate result fields into system/developer instructions, must keep automatic
function execution disabled, and must route every requested call through `ToolDispatcher`.
Instruction-like text inside result fields remains data only; these controls do not claim prompt
injection is universally solved.

Before provider transcript integration, review whether every currently approved profile field
(including full name and work email) is necessary for the agent. No profile field was removed in
Stage 1.1 because that data-minimization decision belongs to Stage 2.

## Stage 1.1 verification

- ordinary provider-free suite: 218 passed, 39 explicitly gated PostgreSQL tests skipped;
- original V1 API regression subset: 19 passed;
- V2 regression subset: 138 passed;
- all V3 Stage 0/1/1.1 tests: 38 passed;
- Ruff lint and format checks: passed; and
- Alembic head remains `0001_v2_knowledge` with no V3 migration.

No provider transcript serializer, model loop, public route, preparation capability, write path,
state, proposal, confirmation, LangGraph, HITL, or V4 execution behavior was added.

## Stage 2 bounded read-agent loop

`AgentService.run(message, AuthenticatedEmployeeContext)` now owns one in-memory provider/tool
conversation. Gemini SDK content and function-call IDs remain inside `GeminiAgentSession`; the
service sees only bounded final text, provider-neutral requested calls, and trusted tool responses.

Hard limits are five attempted tool calls and seven model rounds per turn. Multiple calls from one
model response are dispatched sequentially in provider order. Valid, invalid, and unknown calls all
consume budget. A sixth call is never dispatched, and round/budget exhaustion terminates without
another persuasion loop.

Automatic SDK function execution remains disabled. Every request passes through `ToolDispatcher`.
The single function-response serializer uses `ToolResult.model_dump(mode="json")` inside native
Gemini function-response parts; result strings are never inserted into the system instruction.
Tool failures return to the model as bounded untrusted data while budget remains.

The internal `AgentRunResult` exposes only status, optional final text, server-owned V2 citations,
safe failure text, and round/call counts. Provider call IDs, raw calls/results, employee IDs,
vectors, and hidden reasoning are absent. Citations are collected only from successful
`KnowledgeToolData` and deduplicated by trusted stored metadata.

### Profile data minimization

Provider-visible profile data remains limited to the approved `ProfileToolData` fields. Full name
and work email support direct self-profile questions; location and employment type support workplace
context; hours/day, work days, and timezone describe the employee's own schedule; active state
describes the authenticated fixture record. Repository objects and `employee_id` are never
serialized. Reassess these fields before adding prepare tools.

### Live smoke

One bounded live read-agent smoke requested annual-leave policy plus the authenticated employee's
balance. The agent requested two tools and collected approved `POL-HR-001` v2.0 citations. Final
synthesis ended as the safe `provider_unavailable` outcome on round two, so no final answer was
claimed and no retry was attempted. No business mutation occurred.

Stage 2 remains read-only and single-turn. There is still no public assistant route, conversation
memory, persistence, prepare capability, execution, confirmation, LangGraph, or V4 behavior.

## Stage 2 verification

- ordinary provider-free suite: 238 passed, 39 explicitly gated PostgreSQL tests skipped;
- original V1 API regression subset: 19 passed;
- V2 regression subset: 138 passed;
- all V3 Stage 0–2 tests: 58 passed;
- Ruff lint and format checks: passed; and
- Alembic head remains `0001_v2_knowledge` with no V3 migration.

No public assistant route, prepare tool, write path, persisted conversation/agent state, proposal,
confirmation, LangGraph, HITL, or V4 execution behavior was added.

## Stage 2.1 result sealing before API exposure

`MAX_AGENT_CITATIONS=24` now bounds every terminal `AgentRunResult`. Citation collection still
accepts only successful `KnowledgeToolData`, deduplicates by trusted stored metadata, preserves
first-seen order, and stops adding new unique citations after 24. The cap bounds provider/public
payload size; it does not alter V2 retrieval authority or imply that every retrieved citation must
be surfaced.

Unexpected provider-session/parsing `Exception` values are sealed immediately around provider
interaction and become fixed `unable_to_complete` results. Typed timeout, rate-limit, unavailable,
and malformed-response mappings retain their existing behavior. Exception text, SDK payloads, and
credentials are never copied into the result.

Native function responses preserve the original requested function name only when it exactly
matches the dispatcher's safe result name. Hostile/malformed names use the bounded sanitized result
name; provider call IDs remain internal. No unknown tool is dispatched.

Tests cover citation overflow for completed, tool-budget, model-round, and provider-failure terminal
states; three-plus-three calls across rounds; unexpected provider secrets; and fixed identity after
instruction-like tool output. Malformed calls may fail closed rather than weakening provider
framing.

## Stage 2.1 verification

- ordinary provider-free suite: 248 passed, 39 explicitly gated PostgreSQL tests skipped;
- original V1 API regression subset: 19 passed;
- V2 regression subset: 138 passed;
- all V3 Stage 0–2.1 tests: 68 passed;
- Ruff lint and format checks: passed; and
- Alembic head remains `0001_v2_knowledge` with no V3 migration.

No public assistant route, prepare capability, write path, persistent state, proposal, confirmation,
LangGraph, HITL, or V4 execution behavior was added.

## Stage 3 authenticated assistant API

`POST /api/v1/assistant/query` is a thin authenticated adapter over `AgentService.run()`. Its strict
request accepts only one trimmed 1–4,000-character `message`; identity, applicability, tools,
arguments, model, prompt, budgets, and history cannot be client-controlled.

The explicit public response contains only `status`, `answer`, trusted V2 citations, and a safe
message. Internal `completed` maps to public `completed`; `unable_to_complete` and
`tool_budget_exhausted` collapse to public `unable_to_complete` without budget/round terminology.
Provider unavailable/rate-limited states map to distinct safe HTTP 503 error envelopes. Tool counts,
model rounds, call IDs, transcripts, employee IDs, UUIDs, vectors, and hidden reasoning are never
mapped.

Public citations come only from the sealed `AgentRunResult.citations`; answer prose is never parsed
for citation metadata. The 24-citation cap remains enforced.

V3 dependencies are created lazily only when the assistant route is called. Application startup,
OpenAPI generation, `/health`, `/chat`, `/me/*`, and `/knowledge/query` do not connect to Gemini or
PostgreSQL merely because the route is registered. Tools remain in-process service adapters and are
not exposed as HTTP routes.

Provider-mocked FastAPI integration tests cover real profile/ticket dispatch, knowledge plus leave
balances with trusted citations, and non-revealing cross-user ticket failure.

### Live HTTP smoke

One bounded authenticated live request reached the public endpoint. The provider returned the
reviewed safe HTTP 503 envelope: `assistant_model_unavailable`, a temporary public message, and a
request ID. No retry or business mutation occurred.

Stage 3 remains read-only and single-turn. No prepare capability, conversation persistence,
streaming, write path, proposal, confirmation, LangGraph, HITL, or V4 execution behavior exists.

## Stage 3 verification

- ordinary provider-free suite: 279 passed, 39 explicitly gated PostgreSQL tests skipped;
- original V1 API regression subset: 19 passed;
- V2 regression subset: 138 passed;
- all internal V3 tests: 68 passed;
- assistant API and provider-mocked real-service integration tests: 31 passed;
- Ruff lint and format checks: passed; and
- Alembic head remains `0001_v2_knowledge` with no V3 migration.

No prepare capability, write path, persistent state, proposal, confirmation, LangGraph, HITL, or V4
execution behavior was added.

## Stage 4 deterministic annual-leave preparation

The immutable registry now adds exactly one `PREPARE` tool:

```text
prepare_leave_request(leave_type="annual", start_date, end_date, reason?)
```

Arguments require strict ISO dates, an inclusive range of at most 31 calendar days, annual leave
only, and an optional nonempty 500-character reason. Employee ID, schedule, hours, balance,
jurisdiction, approval, submission, and execution fields are forbidden.

`LeavePreparationService` reads the authenticated employee's profile and annual balance through the
existing employee service. It counts only configured scheduled weekdays and uses Decimal arithmetic:

```text
requested_hours = scheduled_work_days × trusted hours_per_day
projected_balance = trusted balance - requested_hours
```

The immutable draft reports `ready`, `insufficient_balance`, or `no_scheduled_workdays`, plus
current/projected hours, the optional reason, and `non_executing=true`. Because no authoritative
Victorian holiday calendar exists, drafts with scheduled days set
`public_holiday_check_required=true`; no holiday is guessed.

The successful draft is typed `ToolResult` data and remains untrusted provider-visible content.
`AgentRunResult` separately retains the latest successful deterministic draft; a failed later
prepare does not erase it. Public `prepared_action` is explicitly mapped from that draft, never
parsed from model prose. The action type is `leave_request`, contains no IDs/tokens/approval state,
and remains structurally non-executing.

The trusted Melbourne date is included in the agent system instruction by application-controlled
clock input so conversational dates can become explicit ISO tool arguments. `"next <weekday>"` is
the first occurrence of that weekday strictly after that trusted date. Local validation and
calculation remain authoritative. Prepared drafts remain non-executing and expose the explicit ISO
date.

A separate “yes, submit it” request has no prior draft memory and no execution tool, so it cannot
submit anything. No balance/profile/repository state changes during preparation or repeated calls.

### Live preparation smoke

One bounded authenticated request reached the reviewed safe HTTP 503
`assistant_model_unavailable` path. No retry, draft claim, persistence, or mutation occurred.

Stage 4 adds no ticket preparation, submission, approval, conversation memory, persisted proposal,
confirmation endpoint, LangGraph, HITL, or V4 execution path.

## Stage 5A agent evaluation foundation

V3 evaluation extends `enterprise-ai-eval` with `--mode agent`. It runs the real bounded
`AgentService` and real dispatcher/service graph while an evaluator-only wrapper records safe tool
attempt observations. The production `AgentRunResult` and public assistant response remain
transcript-free.

Both datasets were created before development execution:

- development: 16 cases;
- frozen holdout: 8 cases;
- trusted date: `2026-08-26`; and
- evaluator schema at Stage 5A introduction: `v3-agent-eval-1`; current new reports:
  `v3-agent-eval-2`.

Resume compatibility includes the dataset, model, outer timeout, max attempts, fixed date,
tool/model bounds, tool registry, demo fixture version, knowledge settings, database corpus
identity, and evaluator schema. Completed cases carry forward; provider-blocked/error cases may be
retried without duplicate result rows. Under `v3-agent-eval-2`, a provider block does not stop later
independent cases in the same invocation.

Metrics are mechanical. They grade tool selection, identity/business-state invariants, structured
citations, structured prepared actions, bounded execution, narrow false-execution phrases, and
forbidden prompt-injection calls. No second LLM or generic answer-quality judge is used.

Stage 5A's CLI rejected `--mode agent --split holdout`. Stage 5B keeps that accidental rejection
and adds `--authorize-holdout` for one frozen campaign. Report schema remains `v3-agent-eval-2`.
The holdout has not been executed or used for tuning. Detailed methodology and frozen fingerprints
are in `docs/v3-agent-evaluation.md`.

## Gemini GenerateContent continuation correction

A one-shot live profile-tool diagnostic isolated the second provider round as
`ClientError` / HTTP 400 / `INVALID_ARGUMENT`. The broad internal
`provider_unavailable` result had hidden that non-availability API category.

The GenerateContent continuation now places native `FunctionResponse` parts in
`Content(role="user")`. History remains:

1. original user content;
2. the exact original model candidate `Content`, including function-call IDs, part order, and
   provider thought signatures; and
3. user-role content containing the corresponding typed function responses.

Function-call IDs and canonical names are unchanged. Parallel calls retain one original model
content object and ordered response parts. The narrow direct `FunctionResponse` construction remains
because `google-genai==2.19.0`'s helper does not accept the required function-call ID.

This patch does not broaden the provider error taxonomy. It changes no prompt, tool contract,
budget, model, evaluation dataset, checkpoint, or holdout artifact.

## Isolated outer-agent timeout

The V3 outer provider now has a separate `AGENT_TIMEOUT_SECONDS` setting with a bounded default of
60 seconds per outer model round / provider attempt. `GeminiAgentClient` converts it to
`HttpOptions(timeout=60_000)`. This is not a total Agent-turn deadline. The existing
`GEMINI_TIMEOUT_SECONDS=30` remains unchanged for V1 generation, V2 grounded generation, and V2
embeddings.

The outer provider also has an isolated `AGENT_MAX_ATTEMPTS` setting with a default of one total
SDK attempt per model round. V1 generation, V2 grounded generation, and V2 embeddings retain
`GEMINI_MAX_ATTEMPTS=2`. Retryable statuses, backoff, model, prompt, tools, timeout, and loop budgets
are unchanged. This prevents one interactive 60-second agent round from expanding to roughly two
minutes during provider tail latency.

This is a reliability configuration correction, not semantic/model tuning. The inherited shared
30-second deadline produced real outer-agent `499 CANCELLED` and `504 DEADLINE_EXCEEDED` failures
before Case 6 could dispatch a tool.

Agent evaluation compatibility now records the effective outer-agent timeout. Historical reports
that predate reliability isolation resolve to 30 seconds and two attempts. New reports record 60
seconds and one attempt, so resume compatibility rejects either mismatch. The
corrected-continuation 5/16 development checkpoint remains preserved as historical 30-second,
two-attempt evidence and must not be resumed into a final baseline.

Safe request-ID observability remains outside this retry-policy patch.

## Provider schema hygiene

`prepare_leave_request.leave_type` still validates as the single literal `annual`. The
provider-visible JSON Schema now emits `enum: ["annual"]` instead of `const: "annual"` so
google-genai function declarations stay portable. Local validation, the other four tool
declarations, and all application semantics are unchanged.

## Safe internal provider failure taxonomy

Outer-agent provider failures now carry a bounded internal diagnostic:

- `kind`
- `exception_class`
- optional `http_status_code`
- optional sanitized `symbolic_status`

HTTP 400 is `invalid_request`, 401/403 are `authentication_or_permission`, 408/504 and HTTPX
timeouts are `timeout`, 429 is `rate_limited`, 499/`CANCELLED` is `cancelled`, 500/502/503 are
`provider_unavailable`, HTTPX transport failures are `transport_error`, and other supported API
errors are `unknown_provider_error`. Exception messages, HTTP bodies, headers, request IDs,
prompts, and tool payloads are never stored.

High-level `AgentRunStatus` and the public assistant contract are unchanged: rate limits remain
`provider_rate_limited`, and all other provider failures remain the existing safe unavailable path.
Evaluation reports may persist the optional diagnostic on provider-blocked cases. Historical
reports without the field remain readable and resume-compatible. Timeout/attempt mismatches remain
incompatible.

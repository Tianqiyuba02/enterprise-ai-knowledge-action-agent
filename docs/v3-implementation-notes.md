# V3 Implementation Notes

## Current stage

Product Milestone V3 — Agent + Tools has completed Stage 1 deterministic read-tool dispatch on
`feature/v3-agent-tools`, based on released V2 develop commit
`0121bbab58b7c0e7b47b09f8c3cbaf029abd6182`.

No agent loop, public assistant endpoint, preparation handler, persistence, or execution path
exists yet.

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

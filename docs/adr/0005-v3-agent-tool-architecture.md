# ADR 0005: Use a bounded provider-native tool loop for V3

- Status: Accepted
- Date: 2026-08-25
- Milestone: V3 Stage 0

## Context

V3 adds one authenticated employee-assistant orchestration surface over the released V1/V2
services. It must combine knowledge and current-employee reads and may later prepare inspectable
drafts, but it must not execute business writes. Stateful confirmation and deterministic execution
belong to V4.

An isolated live check with `gemini-3.6-flash` verified provider-native function declarations:

- one ticket declaration was selected with structured `ticket_id=TKT-1001`;
- an unrelated arithmetic prompt produced no function call;
- profile and ticket declarations were distinguished correctly when both were available; and
- automatic function execution was disabled.

One intermediate unrelated-prompt request reached provider `DEADLINE_EXCEEDED`; a single bounded
follow-up completed the remaining checks. No declared function was dispatched.

## Decision

V3 uses `gemini-3.6-flash` through the isolated `AGENT_MODEL` setting and a plain bounded
provider-native tool loop. LangGraph is not introduced in V3.

The initial runtime allowlist is read-only:

| Tool | Model-controlled arguments | Trusted context |
|---|---|---|
| `knowledge_query` | `question` | employee applicability |
| `get_my_profile` | none | employee identity |
| `get_my_leave_balances` | none | employee identity |
| `get_my_ticket` | `ticket_id` | employee identity and ownership |

The model never supplies `employee_id`, jurisdiction, audience, authorization state, SQL, URLs,
HTTP requests, filesystem paths, or commands. Tools adapt existing application services; they do
not duplicate V1/V2 business logic or call local FastAPI routes.

The initial per-turn budget is five tool calls. This allows a combined knowledge/profile/balance/
ticket read plus one correction while placing a clear cost and autonomy ceiling. Budget exhaustion
returns a controlled result; there is no recursion, background activity, or agent self-spawning.

Tool arguments are validated before dispatch. Identity, ownership, applicability, and read/prepare
scope are enforced deterministically. Tool outputs are delimited untrusted data; text inside a
result cannot become a system instruction.

Safe internal tool failures use narrow codes such as `invalid_arguments`, `not_found`,
`unavailable`, `timeout`, and `budget_exhausted`. Cross-user and absent tickets remain the same
non-revealing `not_found` outcome. Raw provider/SQL exceptions, stack traces, credentials, and
authorization details never enter the model transcript or public response.

The proposed additive public endpoint is:

```text
POST /api/v1/assistant/query
```

Product milestone number does not change API versioning. Existing `/api/v1/chat` and
`/api/v1/knowledge/query` remain unchanged.

The future strict response should contain a semantic status, final answer, trusted V2 citations,
and optionally a typed non-executing prepared draft or missing-input description. It must not expose
raw tool calls/arguments, provider function IDs, employee/internal UUIDs, vectors, hidden reasoning,
or confidence probabilities.

## Prepare versus execute

V3 prepare may calculate/summarize trusted information, construct a typed leave or ticket draft,
show exactly what would later be submitted, and identify missing fields. Preparation changes no
business state and initially remains non-persistent.

V3 must not submit leave, change balances, create/update tickets, update employees, approve
anything, or treat chat text such as “yes” as confirmation. Execution operations are absent from the
tool registry.

V4 introduces the first execution path: deterministic workflow state, out-of-band payload-bound
confirmation, expiry, idempotency, audit, and Human-in-the-Loop orchestration. That concrete
stateful problem is the justification for LangGraph in V4 rather than V3.

## Consequences

- V3 remains one modular-monolith agent adapter over existing services.
- Provider tool selection is model-controlled; authorization and dispatch are not.
- Read and future prepare capabilities are structurally separated from execution.
- No LangChain, LangGraph, generic tools, business writes, or public agent endpoint is implemented
  in Stage 0.

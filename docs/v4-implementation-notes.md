# V4 Implementation Notes

## Current stage

V4 Stage 2 is a durable LangGraph orchestration foundation. It does **not** implement
confirmation, HITL execution, a workflow worker, or any business mutation.

Project version remains `0.4.0`. Sealed V3 `v0.4.0` is unchanged.

Architecture authority remains [`docs/v4-architecture-freeze-1.0.md`](v4-architecture-freeze-1.0.md).

## What exists

- Stage 1 persistence: owner-scoped `action_workflows` / `action_revisions` and related V4
  tables, plus the trusted VIC holiday calendar seed.
- Stage 2A: pinned LangGraph checkpointing. Alembic `0003_v4_langgraph_checkpoints` owns the
  exact `PostgresSaver` 3.1.2 schema in `public`.
- Stage 2B: a small interrupt/resume graph that stops at `confirmed_barrier`.

## LangGraph role

LangGraph is used only for durable **action-workflow orchestration**.

- It is not the V3 Gemini reasoning/tool loop.
- `AgentService` is not rewritten into LangGraph.
- The graph does not call Gemini or any provider.
- Checkpoint state is not business or security authority.

`PostgresSaver` is durable. Application and worker paths must not call runtime schema setup.
The shipped application relies entirely on Alembic 0003.

Pinned versions:

- `langgraph==1.2.11`
- `langgraph-checkpoint-postgres==3.1.2`

Checkpoint tables live in `public` because PostgresSaver 3.1.2 has no supported schema argument
and emits unqualified SQL. A dedicated schema would require unsupported monkeypatching.

Serializer security: `LANGGRAPH_STRICT_MSGPACK=true` plus
`JsonPlusSerializer(allowed_msgpack_modules=None, pickle_fallback=False)`. Graph state is
JSON-safe primitives only.

## Resume is not confirmation

`Command(resume=...)` is a wake signal only. The resume payload must never set confirmed,
authorized, execute, or workflow state. Empty or omitted payloads are normalized to a
JSON-safe wake marker because LangGraph 1.2.11 does not treat `Command(resume={})` as a
resume. The marker is ignored for authorization.

After wake, the graph reloads the owner-scoped action/revision from PostgreSQL. PostgreSQL wins
any split-brain with the checkpoint observation.

The graph currently stops at `confirmed_barrier` when PostgreSQL state is `CONFIRMED`. It does
not enter `EXECUTING`, reserve an execution ledger row, or insert `leave_requests`.

Until Stage 3 exists, tests may write `CONFIRMED` through a narrow repository helper. There is
no confirmation HTTP API, no token generation, no challenge consumption, and no outbox worker.

## Graph topology

```
START
  → load_authoritative_revision
  → await_confirmation          # interrupt; no database mutation
  → reload_after_wake
  → route from PostgreSQL
       CONFIRMED → confirmed_barrier → END
       AWAITING_CONFIRMATION → await_confirmation
       CANCELLED / EXPIRED / STALE / other → terminal_barrier → END
```

`configurable.thread_id` is always the persisted `action_workflows.langgraph_thread_id`.
Caller-supplied thread ids are not workflow authority.

## Not implemented

- confirmation HTTP endpoints
- confirmation token generation or challenge consumption
- workflow_outbox worker
- business execution, leases, fencing, or reconciliation
- AgentService `action_id` responses
- LLM execution tools
- HITL execution

# Interview Notes

Public-safe prompts for explaining the project. Use the repository and test evidence; do not turn these into claims about production deployment.

## Probabilistic versus deterministic boundary

The model interprets language, retrieves governed evidence, and prepares structured drafts. It cannot authorize or execute. Server code owns identity, draft normalization, challenge issuance, state transitions, revalidation, locking, mutation, and audit evidence.

## Authority-aware RAG

Applicability filtering occurs in SQL before vector ranking. The server derives jurisdiction and audience from trusted employee context. Retrieved text is untrusted evidence, and citations are reconstructed from stored metadata, so relevance cannot create authority.

## Trusted identity

The public browser uses two HttpOnly cookies. `enterprise-portal-persona` is the allow-listed persona/session selector; the Next.js BFF maps it to one of two fixed synthetic demo credentials on the server. `northstar-demo-visitor` carries an HMAC-signed visitor identity used for quota accounting. FastAPI resolves the employee. The browser cannot submit arbitrary `employee_id` authority and never receives the backend demo-session values.

## Revision-bound human authorization

PREPARE creates a persisted authoritative draft. Review reads the draft from the action record. A short-lived challenge binds explicit consent to the current revision. Editing an IT draft supersedes the prior revision and invalidates any earlier approval context. Chat “yes” has no authority.

## PostgreSQL atomicity

For both domains, the business mutation, final action state, and audit event commit in one transaction. “Exactly once” is intentionally scoped: exactly one business mutation inside that PostgreSQL boundary, not universal distributed exactly-once delivery.

## Concurrency

Workers claim and lock the current revision. Annual Leave also takes an employee-level PostgreSQL advisory transaction lock so concurrent leave requests cannot both pass a stale balance/overlap check. IT Support uses revision ownership and a unique `source_action_id` link.

## Lost commit acknowledgement

If the client does not observe the commit acknowledgement, recovery reads the authoritative business result linked to the action. It does not create a second leave record or ticket.

## Annual Leave versus IT locking

Annual Leave has shared per-employee resources—balance and overlapping dates—so it needs employee serialization. IT ticket creation does not consume the same balance-like resource; the unique source-action constraint and action/revision locks provide the relevant invariant.

## Architecture simplification

The original V4 explored LangGraph-style orchestration. The actual side effect was local to the same PostgreSQL database. A deterministic poller and explicit handlers matched that failure boundary with less machinery while retaining authorization, concurrency, idempotency, auditability, and recovery behavior.

## Public-demo safety engineering

The public surface is a Next.js BFF. FastAPI and the worker are private. Server-only keys never enter browser bundles. The demo uses synthetic data, conservative quotas, periodic reset, readiness projections, and a worker heartbeat. It is deliberately single-worker and does not claim HA or production authentication.

## Useful proof points

- 2 explicit domains: HR Annual Leave and IT Support Ticket.
- 13 governed synthetic documents / 47 chunks in the reviewed demo baseline.
- Full deterministic browser E2E does not call Gemini.
- PostgreSQL integration covers migrations, authorization ownership, expiry, concurrency, and action results.
- V4 development provider evidence: 11/11 observed passes across 11 of 15 applicable cases; correctly reported as partial/provider-limited, with no V4 holdout.

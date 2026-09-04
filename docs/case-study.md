# Engineering Case Study

## 1. Problem

Enterprise assistants are useful when they can explain policy and prepare work, but a conversational model should not become the authority for identity, authorization, or business mutation. This prototype asks a narrower question: how can probabilistic AI help an employee without moving consequential controls into the prompt?

## 2. Why chatbot-only was insufficient

A chat response is transient and ambiguous. “Yes, submit it” may refer to prose, dates, or a prior draft. A chatbot-only flow also makes it easy to reconstruct payloads in the browser or let model output carry fields it does not own. The product therefore treats a prepared action as a durable business object and moves consent to an independent Review surface.

## 3. Knowledge plus action architecture

The application separates three responsibilities:

- the assistant reads governed information and prepares proposals;
- PostgreSQL stores the authoritative draft and workflow state;
- deterministic domain handlers perform confirmed mutations.

The model has provider-native READ/PREPARE tools only. There is no browser or model-facing execute tool.

## 4. Authority-aware RAG

Trusted employee context is resolved on the server. SQL filters approved and currently effective document revisions by jurisdiction and audience before vector ranking. Retrieved passages are still treated as untrusted text. The answer is grounded against those passages, while citations are built by the server from stored document, version, and section metadata.

That distinction matters: semantic relevance does not grant authority.

## 5. Persisted draft and human authority

PREPARE persists a normalized draft. The Review page reads that exact object; it does not create a lookalike authorization payload. A short-lived confirmation challenge binds the user’s explicit action to the current revision. Chat text never confirms. After confirmation, a private worker owns execution.

IT Support demonstrates immutable editable revisions: saving an edit supersedes the old revision and requires a fresh challenge. Annual Leave deliberately retains the sealed single-draft behavior proven earlier.

## 6. The failure-boundary lesson

The most important design rule became:

> Infrastructure is earned by the failure boundary.

The Annual Leave side effect and the action state live in the same PostgreSQL database. That means a row lock, an employee advisory transaction lock, constraints, and one atomic commit can provide the required safety without a distributed workflow engine. Lost commit acknowledgements are recovered by reading the authoritative result linked to the action rather than replaying a second mutation.

## 7. Why LangGraph was removed

The original V4 design explored distributed-grade orchestration ideas, including LangGraph checkpoints. LangGraph was not inherently unsuitable. It was unjustified for the observed failure boundary: the single business side effect was a mutation in the same PostgreSQL transaction as workflow state and audit evidence.

The implementation was simplified to an explicit PostgreSQL poller and domain service while retaining revision-bound authorization, concurrency protection, idempotency invariants, failure normalization, and application-level audit evidence. The result has fewer moving parts and a failure model that matches the system actually being demonstrated.

## 8. Multi-domain generalization

The second action, IT Support Ticket creation, tests whether the trust model survives another domain. It shares action identity, ownership, expiry, confirmation, worker claiming, and audit controls. It does not force HR and IT rules into a generic workflow DSL.

The business differences remain visible:

- Annual Leave recalculates calendar coverage and balance, then serializes per employee.
- IT Support validates four explicit ticket fields, supports immutable revisions, and uses a unique source-action link.

The lesson is to generalize the invariants, not the business logic.

## 9. Public deployment failure boundaries

The public demo adds a Next.js BFF, private service networking, server-only credentials, signed visitor accounting, quotas, a reset window, readiness checks, and an honest worker heartbeat. Only the portal is public. A scheduled reset restores synthetic mutable state without replacing governed knowledge.

Provider timeouts are bounded and represented in the deployment source of truth. Readiness distinguishes database, migration, corpus, maintenance, and worker health. The demo remains one-worker and makes no high-availability claim.

## 10. Testing and evaluation

Deterministic unit and API tests run without provider access. PostgreSQL integration tests exercise migrations, owner isolation, concurrency, expiry, execution, and lost-ACK recovery. Browser E2E uses a synthetic local backend and covers desktop/mobile navigation, bounded Markdown, citation state preservation, recovery pages, Review copy/order, Annual Leave authorization controls, IT revision editing, and identity isolation.

Historical provider evaluation is reported without filling gaps. V4 had 15 applicable development cases, 11 observed semantic cases, and 11/11 observed passes. It was not declared a full Development PASS, and a V4 holdout was not created.

## 11. Tradeoffs and limitations

This is a publicly deployed portfolio prototype with synthetic identities, synthetic business systems, a small governed corpus, conservative public quotas, one worker, and no production OIDC/SSO/RBAC. It does not connect to an HRIS or help desk. Annual Leave assumptions are Victoria-focused and fail closed outside a finite trusted calendar horizon.

The application-level audit trail supports explanation and debugging; it is not a compliance certification or tamper-proof ledger. Provider availability still affects knowledge answers and action preparation, while deterministic reads, review state, and mutation controls remain outside the provider.

## 12. What was learned

- Treat identity, applicability, citations, authorization, and mutation as separate authorities.
- Persist the proposal before asking for consent.
- Bind consent to an exact revision, not conversational intent.
- Keep execution handlers explicit when business rules differ.
- Model timeout, quota, reset, and worker health as product states.
- Preserve negative evidence: an incomplete evaluation is more useful than an inflated claim.

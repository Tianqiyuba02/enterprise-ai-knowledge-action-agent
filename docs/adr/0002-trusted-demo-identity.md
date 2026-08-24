# ADR 0002: Resolve V1 demo identity from trusted server context

- Status: Accepted
- Date: 2026-08-24
- Milestone: V1

## Context

V1 needs authenticated-employee semantics and ownership tests without prematurely implementing
OAuth, OIDC, enterprise SSO, or production RBAC. Accepting `employee_id` in request bodies or asking
the LLM to choose identity would create an invalid trust boundary.

## Decision

V1 accepts a fixed opaque value in the `X-Demo-Session` header. A FastAPI dependency maps that value
server-side to an immutable `AuthenticatedEmployeeContext`. `/me/*` routes accept no employee ID;
services receive the trusted context, and the repository requires both `ticket_id` and the trusted
`employee_id` for ticket lookup.

Missing and invalid sessions share one safe 401 response. A ticket owned by another employee and a
nonexistent ticket share one safe 404 response, so record existence is not disclosed.

## Consequences

- Identity and ownership are deterministic code paths independent of prompts and model output.
- The documented demo tokens identify fictitious seeded records only; they are not production
  credentials or a substitute for real authentication.
- Future OIDC can replace the FastAPI identity dependency without changing service method contracts.
- No generic authorization framework or additional user roles are introduced in V1.

# Architecture and Trust Boundaries

This document describes the v1.0 release-candidate architecture. It is a production-minded portfolio prototype using synthetic business systems, not a production deployment blueprint.

## 1. System and trust boundary

```mermaid
flowchart LR
    Browser["Browser\npublic, untrusted"] -->|same-origin HTTPS| Portal["Next.js portal + BFF\npublic service"]

    subgraph Private["Private service boundary"]
      API["FastAPI application\nserver-resolved identity"]
      Worker["Deterministic action worker\nprivate process"]
      Reset["Bootstrap / reset path\nprivate operations"]
    end

    subgraph Authority["Trusted data boundary"]
      DB[("PostgreSQL + pgvector\ngoverned knowledge, actions, business data")]
    end

    Portal -->|server-only demo credential + internal key| API
    API --> DB
    Worker --> DB
    Reset --> DB
    API -->|outbound provider request only| Gemini["Gemini API\nexternal provider"]

    classDef public fill:#efffc0,stroke:#153229,color:#153229;
    classDef private fill:#eaf3f8,stroke:#365e73,color:#17241f;
    classDef trusted fill:#e8f4e7,stroke:#326238,color:#17241f;
    class Browser,Portal public;
    class API,Worker,Reset private;
    class DB trusted;
```

Only the Next.js portal is public in the Render topology. The browser never receives backend demo-session values or the internal portal key. An HttpOnly persona cookie selects one of two fixed synthetic identities; FastAPI remains the authority that resolves the employee.

## 2. Governed knowledge flow

```mermaid
flowchart LR
    Context["Trusted employee context\nserver resolved"] --> Filter["Applicability filter\napproved + effective + jurisdiction + audience"]
    Filter --> Rank["pgvector cosine ranking"]
    Rank --> Evidence["Retrieved evidence\ntreated as untrusted content"]
    Evidence --> Answer["Grounded answer\nbounded provider call"]
    Evidence --> Citation["Server-built citation\ndocument + version + section"]
    Answer --> Portal["Assistant response"]
    Citation --> Portal
    Citation --> Library["Policy Library\napplicable revision destination"]
```

Filtering happens before ranking. Retrieved text never supplies identity or citation authority, and the provider cannot invent the citation object returned to the browser. The current governed demo baseline contains 13 synthetic documents and 47 chunks.

## 3. Action authorization and execution

```mermaid
flowchart LR
    Assistant["Assistant\nREAD / PREPARE only"] --> Draft["Persisted authoritative draft"]
    Draft --> Review["Independent Review surface"]
    Review --> Challenge["Revision-bound, short-lived challenge"]
    Challenge --> Authorize["Explicit human authorization"]
    Authorize --> Confirmed["CONFIRMED"]
    Confirmed --> Worker["Private deterministic worker"]
    Worker --> Dispatch{Explicit domain dispatch}
    Dispatch --> Leave["Annual Leave handler\nsealed single draft"]
    Dispatch --> IT["IT Support handler\neditable immutable revisions"]
    Leave --> Commit["One PostgreSQL commit"]
    IT --> Commit
    Commit --> Result["Business result + final action state + audit evidence"]
```

Typing “yes” in chat cannot issue a challenge, confirm an action, or execute a mutation. The Review page obtains the persisted draft, and confirmation is bound to that action revision. The worker locks and revalidates before applying exactly one domain mutation inside the same PostgreSQL transaction as the final state and audit evidence.

Annual Leave and IT Support share control-plane invariants, not generic business logic. Annual Leave uses an employee advisory transaction lock to serialize leave mutations. IT Support relies on revision locking plus a unique `source_action_id` for exactly-one ticket creation. “Exactly once” here means exactly one business mutation inside this PostgreSQL transaction boundary; it is not a universal distributed-systems guarantee.

## Deployment shape

The production demo runs as four Render services backed by PostgreSQL 17 with pgvector:

- public Next.js portal/BFF;
- private FastAPI application;
- private action worker;
- private scheduled reset job.

See [Deployment](deployment.md) for reproducible configuration, readiness, reset, and rollback steps.

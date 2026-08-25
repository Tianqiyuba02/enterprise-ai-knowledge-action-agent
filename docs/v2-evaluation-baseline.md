# V2 Stage 5A Evaluation Baseline

- Date: 2026-08-25
- Scope: frozen Product Milestone V2 RAG baseline
- Corpus: 12 synthetic documents / 42 chunks
- Development cases: 20
- Holdout cases: 8, created but not executed

> **No tuning decisions were made in Stage 5A.**

## Frozen configuration

| Setting | Measured configuration |
|---|---|
| Embedding | `gemini-embedding-2`, 768 dimensions |
| Retrieval | Exact pgvector cosine distance |
| Top-k | 6 |
| Similarity threshold | None |
| Chunking | Current Stage 2 heading-aware, target 400 lexical units, overlap 50 |
| Grounded answer | Current Stage 4 prompt and strict response behavior |
| Trusted date | 2026-08-25, Melbourne semantics |

## Measured: development retrieval

These are **mechanical metrics** from the version-controlled development set.

| Metric | Result |
|---|---:|
| Cases completed | 20 / 20 |
| Provider-blocked cases | 0 |
| Mean required-document recall@6 | 1.000 |
| Mean reciprocal rank | 1.000 |
| Forbidden-document case hit rate | 0.000 |
| Authority/applicability violation rate | 0.000 |
| Mean returned chunks | 6.0 |
| Mean distinct documents in top-6 | 2.9 |
| Tiny-chunk rate (≤5 lexical units) | 0.350 |

All 14 cases with a required document retrieved every required identity in the top six, with the
first required identity at rank 1. The six cases without accessible supporting evidence have no
fabricated relevant-document label and therefore do not contribute to recall/MRR.

### Per-case retrieval detail

| Case | Required recall | First rank | Forbidden hits | Authority violations | Tiny chunks | Top-ranked source |
|---|---:|---:|---:|---:|---:|---|
| `dev_annual_entitlement` | 1.0 | 1 | 0 | 0 | 2/6 | `POL-HR-001` v2.0 / `entitlement` |
| `dev_annual_planning` | 1.0 | 1 | 0 | 0 | 3/6 | `POL-HR-001` v2.0 / `planning-leave` |
| `dev_annual_carryover` | 1.0 | 1 | 0 | 0 | 2/6 | `POL-HR-001` v2.0 / `carry-over` |
| `dev_personal_leave_purpose` | 1.0 | 1 | 0 | 0 | 2/6 | `POL-HR-002` v1.0 / `purpose` |
| `dev_personal_leave_evidence` | 1.0 | 1 | 0 | 0 | 2/6 | `POL-HR-002` v1.0 / `evidence` |
| `dev_vic_hybrid_days` | 1.0 | 1 | 0 | 0 | 2/6 | `POL-WRK-001` v3.0 / `standard-arrangement` |
| `dev_vic_remote_location` | 1.0 | 1 | 0 | 0 | 2/6 | `POL-WRK-001` v3.0 / `changes-to-location` |
| `dev_global_personal_notification` | 1.0 | 1 | 0 | 0 | 2/6 | `POL-HR-002` v1.0 / `personal-leave-policy` |
| `dev_nsw_pilot_trap` | N/A | N/A | 0 | 0 | 1/6 | `POL-WRK-001` v3.0 / `standard-arrangement` |
| `dev_manager_travel_trap` | N/A | N/A | 0 | 0 | 3/6 | `POL-HR-001` v2.0 / `approval-and-balances` |
| `dev_draft_pets_trap` | N/A | N/A | 0 | 0 | 2/6 | `POL-WRK-001` v3.0 / `standard-arrangement` |
| `dev_future_expense_trap` | N/A | N/A | 0 | 0 | 3/6 | `POL-WRK-001` v3.0 / `changes-to-location` |
| `dev_expired_taxi_trap` | N/A | N/A | 0 | 0 | 1/6 | `POL-SEC-004` v1.0 / `after-hours-office-access-policy` |
| `dev_unsupported_parking` | N/A | N/A | 0 | 0 | 2/6 | `POL-HR-001` v2.0 / `entitlement` |
| `dev_after_hours_conflict` | 1.0 | 1 | 0 | 0 | 2/6 | `SOP-FAC-007` v1.0 / `escort-requirement` |
| `dev_password_reset` | 1.0 | 1 | 0 | 0 | 3/6 | `SOP-IT-002` v2.0 / `self-service-reset` |
| `dev_prompt_injection_evidence` | 1.0 | 1 | 0 | 0 | 2/6 | `SOP-IT-002` v2.0 / `escalation` |
| `dev_adjacent_leave_types` | 1.0 | 1 | 0 | 0 | 2/6 | `POL-HR-002` v1.0 / `personal-leave-policy` |
| `dev_annual_balance` | 1.0 | 1 | 0 | 0 | 2/6 | `POL-HR-001` v2.0 / `approval-and-balances` |
| `dev_personal_leave_privacy` | 1.0 | 1 | 0 | 0 | 2/6 | `POL-HR-002` v1.0 / `notification` |

The machine-readable source is
`evals/results/v2-stage5a-development-retrieval.json`.

## Not yet measured: development grounded responses

The grounded run made one attempt and was immediately:

```text
blocked_by_provider_rate_limit
```

Measured grounded cases: **0 / 20**. The first case recorded the safe category
`GroundedRateLimitError`; the remaining 19 were not run to avoid repeatedly hammering the
provider. Therefore Stage 5A does **not** report status accuracy, citation metrics, factual checks,
or internal-reference leakage metrics from this run.

The partial machine-readable report is
`evals/results/v2-stage5a-development-grounded.json`.

Earlier Stage 4 deterministic tests and manual/live smokes remain valid evidence, but they are not
misrepresented as this 20-case development baseline.

## Not yet measured: holdout

The eight-case holdout split was validated and intentionally not executed. It is reserved for final
post-tuning validation and was not used to choose chunking, top-k, thresholds, prompts, or ranking.

## Hypotheses: measured evidence, no action

### H1 — Tiny title-only chunks may consume top-k positions

Measured: 42 of 120 returned chunks (35%) had five or fewer lexical units. Every development case
returned at least one tiny chunk, and some returned three. This is observational, not proof that
the chunks are harmful.

### H2 — Top-k 6 may include adjacent unnecessary chunks

Measured: every case returned six chunks with mean document diversity 2.9. Annual-leave cases often
included personal-leave chunks, and unsupported/filtered-trap questions still returned unrelated
eligible material.

### H3 — No-threshold retrieval returns evidence for unsupported questions

Measured: all six cases without an accessible gold document still returned six eligible chunks.
For example, the unsupported parking case ranked annual-leave entitlement first. No refusal claim
is made at retrieval level.

### H4 — Grounded classification may refuse despite weak retrieval

Not measured in the development baseline because grounded generation was provider-rate-limited
before the first case completed. Earlier smoke evidence supports the hypothesis but is not a
substitute for the blocked baseline.

## Baseline weaknesses to review before Stage 5B

- Tiny chunks occupy 35% of measured retrieval slots.
- Adjacent/unrelated eligible chunks fill top-k when no gold evidence exists.
- The grounded 20-case baseline is unavailable due to provider rate limiting.
- No holdout result exists by design.

No chunking, top-k, threshold, prompt, ranking, corpus, or status-semantic change was made in
response to these observations.

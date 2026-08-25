# V2 Final Holdout Validation

- Date: 2026-08-25
- Split: holdout
- Cases: 8
- Corpus: 12 synthetic documents / 42 chunks

> **Evaluation showed no measured development regression requiring RAG tuning. The existing
> configuration was therefore frozen before final holdout validation.**

No product or evaluation label was changed before or after viewing holdout results.

## Frozen configuration

| Setting | Value |
|---|---|
| Embedding | `gemini-embedding-2`, 768 dimensions |
| Grounded generation | `gemini-3.6-flash` |
| Retrieval | Exact pgvector cosine distance |
| Top-k | 6 |
| Similarity threshold | None |
| Chunking | Stage 2 heading-aware, target 400 lexical units, overlap 50 |
| Corpus/prompt/status/citations/authority | Unchanged |

## Retrieval holdout

| Mechanical metric | Result |
|---|---:|
| Cases completed | 8 / 8 |
| Required-document recall@6 | 1.000 |
| Mean reciprocal rank | 1.000 |
| Forbidden-document case hit rate | 0.000 |
| Authority/applicability violation rate | 0.000 |
| Mean returned chunks | 6.0 |
| Mean document diversity | 2.875 |
| Tiny-chunk rate (≤5 lexical units) | 0.2292 |

All required documents appeared first when a required identity existed. No draft, superseded,
future, expired, wrong-jurisdiction, or wrong-audience identity entered the measured results.

## Grounded holdout

| Mechanical metric | Result |
|---|---:|
| Cases completed | 8 / 8 |
| Semantic status accuracy | 0.875 |
| Citation presence invariant rate | 1.000 |
| Required-document citation recall | 0.750 |
| Forbidden-citation case hit rate | 0.000 |
| Conflict distinct-source invariant | 1.000 |
| Public citation metadata validity | 1.000 |
| Internal evidence-reference leakage rate | 0.000 |
| Provider interruptions/resumes | 0 / 0 |

Seven cases matched their expected status. The conflict case cited both approved sources. Every
insufficient-evidence response had no citation, and no forbidden identity was cited.

## Single holdout failure

`holdout_remote_state_boundary` expected `answered` but returned `insufficient_evidence`.

The question asked whether the three-day Sydney pilot applies to a Melbourne employee. Retrieval
correctly excluded the AU-NSW pilot and ranked the approved Victorian policy first. The accessible
Victorian evidence states Melbourne's two-day arrangement but does not describe the Sydney pilot or
explicitly state whether that pilot applies to Melbourne. The grounded answer declined to infer
the missing relationship and recommended human confirmation.

This may indicate a holdout-label sufficiency issue rather than a product failure, but the holdout
is frozen and was not changed. Any future investigation must begin a new development cycle and use
a new unseen validation set.

## Tiny-chunk observation

Eleven of 48 retrieved chunks were tiny (22.9%). Tiny chunks still occupy ranking slots, but the
holdout retained perfect required-document recall/MRR and all citation/authority invariants. This
remains an observation, not a demonstrated quality defect.

## Interpretation

- Retrieval authority and applicability controls generalized cleanly to holdout.
- Grounded citation and safety invariants generalized cleanly.
- One semantic-status expectation failed; no tuning was performed against it.
- Holdout results must not be reused as a tuning target.

Machine-readable reports:

- `evals/results/v2-stage5a-holdout-retrieval.json`
- `evals/results/v2-stage5a-holdout-grounded.json`

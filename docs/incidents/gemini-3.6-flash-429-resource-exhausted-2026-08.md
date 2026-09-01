# Gemini 3.6 Flash intermittent 429 RESOURCE_EXHAUSTED during V4 evaluation

**Status:** LOCAL PROVIDER DIAGNOSIS CLOSED  
**Incident window:** 2026-08-28 through 2026-08-29 (UTC)  
**Model:** `gemini-3.6-flash`  
**API:** Gemini `generate_content` through `google-genai` **2.19.0**  
**Evaluator:** `v4-product-eval-2`  
**Development set:** `v4-product-dev-1`

Evaluation provider configuration (unchanged across these observations):

- thinking `MINIMAL`
- temperature `0`
- automatic function calling disabled
- timeout 60 seconds
- SDK `HttpRetryOptions.attempts=1` (no SDK retry; no application retry loop)

Normalized application category on blocked observations: `rate_limited`.

**`rate_limited` is this project's broad normalized category. It does not establish which provider resource or quota was exhausted.** HTTP 429 plus symbolic `RESOURCE_EXHAUSTED` is all that is proven. Visible RPM / TPM / RPD / spend exhaustion is not proven.

---

## 1. Incident summary

During V4 development evaluation, the configured Gemini path returned intermittent HTTP 429 / `RESOURCE_EXHAUSTED` responses. The same minimal text-only preflight request both completed and later blocked. A later Agent-shaped diagnostic and a minimal control, run once each in that order, both blocked with the same safe status and `retry_delay_ms=31000`. Structured quota metric / limit / location fields were `null` on captured blocked observations.

The Project Controller closed further local live provider diagnosis. Run 2 will not be resumed. Run 3 is not authorized.

---

## 2. Evidence timeline

Chronological committed observations (UTC):

| When (UTC) | Observation | Result | Safe provider fields | Evidence |
|---|---|---|---|---|
| 2026-08-28T13:21:12Z | Development Run 1 closed | 9/16 provider-completed; 7/16 provider-blocked; 9/9 semantic pass among evaluable | blocked cases: HTTP 429 / `RESOURCE_EXHAUSTED` | `evals/results/v4-product-development.json`; archive `evals/results/archive/v4-product-dev-1-eval-1-run-1.json`; evidence `d2092d367504eb6c9e83e0c212015641335ba1e6` |
| 2026-08-28T13:41:24.470841Z | Standalone provider preflight | **BLOCKED** | HTTP 429, `RESOURCE_EXHAUSTED`, `retry_delay_ms=35000`; quota fields `null` | `evals/results/v4-provider-preflight.json`; `56468770c6cad297efa3946fe13aec96c4b71996` |
| 2026-08-29T03:41:59.815839Z | Standalone provider preflight | **COMPLETED** | usage prompt 49 / output 1 / total 50 | `evals/results/v4-provider-preflight-2026-08-29.json`; `cd825881cd0eda81bf3b06bf106697b85435747a` |
| Later 2026-08-29, after that completed standalone and before Stage 6P.1 | Automatic Run-2 launch preflight | **BLOCKED**; development runner did not start | Structured launch-preflight artifact **unavailable** — the then-existing launch path discarded the classified result after a one-line gate error | no persisted launch artifact |
| `e96aabd7a78b08689ebb7f2135aa0fd4f44a6fc4` | Stage 6P evaluator transport | eval-2 identity, circuit breaker, non-scored preflight, subject/transport split | not a live provider observation | transport commit |
| `4c2a40dc00307aeca18012aa76d8fd2165b47c21` | Stage 6P.1 | launch-preflight result now persisted on success and failure; **provider request unchanged** | observability only | `4c2a40dc00307aeca18012aa76d8fd2165b47c21` |
| 2026-08-29T05:49:16.445094Z | Run-2 launch preflight (after 6P.1) | **COMPLETED** | usage prompt 49 / output 1 / total 50 | `evals/results/v4-provider-preflight-launch-20260829T054916445094Z.json` |
| 2026-08-29T05:49:17.421960Z | Development Run 2 | launch passed; A1 and A2 **BLOCKED**; circuit breaker stopped the rest | A1/A2: HTTP 429, `RESOURCE_EXHAUSTED`, `retry_delay_ms=42000`; quota fields `null`; 0 semantic-evaluable; 14 not attempted | `evals/results/v4-product-development-eval-2.json`; `965b77343eca4f52a925d99cc593470c251ea914` |
| Stage 6P.2 | Offline request-shape audit | no live call | no material local split in model, credential resolution, SDK/method, timeout/retry, thinking, temperature, AFC, or Run-1 vs Run-2 outbound Agent request construction | audit in conversation; no new artifact |
| `0f11cbcfaaadbe40cbfa8676c970cbff22ed50e3` | Stage 6P.3 harness | offline-tested pair runner | not a live observation | harness commit |
| 2026-08-29T06:05:27.910203Z–06:05:28.471251Z | Mirrored pair Call 1: Agent-shaped | **BLOCKED** | HTTP 429, `RESOURCE_EXHAUSTED`, `retry_delay_ms=31000`; quota fields `null` | pair artifact below |
| 2026-08-29T06:05:28.485754Z–06:05:28.835750Z | Mirrored pair Call 2: minimal control | **BLOCKED** | HTTP 429, `RESOURCE_EXHAUSTED`, `retry_delay_ms=31000`; quota fields `null` | `evals/results/v4-provider-diagnostic-pair-20260829T060528859584Z.json`; `d76526871b8cdc9f57762c8832489f1de655efc5` |

No retry was performed on any of these diagnostic or Run-2 case attempts (`attempts=1`).

---

## 3. Important success evidence

- The **same** committed minimal provider-preflight request path has both **completed** (2026-08-29T03:41:59Z and the Run-2 launch preflight at 2026-08-29T05:49:16Z) and **blocked** (2026-08-28T13:41:24Z; the pre-6P.1 launch gate; the 6P.3 minimal control).
- Run-1 A1 and A2 **provider-completed** using materially equivalent outbound Agent request construction (usage-capture-only changes afterward; `generate_content` arguments, instruction, and tool declarations unchanged).

Current evidence does **not** support claiming that the Agent request shape is permanently invalid.

---

## 4. Minimal reproduction characteristics

Smallest committed blocked diagnostic observation: Stage 6P.3 **minimal control**.

Local serialized request inventory (not provider token counts):

- approximately **419** local serialized envelope bytes
- user message **33** bytes (`Reply with the single word READY.`)
- instruction **148** bytes
- **0** tools
- **1** content (string, not `Content(role=user)`)
- `max_output_tokens=16`
- text-only

Returned: HTTP 429, `RESOURCE_EXHAUSTED`, `retry_delay_ms=31000`.

Full system instruction and tool schemas are intentionally not copied here.

Agent-shaped pair call (for contrast only): ~4731 local envelope bytes, 5 production V3 tool declarations, Agent system instruction plus trusted date, `Content(role=user)`, `max_output_tokens=1024`. Same 429 / `RESOURCE_EXHAUSTED` / `retry_delay_ms=31000`. No tools were executed.

---

## 5. Provider error details

Across relevant blocked observations:

- HTTP **429**
- symbolic status **`RESOURCE_EXHAUSTED`**
- exception class `ClientError` (SDK)
- safe observed RetryInfo values: **31000 ms**, **35000 ms**, **42000 ms**

Structured fields remained unavailable / `null`:

- `provider_error_code`
- `quota_metric`
- `quota_limit`
- `quota_limit_value`
- `quota_location`

Raw provider error bodies, messages, and headers are not stored.

---

## 6. What has been ruled out locally

Evidence-backed exclusions only:

- No local request/config/client/concurrency difference explaining standalone vs launch preflight (same provider-facing path).
- No different model.
- No different credential-resolution path.
- No different SDK version or `generate_content` method.
- No different timeout / retry configuration.
- No application retry loop.
- No concurrent or background Gemini call from the evaluator CLI for these commands.
- No `AgentService` / workflow involvement in minimal-control and standalone-preflight failures.
- No local code synthesis of HTTP 429, `RESOURCE_EXHAUSTED`, or RetryInfo; those fields were copied from SDK `APIError`.
- No evidence that Run 2 changed outbound Agent request construction relative to the previously completing Run-1 A1/A2 request-affecting path.

**Provider root cause is not proven.**

---

## 7. Current interpretation

### Fact

- Gemini returned intermittent HTTP 429 / `RESOURCE_EXHAUSTED` observations on the same configured project / provider path.
- A minimal text-only control can be blocked.
- The same minimal path has also completed.
- No quota metric / limit / location was supplied in the captured blocked observations.
- The application / evaluator did not generate these HTTP or provider status values.

### Hypothesis (not fact)

Provider-side capacity / admission behavior, or another non-visible provider resource constraint, is plausible.

### Not claimed

- visible RPM exhausted
- visible TPM exhausted
- visible RPD exhausted
- spend limit exhausted
- tool schemas caused 429
- prompt size caused 429
- Gemini backend bug proven

---

## 8. Development evaluation status

**Run 1 — CLOSED — PARTIAL / PROVIDER-LIMITED**

- 16 total
- 9 provider-completed / evaluable
- 7 provider-blocked
- 9/9 semantic pass among evaluable cases

Run 1 is **not** a Development PASS and **not** a holdout.

**Run 2 — CLOSED — STARTED / STOPPED EARLY / PROVIDER-LIMITED**

- 2 attempted
- 2 provider-blocked
- 0 semantic-evaluable
- 14 not attempted due to provider circuit breaker

Run 1 and Run 2 denominators **must not** be combined.

- No Development Closure
- No pre-holdout review
- V4 holdout **does not exist**

---

## 9. Support-ready report

Copy for Google / Gemini support. Attach private project details separately.

### Title

Gemini 3.6 Flash intermittently returns 429 RESOURCE_EXHAUSTED for minimal text-only GenerateContent requests while visible usage is below displayed limits

### Body

We are seeing intermittent HTTP 429 with symbolic status RESOURCE_EXHAUSTED on `gemini-3.6-flash` via `google-genai` 2.19.0 `models.generate_content` (sync). Configuration: thinking MINIMAL, temperature 0, automatic function calling disabled, timeout 60s, SDK attempts=1 (single request, no client retry).

UTC timestamps from committed evidence:

- 2026-08-28T13:41:24.470841Z — minimal preflight BLOCKED (retry_delay_ms=35000)
- 2026-08-29T03:41:59.815839Z — same minimal preflight path COMPLETED (usage 49/1/50)
- 2026-08-29T05:49:16.445094Z — same minimal path COMPLETED again (usage 49/1/50)
- 2026-08-29T05:49:17Z — two Agent evaluation first-round requests BLOCKED (retry_delay_ms=42000)
- 2026-08-29T06:05:27.910203Z — Agent-shaped diagnostic BLOCKED (retry_delay_ms=31000)
- 2026-08-29T06:05:28.485754Z — immediately following minimal text-only control BLOCKED (retry_delay_ms=31000)

The smallest failing control is text-only GenerateContent: about 419 locally serialized bytes, 33-byte user text, 148-byte instruction, 0 tools, 1 content, max_output_tokens=16. No file input, no URL / fileData, no grounding, no function calling on that control. The same minimal path has both succeeded and failed at different times. Observed RetryInfo values include 31s, 35s, and 42s. Structured quota_metric, quota_limit, quota_limit_value, and quota_location were absent/null on blocked captures.

We request provider-side investigation.

1. Which resource is producing RESOURCE_EXHAUSTED?
2. Does the returned RetryInfo correspond to a quota or capacity dimension not exposed in AI Studio?
3. Why are quota_metric / quota_limit / location absent?
4. Is there known capacity or admission degradation affecting gemini-3.6-flash in this window?
5. Can the supplied UTC timestamps and private project number be inspected server-side?

[PROJECT NUMBER — PROVIDE PRIVATELY TO GOOGLE SUPPORT]

[AI STUDIO RATE-LIMIT SCREENSHOT — ATTACH PRIVATELY]

---

## 10. Private data placeholders

Do not put the following in this repository:

- API key
- full credential
- billing identifiers
- project number
- support account information

Use private channels for:

- `[PROJECT NUMBER — PROVIDE PRIVATELY TO GOOGLE SUPPORT]`
- `[AI STUDIO RATE-LIMIT SCREENSHOT — ATTACH PRIVATELY]`

Visible quota numbers are not reconstructed here; they are not verifiable from committed repository evidence.

---

## 11. Artifact and commit references

Verified before writing this document:

| Item | Hash / path |
|---|---|
| Stage 6P transport | `e96aabd7a78b08689ebb7f2135aa0fd4f44a6fc4` |
| 2026-08-28 blocked standalone preflight | `56468770c6cad297efa3946fe13aec96c4b71996` — `evals/results/v4-provider-preflight.json` |
| 2026-08-29 completed standalone preflight | `cd825881cd0eda81bf3b06bf106697b85435747a` — `evals/results/v4-provider-preflight-2026-08-29.json` |
| Stage 6P.1 launch-preflight persist | `4c2a40dc00307aeca18012aa76d8fd2165b47c21` |
| Run-2 evidence | `965b77343eca4f52a925d99cc593470c251ea914` — `evals/results/v4-product-development-eval-2.json` |
| Run-2 successful launch preflight | `evals/results/v4-provider-preflight-launch-20260829T054916445094Z.json` |
| Stage 6P.3 harness | `0f11cbcfaaadbe40cbfa8676c970cbff22ed50e3` |
| Mirrored-pair evidence | `d76526871b8cdc9f57762c8832489f1de655efc5` — `evals/results/v4-provider-diagnostic-pair-20260829T060528859584Z.json` |
| Run-1 archive | `d2092d367504eb6c9e83e0c212015641335ba1e6` — `evals/results/archive/v4-product-dev-1-eval-1-run-1.json` |

---

## 12. Stop policy

No more local live provider diagnostic experiments are authorized at this stage.

- Do not call Gemini for further probes.
- Do not resume Run 2.
- Run 3 remains **NOT AUTHORIZED**.
- Do not define repeated automatic probes.

The next provider-dependent evaluation attempt requires a new Project Controller decision after a meaningful external or time-based recovery signal.

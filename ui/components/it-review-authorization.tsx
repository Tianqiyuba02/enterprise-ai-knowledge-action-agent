"use client";

import {
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  Fingerprint,
  LoaderCircle,
  LockKeyhole,
  PencilLine,
  ShieldCheck,
  X,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { StatusPill } from "@/components/status-pill";
import type {
  ActionResponse,
  ConfirmationChallenge,
  ITActionDetail,
  ITTicketCategory,
  ITTicketUrgency,
} from "@/lib/contracts";
import { formatDateTime, sentenceCase } from "@/lib/format";
import { reviewStatusCopy } from "@/lib/review-copy";

const TERMINAL_STATES = new Set([
  "SUCCEEDED",
  "EXECUTION_FAILED",
  "CANCELLED",
  "EXPIRED",
  "STALE",
]);

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

async function portalRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/portal/${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message ?? "The request could not be completed.");
  return payload as T;
}

export function ITReviewAuthorization({ initialDetail }: { initialDetail: ITActionDetail }) {
  const [detail, setDetail] = useState(initialDetail);
  const [category, setCategory] = useState<ITTicketCategory>(
    initialDetail.authoritative_draft.category,
  );
  const [summary, setSummary] = useState(initialDetail.authoritative_draft.summary);
  const [description, setDescription] = useState(
    initialDetail.authoritative_draft.description,
  );
  const [urgency, setUrgency] = useState<ITTicketUrgency>(
    initialDetail.authoritative_draft.urgency,
  );
  const [challenge, setChallenge] = useState<ConfirmationChallenge | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [pending, setPending] = useState<
    "edit" | "challenge" | "confirm" | "cancel" | null
  >(null);
  const [error, setError] = useState<string | null>(null);
  const [executionSlow, setExecutionSlow] = useState(false);

  useEffect(() => {
    if (detail.state !== "CONFIRMED") return;
    const slowTimer = window.setTimeout(() => setExecutionSlow(true), 12_000);
    const interval = window.setInterval(async () => {
      try {
        const next = await portalRequest<ITActionDetail>(
          `actions/${detail.action_id}/detail`,
        );
        setDetail(next);
        if (TERMINAL_STATES.has(next.state)) {
          window.clearInterval(interval);
          window.clearTimeout(slowTimer);
        }
      } catch {
        // Keep the confirmed state visible; the next poll can recover.
      }
    }, 1200);
    return () => {
      window.clearInterval(interval);
      window.clearTimeout(slowTimer);
    };
  }, [detail.action_id, detail.state]);

  const draft = detail.authoritative_draft;
  const changed =
    category !== draft.category ||
    summary.trim() !== draft.summary ||
    description.trim() !== draft.description ||
    urgency !== draft.urgency;
  const canReview = detail.state === "AWAITING_CONFIRMATION";

  async function saveRevision() {
    if (!changed || !canReview || challenge) return;
    setError(null);
    setPending("edit");
    try {
      await portalRequest<ActionResponse>(`actions/${detail.action_id}/revisions`, {
        method: "POST",
        body: JSON.stringify({
          expected_revision: detail.revision,
          category,
          summary: summary.trim(),
          description: description.trim(),
          urgency,
        }),
      });
      const persisted = await portalRequest<ITActionDetail>(
        `actions/${detail.action_id}/detail`,
      );
      setDetail(persisted);
      setCategory(persisted.authoritative_draft.category);
      setSummary(persisted.authoritative_draft.summary);
      setDescription(persisted.authoritative_draft.description);
      setUrgency(persisted.authoritative_draft.urgency);
      setChallenge(null);
      setReviewed(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save this revision.");
    } finally {
      setPending(null);
    }
  }

  async function beginAuthorization() {
    setError(null);
    setPending("challenge");
    try {
      const issued = await portalRequest<ConfirmationChallenge>(
        `actions/${detail.action_id}/confirmation-challenges`,
        { method: "POST" },
      );
      if (
        issued.revision !== detail.revision ||
        stableJson(issued.action.draft) !== stableJson(detail.authoritative_draft)
      ) {
        throw new Error("The authorization draft did not match this persisted revision.");
      }
      setChallenge(issued);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start authorization.");
    } finally {
      setPending(null);
    }
  }

  async function authorize() {
    if (!challenge || !reviewed) return;
    setError(null);
    setPending("confirm");
    try {
      const confirmed = await portalRequest<ActionResponse>(
        `actions/${detail.action_id}/confirm`,
        {
          method: "POST",
          body: JSON.stringify({
            challenge_id: challenge.challenge_id,
            confirmation_token: challenge.confirmation_token,
          }),
        },
      );
      if (confirmed.state !== "CONFIRMED" && confirmed.state !== "SUCCEEDED") {
        throw new Error("The request was not accepted for execution.");
      }
      setChallenge(null);
      setDetail((current) => ({
        ...current,
        state: confirmed.state,
        confirmation_required: false,
        confirmed_expires_at: confirmed.confirmed_expires_at,
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Authorization failed.");
    } finally {
      setPending(null);
    }
  }

  async function cancel() {
    setError(null);
    setPending("cancel");
    try {
      const cancelled = await portalRequest<ActionResponse>(
        `actions/${detail.action_id}/cancel`,
        { method: "POST" },
      );
      setChallenge(null);
      setDetail((current) => ({
        ...current,
        state: cancelled.state,
        confirmation_required: false,
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Cancellation failed.");
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="review-layout">
      <section className="review-main">
        <div className="review-title-row">
          <div>
            <p className="eyebrow">Independent authorization</p>
            <h1>Review IT support</h1>
            <p>{reviewStatusCopy(detail.state, "it")}</p>
          </div>
          <StatusPill state={detail.state} />
        </div>

        <article className="authoritative-review">
          <div className="authoritative-heading">
            <span><PencilLine aria-hidden="true" size={20} /></span>
            <div><p className="eyebrow">Authoritative draft</p><h2>Issue details</h2></div>
            <span className="authority-badge">Persisted · Revision {detail.revision}</span>
          </div>
          <div className="it-edit-grid">
            <label>
              Category
              <select
                name="category"
                value={category}
                onChange={(event) => setCategory(event.target.value as ITTicketCategory)}
                disabled={!canReview || challenge !== null}
              >
                <option value="access">Access</option>
                <option value="hardware">Hardware</option>
                <option value="software">Software</option>
                <option value="network">Network</option>
              </select>
            </label>
            <label>
              Urgency
              <select
                name="urgency"
                value={urgency}
                onChange={(event) => setUrgency(event.target.value as ITTicketUrgency)}
                disabled={!canReview || challenge !== null}
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </label>
            <label className="it-edit-wide">
              Summary
              <input
                name="summary"
                autoComplete="off"
                value={summary}
                onChange={(event) => setSummary(event.target.value)}
                maxLength={160}
                disabled={!canReview || challenge !== null}
              />
            </label>
            <label className="it-edit-wide">
              Description
              <textarea
                name="description"
                autoComplete="off"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={2000}
                rows={5}
                disabled={!canReview || challenge !== null}
              />
            </label>
          </div>
          {canReview && !challenge ? (
            <button
              className="button button-secondary"
              type="button"
              disabled={!changed || pending !== null || !summary.trim() || !description.trim()}
              onClick={() => void saveRevision()}
            >
              {pending === "edit" ? <LoaderCircle className="spin" size={16} /> : <PencilLine size={16} />}
              Save as new revision
            </button>
          ) : null}
          <div className="draft-proof">
            <Fingerprint aria-hidden="true" size={17} />
            <span>Verified at preparation</span>
            <details>
              <summary>Technical evidence</summary>
              <code>{draft.authority_snapshot_hash.slice(0, 16)}…</code>
            </details>
          </div>
        </article>

        {detail.state === "SUCCEEDED" && detail.result ? (
          <div className="review-success">
            <CheckCircle2 aria-hidden="true" size={25} />
            <div>
              <p className="eyebrow">Ticket created</p>
              <h2>{detail.result.ticket_id}</h2>
              <p>Status: {sentenceCase(detail.result.status)} · Created {formatDateTime(detail.result.created_at)}</p>
            </div>
            <Link className="button button-primary" href={`/requests/${detail.action_id}`}>
              View evidence <ArrowRight aria-hidden="true" size={15} />
            </Link>
          </div>
        ) : detail.state === "CONFIRMED" ? (
          <div className="execution-queued" role="status">
            <LoaderCircle aria-hidden="true" className="spin" size={20} />
            <div><strong>{executionSlow ? "Authorized — processing is taking longer than usual" : "Authorized and queued"}</strong><p>{executionSlow ? "No IT ticket has been created yet. Do not authorize it again; the private worker may be delayed and this page will keep checking safely." : "The internal worker is creating exactly one ticket."}</p></div>
          </div>
        ) : canReview ? (
          <div className="authorization-panel">
            <div className="authorization-heading">
              <LockKeyhole aria-hidden="true" size={19} />
              <div><strong>Explicit authorization required</strong><p>A short-lived challenge binds approval to this exact revision.</p></div>
            </div>
            {!challenge ? (
              <button
                className="button button-primary button-wide"
                type="button"
                disabled={pending !== null || changed}
                onClick={() => void beginAuthorization()}
              >
                {pending === "challenge" ? <LoaderCircle className="spin" size={16} /> : <LockKeyhole size={16} />}
                Begin authorization
              </button>
            ) : (
              <div className="challenge-step">
                <p className="challenge-valid">Challenge valid until {formatDateTime(challenge.expires_at)}</p>
                <label className="review-check">
                  <input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} />
                  <span>I reviewed this exact revision and explicitly authorize creation of this IT ticket.</span>
                </label>
                <p className="submission-consent-copy">By submitting, you authorize this exact request.</p>
                <button
                  className="button button-primary button-wide"
                  type="button"
                  disabled={!reviewed || pending !== null}
                  onClick={() => void authorize()}
                >
                  {pending === "confirm" ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}
                  Create IT ticket
                </button>
              </div>
            )}
            <button className="cancel-link" type="button" disabled={pending !== null} onClick={() => void cancel()}>
              {pending === "cancel" ? <LoaderCircle className="spin" size={14} /> : <X size={14} />}
              Cancel draft
            </button>
          </div>
        ) : (
          <div className="terminal-note"><CircleAlert size={20} /><div><strong>This request can no longer be authorized.</strong><p>{reviewStatusCopy(detail.state, "it")}</p></div></div>
        )}
        {error ? <div className="form-error" role="alert"><CircleAlert size={17} />{error}</div> : null}
      </section>

      <aside className="review-aside">
        <p className="eyebrow">Why this is separate</p>
        <h2>Conversation is not consent.</h2>
        <p>Messages such as “yes, create it” remain non-authoritative. Only this revision-bound control can confirm the action.</p>
        <ol>
          <li data-complete="true"><span>1</span><div><strong>Prepared</strong><p>The assistant proposed four business fields.</p></div></li>
          <li data-complete={detail.revision > 1}><span>2</span><div><strong>Edited</strong><p>Each saved edit creates an immutable revision.</p></div></li>
          <li data-complete={challenge !== null || detail.state !== "AWAITING_CONFIRMATION"}><span>3</span><div><strong>Bound review</strong><p>The challenge matches the current persisted revision.</p></div></li>
          <li data-complete={detail.state === "SUCCEEDED"}><span>4</span><div><strong>Created</strong><p>The internal worker records one ticket.</p></div></li>
        </ol>
        <small>Action ID<br /><code>{detail.action_id}</code></small>
      </aside>
    </div>
  );
}

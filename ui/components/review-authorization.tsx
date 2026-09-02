"use client";

import {
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  Fingerprint,
  LoaderCircle,
  LockKeyhole,
  ShieldCheck,
  X,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { StatusPill } from "@/components/status-pill";
import type {
  ActionDetail,
  ActionResponse,
  ConfirmationChallenge,
} from "@/lib/contracts";
import { formatDate, formatDateTime, formatHours } from "@/lib/format";

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

async function portalPost<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(`/api/portal/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message ?? "The request could not be completed.");
  return payload as T;
}

export function ReviewAuthorization({ initialDetail }: { initialDetail: ActionDetail }) {
  const [detail, setDetail] = useState(initialDetail);
  const [challenge, setChallenge] = useState<ConfirmationChallenge | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [pending, setPending] = useState<"challenge" | "confirm" | "cancel" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (detail.state !== "CONFIRMED") return;
    const interval = window.setInterval(async () => {
      const response = await fetch(`/api/portal/actions/${detail.action_id}/detail`, {
        cache: "no-store",
      });
      if (!response.ok) return;
      const next = (await response.json()) as ActionDetail;
      setDetail(next);
      if (TERMINAL_STATES.has(next.state)) window.clearInterval(interval);
    }, 1200);
    return () => window.clearInterval(interval);
  }, [detail.action_id, detail.state]);

  async function beginAuthorization() {
    setError(null);
    setPending("challenge");
    try {
      const issued = await portalPost<ConfirmationChallenge>(
        `actions/${detail.action_id}/confirmation-challenges`,
      );
      if (
        issued.action.action_id !== detail.action_id ||
        stableJson(issued.action.draft) !== stableJson(detail.authoritative_draft)
      ) {
        throw new Error("The authorization draft did not match the reviewed persisted draft.");
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
      const confirmed = await portalPost<ActionResponse>(`actions/${detail.action_id}/confirm`, {
        challenge_id: challenge.challenge_id,
        confirmation_token: challenge.confirmation_token,
      });
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
      const cancelled = await portalPost<ActionResponse>(`actions/${detail.action_id}/cancel`);
      setChallenge(null);
      setDetail((current) => ({ ...current, state: cancelled.state, confirmation_required: false }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Cancellation failed.");
    } finally {
      setPending(null);
    }
  }

  const draft = detail.authoritative_draft;
  const canReview = detail.state === "AWAITING_CONFIRMATION";

  return (
    <div className="review-layout">
      <section className="review-main">
        <div className="review-title-row">
          <div>
            <p className="eyebrow">Independent authorization</p>
            <h1>Review annual leave</h1>
            <p>This is the exact persisted draft. The assistant cannot approve it for you.</p>
          </div>
          <StatusPill state={detail.state} />
        </div>

        <article className="authoritative-review">
          <div className="authoritative-heading">
            <span><ShieldCheck aria-hidden="true" size={20} /></span>
            <div><p className="eyebrow">Authoritative draft</p><h2>Confirm every detail</h2></div>
            <span className="authority-badge">Persisted · Revision {detail.revision}</span>
          </div>
          <dl className="review-fields">
            <div className="review-field-wide"><dt>Leave period</dt><dd>{formatDate(draft.start_date)} <span>to</span> {formatDate(draft.end_date)}</dd></div>
            <div><dt>Scheduled work days</dt><dd>{draft.scheduled_work_days}</dd></div>
            <div><dt>Requested hours</dt><dd>{formatHours(draft.requested_hours)}</dd></div>
            <div><dt>Projected balance</dt><dd>{formatHours(draft.projected_balance_hours)}</dd></div>
            <div><dt>Readiness</dt><dd>{draft.readiness}</dd></div>
            <div className="review-field-wide"><dt>Reason</dt><dd>{draft.reason ?? "No reason provided"}</dd></div>
          </dl>
          <div className="draft-proof">
            <Fingerprint aria-hidden="true" size={17} />
            <span>Authority snapshot</span>
            <code>{draft.authority_snapshot_hash.slice(0, 16)}…</code>
          </div>
        </article>

        {detail.state === "SUCCEEDED" && detail.result ? (
          <div className="review-success">
            <CheckCircle2 aria-hidden="true" size={25} />
            <div><p className="eyebrow">Execution succeeded</p><h2>Your leave is recorded.</h2><p>Submitted {formatDateTime(detail.result.submitted_at)}. Your available balance has been updated.</p></div>
            <Link className="button button-primary" href={`/requests/${detail.action_id}`}>View evidence <ArrowRight aria-hidden="true" size={15} /></Link>
          </div>
        ) : detail.state === "CONFIRMED" ? (
          <div className="execution-queued" role="status">
            <LoaderCircle aria-hidden="true" className="spin" size={20} />
            <div><strong>Authorized and queued</strong><p>The existing V4 worker is processing this request. This page will update automatically.</p></div>
          </div>
        ) : canReview ? (
          <div className="authorization-panel">
            <div className="authorization-heading"><LockKeyhole aria-hidden="true" size={19} /><div><strong>Explicit authorization required</strong><p>A short-lived challenge binds your approval to this exact draft.</p></div></div>
            {!challenge ? (
              <button className="button button-primary button-wide" type="button" disabled={pending !== null} onClick={() => void beginAuthorization()}>
                {pending === "challenge" ? <LoaderCircle className="spin" aria-hidden="true" size={16} /> : <LockKeyhole aria-hidden="true" size={16} />}
                Begin authorization
              </button>
            ) : (
              <div className="challenge-step">
                <p className="challenge-valid">Challenge valid until {formatDateTime(challenge.expires_at)}</p>
                <label className="review-check">
                  <input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} />
                  <span>I have reviewed these dates and hours, and I explicitly authorize this annual leave request.</span>
                </label>
                <button className="button button-primary button-wide" type="button" disabled={!reviewed || pending !== null} onClick={() => void authorize()}>
                  {pending === "confirm" ? <LoaderCircle className="spin" aria-hidden="true" size={16} /> : <ShieldCheck aria-hidden="true" size={16} />}
                  Authorize and submit request
                </button>
              </div>
            )}
            <button className="cancel-link" type="button" disabled={pending !== null} onClick={() => void cancel()}>
              {pending === "cancel" ? <LoaderCircle className="spin" aria-hidden="true" size={14} /> : <X aria-hidden="true" size={14} />}
              Cancel draft
            </button>
          </div>
        ) : (
          <div className="terminal-note"><CircleAlert aria-hidden="true" size={20} /><div><strong>This request can no longer be authorized.</strong><p>Its current state is {detail.state.toLowerCase().replaceAll("_", " ")}.</p></div></div>
        )}
        {error ? <div className="form-error" role="alert"><CircleAlert aria-hidden="true" size={17} />{error}</div> : null}
      </section>

      <aside className="review-aside">
        <p className="eyebrow">Why this is separate</p>
        <h2>Conversation is not consent.</h2>
        <p>Messages such as “yes, submit it” stay non-authoritative. Only the bound control on this page can confirm the action.</p>
        <ol>
          <li data-complete="true"><span>1</span><div><strong>Prepared</strong><p>Trusted data produced the persisted draft.</p></div></li>
          <li data-complete={challenge !== null || detail.state !== "AWAITING_CONFIRMATION"}><span>2</span><div><strong>Bound review</strong><p>A short-lived challenge matches this revision.</p></div></li>
          <li data-complete={detail.state !== "AWAITING_CONFIRMATION"}><span>3</span><div><strong>Authorized</strong><p>Your explicit confirmation enters V4 execution.</p></div></li>
          <li data-complete={detail.state === "SUCCEEDED"}><span>4</span><div><strong>Recorded</strong><p>Result and audit evidence become visible.</p></div></li>
        </ol>
        <small>Action ID<br /><code>{detail.action_id}</code></small>
      </aside>
    </div>
  );
}

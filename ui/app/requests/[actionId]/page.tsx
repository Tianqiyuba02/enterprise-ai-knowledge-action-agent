import {
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  Fingerprint,
  ScrollText,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { BackLink, DataUnavailable, PageIntro } from "@/components/shared";
import { StatusPill } from "@/components/status-pill";
import { backendFetch, isMissingPortalResource } from "@/lib/backend";
import type { ActionDetail, AnnualLeaveActionDetail, ITActionDetail } from "@/lib/contracts";
import { formatDate, formatDateTime, formatHours, sentenceCase } from "@/lib/format";

export const metadata = { title: "Request detail" };

type TerminalNonSuccessState = "EXECUTION_FAILED" | "STALE" | "EXPIRED" | "CANCELLED";

type EmployeeOutcome = {
  title: string;
  reason: string | null;
  impact: string;
  nextStep: string;
  tone: "danger" | "neutral";
};

const EMPLOYEE_SAFE_FAILURE_REASONS: Record<string, string> = {
  DRAFT_INTEGRITY_FAILURE: "The saved draft could not be verified safely.",
  CALENDAR_UNCOVERED: "The working calendar could not confirm every requested date.",
  INSUFFICIENT_BALANCE: "Your available annual leave balance is no longer enough for this request.",
  OVERLAP: "These dates now overlap another submitted annual leave request.",
  AUTHORITY_CHANGED: "Trusted employee or leave-rule information changed after this draft was prepared.",
};

function terminalMetadataValue(detail: ActionDetail, key: "failure_kind" | "reason"): string | null {
  const event = detail.audit_events
    .toReversed()
    .find((item) => item.to_state === detail.state && typeof item.safe_metadata[key] === "string");
  const value = event?.safe_metadata[key];
  return typeof value === "string" ? value : null;
}

function employeeOutcome(detail: AnnualLeaveActionDetail): EmployeeOutcome | null {
  const state = detail.state as TerminalNonSuccessState;
  if (state === "EXECUTION_FAILED") {
    const failureKind = terminalMetadataValue(detail, "failure_kind");
    return {
      title: "We couldn’t submit this leave request.",
      reason: failureKind ? (EMPLOYEE_SAFE_FAILURE_REASONS[failureKind] ?? null) : null,
      impact: "Nothing was submitted and your leave balance was not changed.",
      nextStep: "Review your balance and dates, then prepare a new request.",
      tone: "danger",
    };
  }
  if (state === "STALE") {
    const failureKind = terminalMetadataValue(detail, "failure_kind");
    return {
      title: "This draft is no longer current.",
      reason:
        (failureKind ? EMPLOYEE_SAFE_FAILURE_REASONS[failureKind] : null) ??
        "The saved draft no longer matches current trusted information.",
      impact: "Nothing was submitted and your leave balance was not changed.",
      nextStep: "Prepare a new request so it can be checked against current information.",
      tone: "danger",
    };
  }
  if (state === "EXPIRED") {
    const reason = terminalMetadataValue(detail, "reason");
    return {
      title: "This draft expired before it was submitted.",
      reason:
        reason === "confirmed_ttl"
          ? "The secure processing window closed before the request was recorded."
          : "The review window ended before the request was completed.",
      impact: "Nothing was submitted and your leave balance was not changed.",
      nextStep: "Prepare a new request if you still want to take these dates off.",
      tone: "neutral",
    };
  }
  if (state === "CANCELLED") {
    return {
      title: "This leave request was cancelled.",
      reason: null,
      impact: "Nothing was submitted and your leave balance was not changed.",
      nextStep: "Prepare a new request if you still need annual leave.",
      tone: "neutral",
    };
  }
  return null;
}

function itEmployeeOutcome(detail: ITActionDetail): EmployeeOutcome {
  if (detail.state === "SUCCEEDED") {
    return {
      title: detail.result ? `${detail.result.ticket_id} was created.` : "Your IT ticket was created.",
      reason: null,
      impact: "The IT team can now triage this issue.",
      nextStep: "Follow its latest status in My Tickets.",
      tone: "neutral",
    };
  }
  if (detail.state === "CONFIRMED") {
    return {
      title: "Your request is authorized and queued.",
      reason: null,
      impact: "No ticket number is available until the internal worker records it.",
      nextStep: "This page will show the outcome after processing.",
      tone: "neutral",
    };
  }
  if (detail.state === "AWAITING_CONFIRMATION") {
    return {
      title: "This IT request is waiting for your review.",
      reason: null,
      impact: "Nothing has been submitted and no ticket exists yet.",
      nextStep: "Review the exact draft before authorizing ticket creation.",
      tone: "neutral",
    };
  }
  const failureKind = terminalMetadataValue(detail, "failure_kind");
  return {
    title: detail.state === "CANCELLED"
      ? "This IT request was cancelled."
      : detail.state === "EXPIRED"
        ? "This IT request expired."
        : detail.state === "STALE"
          ? "This IT request is no longer current."
          : "The IT ticket could not be created.",
    reason: failureKind === "DRAFT_INTEGRITY_FAILURE"
      ? "The saved draft could not be verified safely."
      : failureKind === "AUTHORITY_CHANGED"
        ? "Trusted request information changed after preparation."
        : null,
    impact: "Nothing was submitted and no IT ticket was created.",
    nextStep: "Prepare a new IT request if support is still needed.",
    tone: detail.state === "CANCELLED" || detail.state === "EXPIRED" ? "neutral" : "danger",
  };
}

function ITRequestDetail({ detail }: { detail: ITActionDetail }) {
  const draft = detail.authoritative_draft;
  const outcome = itEmployeeOutcome(detail);
  return (
    <div className="page-shell detail-page">
      <BackLink href="/requests">All requests</BackLink>
      <PageIntro
        eyebrow={`Request ${detail.action_id.slice(0, 8).toUpperCase()}`}
        title="IT support request"
        description={`Created ${formatDateTime(detail.created_at)} · Revision ${detail.revision}`}
        action={<StatusPill state={detail.state} />}
      />
      <section className="panel detail-primary outcome-first">
        <p className="eyebrow">Outcome</p>
        <h2>{outcome.title}</h2>
        {outcome.reason ? <p><b>Known reason:</b> {outcome.reason}</p> : null}
        <p>{outcome.impact} {outcome.nextStep}</p>
        {detail.state === "AWAITING_CONFIRMATION" ? (
          <Link className="button button-primary" href={`/it/review/${detail.action_id}`}>
            Review exact draft <ArrowRight aria-hidden="true" size={14} />
          </Link>
        ) : null}
      </section>
      <section className="detail-grid detail-secondary-grid">
        <article className="panel detail-primary">
          <div className="detail-title">
            <span><ScrollText aria-hidden="true" size={20} /></span>
            <div><p className="eyebrow">Request</p><h2>Issue details</h2></div>
          </div>
          <dl className="definition-grid">
            <div><dt>Category</dt><dd>{sentenceCase(draft.category)}</dd></div>
            <div><dt>Urgency</dt><dd>{sentenceCase(draft.urgency)}</dd></div>
            <div className="definition-wide"><dt>Summary</dt><dd>{draft.summary}</dd></div>
            <div className="definition-wide"><dt>Description</dt><dd>{draft.description}</dd></div>
            {detail.result ? <div className="definition-wide"><dt>Ticket</dt><dd>{detail.result.ticket_id} · {sentenceCase(detail.result.status)}</dd></div> : null}
          </dl>
        </article>
        <aside className="panel authority-card">
          <ShieldCheck aria-hidden="true" size={21} />
          <p className="eyebrow">Sources &amp; checks</p>
          <h2>Trusted and revision-bound</h2>
          <dl>
            <div><dt>Action type</dt><dd>IT support ticket</dd></div>
            <div><dt>Ruleset</dt><dd>{draft.ruleset_version}</dd></div>
            <div><dt>Revision</dt><dd>{detail.revision}</dd></div>
          </dl>
          <details className="technical-snapshot">
            <summary>Technical evidence</summary>
            <small title={draft.authority_snapshot_hash}>Snapshot {draft.authority_snapshot_hash.slice(0, 12)}…</small>
          </details>
        </aside>
      </section>
      <details className="panel technical-evidence">
        <summary>Show technical evidence</summary>
        <div><p>Action ID <code>{detail.action_id}</code></p><p>Current revision {detail.revision}</p></div>
        <ol className="audit-timeline">
          {detail.audit_events.map((event) => (
            <li key={event.event_id}>
              <span aria-hidden="true" />
              <div><strong>{sentenceCase(event.event_type)}</strong><p>Revision {event.revision}</p></div>
              <time dateTime={event.created_at}>{formatDateTime(event.created_at)}</time>
            </li>
          ))}
        </ol>
      </details>
    </div>
  );
}

export default async function RequestDetailPage({ params }: { params: Promise<{ actionId: string }> }) {
  const { actionId } = await params;
  let detail: ActionDetail;
  try {
    detail = await backendFetch<ActionDetail>(`/actions/${encodeURIComponent(actionId)}/detail`);
  } catch (error) {
    if (isMissingPortalResource(error)) notFound();
    return <div className="page-shell"><DataUnavailable /></div>;
  }
  if (detail.action_type === "create_it_support_ticket") {
    return <ITRequestDetail detail={detail} />;
  }
  const draft = detail.authoritative_draft;
  const outcome = employeeOutcome(detail);
  return (
    <div className="page-shell detail-page">
      <BackLink href="/requests">All requests</BackLink>
      <PageIntro
        eyebrow={`Request ${detail.action_id.slice(0, 8).toUpperCase()}`}
        title="Annual leave request"
        description={`Created ${formatDateTime(detail.created_at)} · Revision ${detail.revision}`}
        action={<StatusPill state={detail.state} />}
      />
      <section className="detail-grid">
        <article className="panel detail-primary">
          <div className="detail-title">
            <span><ScrollText aria-hidden="true" size={20} /></span>
            <div><p className="eyebrow">Authoritative draft</p><h2>Request particulars</h2></div>
          </div>
          <dl className="definition-grid">
            <div><dt>Leave period</dt><dd>{formatDate(draft.start_date)} – {formatDate(draft.end_date)}</dd></div>
            <div><dt>Duration</dt><dd>{formatHours(draft.requested_hours)}</dd></div>
            <div><dt>Scheduled days</dt><dd>{draft.scheduled_work_days}</dd></div>
            <div><dt>Projected balance</dt><dd>{formatHours(draft.projected_balance_hours)}</dd></div>
            <div className="definition-wide"><dt>Reason</dt><dd>{draft.reason ?? "No reason provided"}</dd></div>
          </dl>
          {detail.result ? (
            <div className="execution-result">
              <CheckCircle2 aria-hidden="true" size={20} />
              <div>
                <strong>Recorded successfully</strong>
                <p>Leave request {detail.result.leave_request_id.slice(0, 8).toUpperCase()} was submitted {formatDateTime(detail.result.submitted_at)}.</p>
              </div>
            </div>
          ) : outcome ? (
            <div className="terminal-outcome" data-tone={outcome.tone}>
              <CircleAlert aria-hidden="true" size={20} />
              <div>
                <p className="eyebrow">Outcome</p>
                <strong>{outcome.title}</strong>
                {outcome.reason ? <p><b>Why:</b> {outcome.reason}</p> : null}
                <p><b>Business impact:</b> {outcome.impact}</p>
                <p><b>Next step:</b> {outcome.nextStep}</p>
                <Link className="text-link" href="/assistant?intent=leave">
                  Prepare a new request <ArrowRight aria-hidden="true" size={14} />
                </Link>
              </div>
            </div>
          ) : null}
        </article>
        <aside className="panel authority-card">
          <Fingerprint aria-hidden="true" size={21} />
          <p className="eyebrow">Verified at preparation</p>
          <h2>Trusted business context</h2>
          <dl>
            <div><dt>Employee</dt><dd>{draft.stable_authority.employee_id}</dd></div>
            <div><dt>Jurisdiction</dt><dd>{draft.stable_authority.jurisdiction}</dd></div>
            <div><dt>Calendar</dt><dd>{draft.calendar_version}</dd></div>
            <div><dt>Ruleset</dt><dd>{draft.ruleset_version}</dd></div>
          </dl>
          <details className="technical-snapshot">
            <summary>Technical evidence</summary>
            <small title={draft.authority_snapshot_hash}>Snapshot {draft.authority_snapshot_hash.slice(0, 12)}…</small>
          </details>
        </aside>
      </section>
      <section className="panel audit-panel">
        <div className="panel-heading"><div><p className="eyebrow">Evidence</p><h2>Audit timeline</h2></div></div>
        <ol className="audit-timeline">
          {detail.audit_events.map((event) => (
            <li key={event.event_id}>
              <span aria-hidden="true" />
              <div>
                <strong>{sentenceCase(event.event_type)}</strong>
                <p>{event.from_state ? `${sentenceCase(event.from_state)} → ` : ""}{event.to_state ? sentenceCase(event.to_state) : "Recorded"}</p>
              </div>
              <time dateTime={event.created_at}>{formatDateTime(event.created_at)}</time>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

import { CheckCircle2, Fingerprint, ScrollText } from "lucide-react";
import { notFound } from "next/navigation";

import { BackLink, DataUnavailable, PageIntro } from "@/components/shared";
import { StatusPill } from "@/components/status-pill";
import { backendFetch, PortalApiError } from "@/lib/backend";
import type { ActionDetail } from "@/lib/contracts";
import { formatDate, formatDateTime, formatHours, sentenceCase } from "@/lib/format";

export const metadata = { title: "Request detail" };

export default async function RequestDetailPage({ params }: { params: Promise<{ actionId: string }> }) {
  const { actionId } = await params;
  let detail: ActionDetail;
  try {
    detail = await backendFetch<ActionDetail>(`/actions/${encodeURIComponent(actionId)}/detail`);
  } catch (error) {
    if (error instanceof PortalApiError && error.status === 404) notFound();
    return <div className="page-shell"><DataUnavailable /></div>;
  }
  const draft = detail.authoritative_draft;
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
          ) : null}
        </article>
        <aside className="panel authority-card">
          <Fingerprint aria-hidden="true" size={21} />
          <p className="eyebrow">Authority snapshot</p>
          <h2>Trusted at preparation</h2>
          <dl>
            <div><dt>Employee</dt><dd>{draft.stable_authority.employee_id}</dd></div>
            <div><dt>Jurisdiction</dt><dd>{draft.stable_authority.jurisdiction}</dd></div>
            <div><dt>Calendar</dt><dd>{draft.calendar_version}</dd></div>
            <div><dt>Ruleset</dt><dd>{draft.ruleset_version}</dd></div>
          </dl>
          <small title={draft.authority_snapshot_hash}>Snapshot {draft.authority_snapshot_hash.slice(0, 12)}…</small>
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

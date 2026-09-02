import { ArrowRight, ClipboardList } from "lucide-react";
import Link from "next/link";

import { DataUnavailable, PageIntro } from "@/components/shared";
import { StatusPill } from "@/components/status-pill";
import { backendFetch } from "@/lib/backend";
import type { ActionList } from "@/lib/contracts";
import { formatDate, formatDateTime, formatHours } from "@/lib/format";

export const metadata = { title: "My requests" };

export default async function RequestsPage() {
  let actions: ActionList;
  try {
    actions = await backendFetch<ActionList>("/me/actions?limit=50");
  } catch {
    return <div className="page-shell"><DataUnavailable /></div>;
  }
  return (
    <div className="page-shell">
      <PageIntro
        eyebrow="Governed workflow"
        title="My requests"
        description="Prepared, authorized and completed actions tied to your trusted identity."
      />
      <section className="panel requests-panel">
        <div className="requests-summary">
          <span><b>{actions.total}</b> total requests</span>
          <span><i data-tone="attention" />Awaiting review</span>
          <span><i data-tone="success" />Succeeded</span>
        </div>
        {actions.items.length ? (
          <div className="request-cards">
            {actions.items.map((action) => (
              <Link className="request-card" href={`/requests/${action.action_id}`} key={action.action_id}>
                <span className="request-card-date">
                  <small>{new Date(`${action.start_date}T12:00:00`).toLocaleString("en-AU", { month: "short" })}</small>
                  <strong>{new Date(`${action.start_date}T12:00:00`).getDate()}</strong>
                </span>
                <span className="request-card-copy">
                  <small>Annual leave · Revision {action.revision}</small>
                  <strong>{formatDate(action.start_date)} – {formatDate(action.end_date)}</strong>
                  <span>{formatHours(action.requested_hours)} · Created {formatDateTime(action.created_at)}</span>
                </span>
                <StatusPill state={action.state} />
                <ArrowRight aria-hidden="true" size={17} />
              </Link>
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <ClipboardList aria-hidden="true" size={25} />
            <h3>No requests yet</h3>
            <p>Ask the assistant to prepare an annual leave draft.</p>
            <Link className="button button-secondary" href="/assistant">Open assistant</Link>
          </div>
        )}
      </section>
    </div>
  );
}

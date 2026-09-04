import { ArrowRight, CalendarDays, Clock3, Palmtree } from "lucide-react";
import Link from "next/link";

import { DataUnavailable, PageIntro } from "@/components/shared";
import { backendFetch } from "@/lib/backend";
import type { EmployeeProfile, LeaveSummary } from "@/lib/contracts";
import { formatDate, formatDateTime, formatDays, formatHours, sentenceCase } from "@/lib/format";

export const metadata = { title: "My leave" };

export default async function LeavePage() {
  let profile: EmployeeProfile;
  let summary: LeaveSummary;
  try {
    [profile, summary] = await Promise.all([
      backendFetch<EmployeeProfile>("/me/profile"),
      backendFetch<LeaveSummary>("/me/leave/summary"),
    ]);
  } catch {
    return (
      <div className="page-shell">
        <DataUnavailable />
      </div>
    );
  }
  const annual = summary.balances.find((item) => item.leave_type === "annual");
  const personal = summary.balances.find((item) => item.leave_type === "personal");

  return (
    <div className="page-shell">
      <PageIntro
        eyebrow="Time away"
        title="My leave"
        description="A current view of your entitlement and submitted annual leave."
        action={
          <Link className="button button-primary" href="/assistant?intent=leave">
            Prepare annual leave <ArrowRight aria-hidden="true" size={16} />
          </Link>
        }
      />
      <section className="balance-grid" aria-label="Leave balances">
        <article className="balance-card balance-card-featured">
          <Palmtree aria-hidden="true" size={22} />
          <p>Annual leave</p>
          <strong>{annual ? formatDays(annual.available_hours, profile.hours_per_day) : "—"}</strong>
          <span>{annual ? `${formatHours(annual.available_hours)} available` : "Unavailable"}</span>
          {annual ? (
            <div className="balance-breakdown">
              <span>Base entitlement <b>{formatHours(annual.base_balance_hours)}</b></span>
              <span>Submitted <b>{formatHours(annual.committed_hours)}</b></span>
            </div>
          ) : null}
        </article>
        <article className="balance-card">
          <Clock3 aria-hidden="true" size={22} />
          <p>Personal leave</p>
          <strong>
            {personal ? formatDays(personal.available_hours, profile.hours_per_day) : "—"}
          </strong>
          <span>{personal ? formatHours(personal.available_hours) : "Unavailable"}</span>
          <p className="card-footnote">Read-only capability</p>
        </article>
        <article className="balance-card balance-card-context">
          <CalendarDays aria-hidden="true" size={22} />
          <p>Your working pattern</p>
          <strong>{profile.hours_per_day} hrs</strong>
          <span>{profile.work_days.map(sentenceCase).join(" · ")}</span>
          <p className="card-footnote">{profile.timezone}</p>
        </article>
      </section>

      <section className="panel leave-history">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Submitted records</p>
            <h2>Annual leave history</h2>
          </div>
          <small>Computed {formatDateTime(summary.computed_at)}</small>
        </div>
        {summary.requests.length ? (
          <div className="history-table-wrap">
            <table className="history-table">
              <thead>
                <tr>
                  <th>Dates</th>
                  <th>Duration</th>
                  <th>Status</th>
                  <th>Submitted</th>
                  <th><span className="sr-only">Open</span></th>
                </tr>
              </thead>
              <tbody>
                {summary.requests.map((request) => (
                  <tr key={request.leave_request_id}>
                    <td>{formatDate(request.start_date)} – {formatDate(request.end_date)}</td>
                    <td>{formatHours(request.requested_hours)}</td>
                    <td><span className="record-status">Submitted</span></td>
                    <td>{formatDateTime(request.submitted_at)}</td>
                    <td>
                      <Link aria-label="Open request detail" href={`/requests/${request.source_action_id}`}>
                        <ArrowRight aria-hidden="true" size={16} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-state">
            <CalendarDays aria-hidden="true" size={25} />
            <h3>No submitted leave yet</h3>
            <p>When an authorized request succeeds, it will appear here.</p>
          </div>
        )}
      </section>
    </div>
  );
}

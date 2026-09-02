import {
  ArrowRight,
  BookOpenText,
  CalendarCheck2,
  MessageCircleMore,
  Sparkles,
} from "lucide-react";
import Link from "next/link";

import { DataUnavailable } from "@/components/shared";
import { StatusPill } from "@/components/status-pill";
import { backendFetch } from "@/lib/backend";
import type {
  ActionList,
  EmployeeProfile,
  LeaveSummary,
  PolicyDocumentList,
} from "@/lib/contracts";
import { formatDate, formatDays, formatHours } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const melbourneWeekday = new Intl.DateTimeFormat("en-AU", {
    weekday: "long",
    timeZone: "Australia/Melbourne",
  }).format(new Date());
  const [profileResult, leaveResult, actionsResult, policiesResult] = await Promise.allSettled([
    backendFetch<EmployeeProfile>("/me/profile"),
    backendFetch<LeaveSummary>("/me/leave/summary"),
    backendFetch<ActionList>("/me/actions?limit=4"),
    backendFetch<PolicyDocumentList>("/knowledge/documents"),
  ]);
  if (profileResult.status === "rejected" || leaveResult.status === "rejected") {
    return (
      <div className="page-shell">
        <DataUnavailable />
      </div>
    );
  }

  const profile = profileResult.value;
  const leave = leaveResult.value;
  const annual = leave.balances.find((item) => item.leave_type === "annual");
  const actions = actionsResult.status === "fulfilled" ? actionsResult.value.items : [];
  const policies = policiesResult.status === "fulfilled" ? policiesResult.value.items : [];
  const attention = actions.find((action) => action.state === "AWAITING_CONFIRMATION");

  return (
    <div className="page-shell home-page">
      <section className="home-hero">
        <div className="hero-copy">
          <p className="eyebrow">{melbourneWeekday} · Melbourne</p>
          <h1>Welcome back, {profile.full_name.split(" ")[0]}.</h1>
          <p>
            Ask a policy question, prepare leave, or pick up a request where you left it.
          </p>
          <Link className="button button-primary" href="/assistant">
            <MessageCircleMore aria-hidden="true" size={17} />
            Ask the assistant
          </Link>
        </div>
        <div className="hero-balance">
          <div className="balance-orbit" aria-hidden="true" />
          <p>Annual leave available</p>
          <strong>{annual ? formatDays(annual.available_hours, profile.hours_per_day) : "—"}</strong>
          <span>{annual ? formatHours(annual.available_hours) : "No balance found"}</span>
          <Link href="/leave">
            View leave details <ArrowRight aria-hidden="true" size={15} />
          </Link>
        </div>
      </section>

      {attention ? (
        <section className="attention-strip" aria-label="Request requiring attention">
          <span className="attention-icon">
            <Sparkles aria-hidden="true" size={18} />
          </span>
          <div>
            <p className="eyebrow">Ready for your review</p>
            <strong>
              Annual leave · {formatDate(attention.start_date)}–{formatDate(attention.end_date)}
            </strong>
          </div>
          <Link href={`/leave/review/${attention.action_id}`}>
            Review authoritative draft <ArrowRight aria-hidden="true" size={16} />
          </Link>
        </section>
      ) : null}

      <section className="home-grid">
        <div className="panel recent-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Workflow</p>
              <h2>Recent requests</h2>
            </div>
            <Link href="/requests">View all</Link>
          </div>
          {actions.length ? (
            <div className="request-list">
              {actions.map((action) => (
                <Link className="request-row" href={`/requests/${action.action_id}`} key={action.action_id}>
                  <span className="request-icon">
                    <CalendarCheck2 aria-hidden="true" size={19} />
                  </span>
                  <span className="request-main">
                    <strong>Annual leave</strong>
                    <small>
                      {formatDate(action.start_date)}–{formatDate(action.end_date)} ·{" "}
                      {formatHours(action.requested_hours)}
                    </small>
                  </span>
                  <StatusPill state={action.state} />
                  <ArrowRight className="row-arrow" aria-hidden="true" size={16} />
                </Link>
              ))}
            </div>
          ) : (
            <div className="empty-compact">
              <p>No requests yet.</p>
              <Link href="/assistant">Prepare annual leave</Link>
            </div>
          )}
        </div>

        <aside className="panel policy-peek">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Source of truth</p>
              <h2>Policy library</h2>
            </div>
            <BookOpenText aria-hidden="true" size={21} />
          </div>
          <p>Approved, applicable policy revisions used by your assistant.</p>
          <div className="policy-count">
            <strong>{policies.length.toString().padStart(2, "0")}</strong>
            <span>current documents</span>
          </div>
          <Link className="text-link" href="/policies">
            Browse governed sources <ArrowRight aria-hidden="true" size={15} />
          </Link>
        </aside>
      </section>
    </div>
  );
}

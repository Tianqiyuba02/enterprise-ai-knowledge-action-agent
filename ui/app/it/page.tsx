import {
  ArrowRight,
  BookOpenText,
  CircleDot,
  Headphones,
  Laptop,
  Plus,
} from "lucide-react";
import Link from "next/link";

import { DataUnavailable, PageIntro } from "@/components/shared";
import { backendFetch } from "@/lib/backend";
import type { PolicyDocumentList, TicketList } from "@/lib/contracts";
import { formatDateTime, sentenceCase } from "@/lib/format";

export const metadata = { title: "IT Support" };
export const dynamic = "force-dynamic";

export default async function ITSupportPage() {
  const [ticketsResult, policiesResult] = await Promise.allSettled([
    backendFetch<TicketList>("/me/tickets"),
    backendFetch<PolicyDocumentList>("/knowledge/documents"),
  ]);
  if (ticketsResult.status === "rejected") {
    return <div className="page-shell"><DataUnavailable /></div>;
  }
  const tickets = ticketsResult.value;
  const articles = policiesResult.status === "fulfilled"
    ? policiesResult.value.items.filter((item) => item.doc_code.startsWith("SOP-IT-"))
    : [];

  return (
    <div className="page-shell it-page">
      <PageIntro
        eyebrow="Employee support"
        title="IT Support"
        description="Report an issue, follow your tickets, and use approved support guidance."
        action={
          <Link className="button button-primary" href="/assistant?intent=it">
            <Plus aria-hidden="true" size={16} /> New request
          </Link>
        }
      />

      <section className="it-support-grid" aria-label="IT support options">
        <article className="panel it-service-card it-service-featured">
          <Headphones aria-hidden="true" size={24} />
          <p className="eyebrow">New request</p>
          <h2>Tell us what is getting in your way.</h2>
          <p>The assistant can prepare a structured draft. You review and authorize it separately.</p>
          <Link className="button button-primary" href="/assistant?intent=it">
            Prepare an IT request <ArrowRight aria-hidden="true" size={15} />
          </Link>
        </article>
        <article className="panel it-service-card">
          <Laptop aria-hidden="true" size={24} />
          <p className="eyebrow">My tickets</p>
          <h2>{tickets.total} support {tickets.total === 1 ? "ticket" : "tickets"}</h2>
          <p>Only tickets owned by your trusted employee identity appear here.</p>
          <a className="text-link" href="#my-tickets">View tickets <ArrowRight size={15} /></a>
        </article>
        <article className="panel it-service-card">
          <BookOpenText aria-hidden="true" size={24} />
          <p className="eyebrow">Help articles</p>
          <h2>Approved support guidance</h2>
          <p>Use governed instructions for safe self-service and request triage.</p>
          <a className="text-link" href="#help-articles">Browse guidance <ArrowRight size={15} /></a>
        </article>
      </section>

      <section className="panel ticket-panel" id="my-tickets">
        <div className="panel-heading">
          <div><p className="eyebrow">Owned by you</p><h2>My tickets</h2></div>
          <span>{tickets.total} total</span>
        </div>
        {tickets.items.length ? (
          <div className="ticket-list">
            {tickets.items.map((ticket) => (
              <article className="ticket-row" key={ticket.ticket_id}>
                <span className="ticket-symbol"><CircleDot aria-hidden="true" size={18} /></span>
                <div>
                  <small>{ticket.ticket_id} · {sentenceCase(ticket.category)}</small>
                  <strong>{ticket.summary}</strong>
                  <p>{ticket.description}</p>
                </div>
                <span className="ticket-meta">
                  <b>{sentenceCase(ticket.status)}</b>
                  <small>{sentenceCase(ticket.urgency)} urgency</small>
                  <time dateTime={ticket.updated_at}>{formatDateTime(ticket.updated_at)}</time>
                </span>
              </article>
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <Laptop aria-hidden="true" size={25} />
            <h3>No IT tickets yet</h3>
            <p>Prepare a request when you need support.</p>
          </div>
        )}
      </section>

      <section className="panel help-panel" id="help-articles">
        <div className="panel-heading">
          <div><p className="eyebrow">Governed knowledge</p><h2>Help articles</h2></div>
          <Link href="/policies">Policy library</Link>
        </div>
        <div className="help-links">
          {articles.map((article) => (
            <Link
              href={`/policies/${encodeURIComponent(article.doc_code)}/${encodeURIComponent(article.version)}`}
              key={`${article.doc_code}-${article.version}`}
            >
              <BookOpenText aria-hidden="true" size={17} />
              <span><small>{article.doc_code} · Version {article.version}</small><strong>{article.title}</strong></span>
              <ArrowRight aria-hidden="true" size={15} />
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}

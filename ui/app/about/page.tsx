import { Database, ExternalLink, LockKeyhole, RefreshCcw, Server, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { PageIntro } from "@/components/shared";

export const metadata = { title: "About this demo" };

export default function AboutPage() {
  const repository = process.env.GITHUB_REPOSITORY_URL?.trim();
  return (
    <div className="page-shell about-page">
      <PageIntro
        eyebrow="Architecture & safety"
        title="Built to show the boundary, not hide it."
        description="A production-minded prototype demonstrating governed knowledge, trusted synthetic identity, explicit authorization, and PostgreSQL-authoritative action execution."
        action={repository ? <a className="button button-secondary" href={repository} target="_blank" rel="noreferrer">View source <ExternalLink aria-hidden="true" size={15} /></a> : undefined}
      />
      <section className="about-grid">
        <article><Server aria-hidden="true" /><p className="eyebrow">Public edge</p><h2>Next.js portal + BFF</h2><p>The browser talks only to the portal. Trusted demo credentials and private-service addresses remain server-side.</p></article>
        <article><LockKeyhole aria-hidden="true" /><p className="eyebrow">Private application</p><h2>FastAPI control plane</h2><p>Identity is resolved on the server. Chat can read and prepare, while authorization stays on an independent Review surface.</p></article>
        <article><Database aria-hidden="true" /><p className="eyebrow">Authority</p><h2>PostgreSQL + pgvector</h2><p>Governed documents, persisted drafts, quotas, business results, and audit evidence use one transactional source of truth.</p></article>
        <article><ShieldCheck aria-hidden="true" /><p className="eyebrow">Execution</p><h2>Dedicated private worker</h2><p>The private action worker alone executes confirmed actions and publishes an honest heartbeat for readiness.</p></article>
      </section>
      <section className="about-disclosure">
        <RefreshCcw aria-hidden="true" size={21} />
        <div><h2>Shared, synthetic, and regularly reset</h2><p>Alex, Sam, leave records, tickets, and policies are fictional demonstration data. Never enter real personal, confidential, password, or company information.</p></div>
      </section>
      <Link className="text-link" href="/assistant">Try the guided demo scenarios</Link>
    </div>
  );
}

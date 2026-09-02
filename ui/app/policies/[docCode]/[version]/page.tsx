import { FileCheck2 } from "lucide-react";
import { notFound } from "next/navigation";

import { BackLink, DataUnavailable } from "@/components/shared";
import { backendFetch, PortalApiError } from "@/lib/backend";
import type { PolicyDocumentDetail } from "@/lib/contracts";
import { formatDate } from "@/lib/format";

export const metadata = { title: "Policy" };

function policyParagraphs(content: string): string[] {
  return content
    .split(/\n\s*\n/)
    .map((block) =>
      block
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line && !/^#{1,6}\s+/.test(line))
        .join(" "),
    )
    .filter(Boolean);
}

export default async function PolicyDetailPage({ params }: { params: Promise<{ docCode: string; version: string }> }) {
  const { docCode, version } = await params;
  let document: PolicyDocumentDetail;
  try {
    document = await backendFetch<PolicyDocumentDetail>(
      `/knowledge/documents/${encodeURIComponent(docCode)}/versions/${encodeURIComponent(version)}`,
    );
  } catch (error) {
    if (error instanceof PortalApiError && error.status === 404) notFound();
    return <div className="page-shell"><DataUnavailable /></div>;
  }
  return (
    <div className="page-shell policy-reader">
      <BackLink href="/policies">Policy library</BackLink>
      <header className="policy-reader-header">
        <p className="eyebrow">{document.doc_code} · Version {document.version}</p>
        <h1>{document.title}</h1>
        <div className="policy-meta">
          <span><FileCheck2 aria-hidden="true" size={15} /> Approved</span>
          <span>Effective {formatDate(document.effective_date)}</span>
          <span>{document.jurisdiction}</span>
        </div>
      </header>
      <div className="policy-reader-grid">
        <aside className="policy-toc">
          <p className="eyebrow">In this policy</p>
          <nav aria-label="Policy sections">
            {document.sections.map((section) => (
              <a href={`#${section.anchor}`} key={section.anchor}>{section.section_label}</a>
            ))}
          </nav>
        </aside>
        <article className="policy-body">
          {document.sections.map((section) => (
            <section id={section.anchor} key={section.anchor}>
              <p>{section.page ? `Page ${section.page}` : document.doc_code}</p>
              <h2>{section.section_label}</h2>
              {policyParagraphs(section.content).map((paragraph, index) => (
                <p key={index}>{paragraph}</p>
              ))}
            </section>
          ))}
        </article>
      </div>
    </div>
  );
}

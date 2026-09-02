import { ArrowRight, BookOpenText, FileCheck2 } from "lucide-react";
import Link from "next/link";

import { DataUnavailable, PageIntro } from "@/components/shared";
import { backendFetch } from "@/lib/backend";
import type { PolicyDocumentList } from "@/lib/contracts";
import { formatDate } from "@/lib/format";

export const metadata = { title: "Policy library" };

export default async function PoliciesPage() {
  let documents: PolicyDocumentList;
  try {
    documents = await backendFetch<PolicyDocumentList>("/knowledge/documents");
  } catch {
    return <div className="page-shell"><DataUnavailable /></div>;
  }
  return (
    <div className="page-shell policy-page">
      <PageIntro
        eyebrow="Governed knowledge"
        title="Policy library"
        description="Approved policy revisions applicable to your location and employee group."
      />
      <div className="library-banner">
        <FileCheck2 aria-hidden="true" size={20} />
        <p><strong>Applicability is resolved for you.</strong> Draft, expired and non-applicable documents are not shown.</p>
      </div>
      <section className="policy-library" aria-label={`${documents.total} policy documents`}>
        {documents.items.map((document, index) => (
          <Link
            className="policy-card"
            href={`/policies/${encodeURIComponent(document.doc_code)}/${encodeURIComponent(document.version)}`}
            key={`${document.doc_code}-${document.version}`}
          >
            <span className="policy-index">{String(index + 1).padStart(2, "0")}</span>
            <BookOpenText aria-hidden="true" size={22} />
            <div>
              <p>{document.doc_code} · Version {document.version}</p>
              <h2>{document.title}</h2>
              <span>Effective {formatDate(document.effective_date)} · {document.section_count} sections</span>
            </div>
            <span className="approved-mark">Approved</span>
            <ArrowRight aria-hidden="true" size={17} />
          </Link>
        ))}
      </section>
    </div>
  );
}

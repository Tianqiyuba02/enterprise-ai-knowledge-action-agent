import { AlertCircle, ArrowLeft } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

export function PageIntro({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-intro">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {action ? <div className="page-action">{action}</div> : null}
    </header>
  );
}

export function BackLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link className="back-link" href={href}>
      <ArrowLeft aria-hidden="true" size={15} />
      {children}
    </Link>
  );
}

export function DataUnavailable({ detail }: { detail?: string }) {
  return (
    <div className="data-unavailable" role="alert">
      <AlertCircle aria-hidden="true" size={20} />
      <div>
        <strong>Portal data is temporarily unavailable</strong>
        <p>{detail ?? "Check that the employee service is running, then try again."}</p>
      </div>
    </div>
  );
}

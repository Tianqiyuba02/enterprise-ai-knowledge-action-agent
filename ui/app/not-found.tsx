import { ArrowRight, FileQuestion } from "lucide-react";
import Link from "next/link";

export default function NotFound() {
  return (
    <div className="page-shell not-found-page">
      <section className="panel not-found-panel">
        <span className="not-found-mark"><FileQuestion aria-hidden="true" size={24} /></span>
        <p className="eyebrow">Item unavailable</p>
        <h1>We couldn’t find that portal item.</h1>
        <p>
          It may belong to another demo identity, have been replaced during a shared reset,
          or no longer be available. No private details were revealed.
        </p>
        <div className="not-found-actions">
          <Link className="button button-primary" href="/requests">
            View my requests <ArrowRight aria-hidden="true" size={15} />
          </Link>
          <Link className="button button-secondary" href="/policies">Policy library</Link>
          <Link className="text-link" href="/">Return home</Link>
        </div>
      </section>
    </div>
  );
}

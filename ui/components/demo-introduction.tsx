"use client";

import { ShieldCheck, X } from "lucide-react";
import { useEffect, useState } from "react";

const STORAGE_KEY = "northstar-demo-introduction-v1";

export function DemoIntroduction() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setOpen(window.localStorage.getItem(STORAGE_KEY) !== "seen");
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  function close() {
    window.localStorage.setItem(STORAGE_KEY, "seen");
    setOpen(false);
  }

  if (!open) return null;
  return (
    <div className="demo-intro-backdrop" role="presentation">
      <section className="demo-intro" role="dialog" aria-modal="true" aria-labelledby="demo-title">
        <button className="demo-intro-close" type="button" onClick={close} aria-label="Close demo introduction">
          <X aria-hidden="true" size={17} />
        </button>
        <span className="demo-intro-mark"><ShieldCheck aria-hidden="true" size={22} /></span>
        <p className="eyebrow">Public portfolio demonstration</p>
        <h2 id="demo-title">A safe, shared employee-portal demo.</h2>
        <p>
          Everything here is synthetic. Alex and Sam are fictional identities, and this shared
          environment resets on a schedule. Do not enter personal information, passwords,
          confidential material, or real support details.
        </p>
        <p>
          The assistant can read governed sources and prepare drafts. It cannot authorize or
          execute a request from chat; every business action requires the separate Review surface.
        </p>
        <button className="button button-primary" type="button" onClick={close}>Explore the demo</button>
      </section>
    </div>
  );
}

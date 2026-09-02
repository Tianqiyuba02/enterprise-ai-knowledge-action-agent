"use client";

import { Check, ChevronsUpDown } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import type { PersonaId } from "@/lib/contracts";
import { PERSONAS } from "@/lib/personas";

export function IdentitySwitcher({ current }: { current: PersonaId }) {
  const router = useRouter();
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const [pending, setPending] = useState<PersonaId | null>(null);

  async function selectPersona(persona: PersonaId) {
    if (persona === current || pending) return;
    setPending(persona);
    try {
      const response = await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ persona }),
      });
      if (!response.ok) throw new Error("Identity switch failed");
      detailsRef.current?.removeAttribute("open");
      router.push("/");
      router.refresh();
    } finally {
      setPending(null);
    }
  }

  const selected = PERSONAS[current];
  return (
    <details className="identity" ref={detailsRef}>
      <summary aria-label={`Current demo identity: ${selected.fullName}`}>
        <span className="avatar" aria-hidden="true">
          {selected.initials}
        </span>
        <span className="identity-copy">
          <strong>{selected.fullName}</strong>
          <small>Trusted demo identity</small>
        </span>
        <ChevronsUpDown aria-hidden="true" size={16} />
      </summary>
      <div className="identity-menu">
        <p>View portal as</p>
        {(Object.keys(PERSONAS) as PersonaId[]).map((persona) => {
          const item = PERSONAS[persona];
          return (
            <button
              type="button"
              key={persona}
              onClick={() => selectPersona(persona)}
              disabled={pending !== null}
            >
              <span className="avatar avatar-small" aria-hidden="true">
                {item.initials}
              </span>
              <span>
                <strong>{item.fullName}</strong>
                <small>{item.descriptor}</small>
              </span>
              {persona === current ? <Check aria-label="Selected" size={16} /> : null}
            </button>
          );
        })}
        <p className="identity-note">Identity is resolved on the server.</p>
      </div>
    </details>
  );
}

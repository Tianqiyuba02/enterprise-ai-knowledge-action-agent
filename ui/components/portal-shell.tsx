import { ShieldCheck } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { IdentitySwitcher } from "@/components/identity-switcher";
import { SidebarNav } from "@/components/sidebar-nav";
import type { EmployeeProfile, PersonaId } from "@/lib/contracts";
import { PERSONAS } from "@/lib/personas";

export function PortalShell({
  children,
  persona,
  profile,
}: {
  children: ReactNode;
  persona: PersonaId;
  profile: EmployeeProfile | null;
}) {
  const name = profile?.full_name ?? PERSONAS[persona].fullName;
  return (
    <div className="portal-frame">
      <aside className="portal-sidebar">
        <Link className="wordmark" href="/" aria-label="Northstar employee portal home">
          <span className="wordmark-mark">N</span>
          <span>
            <strong>Northstar</strong>
            <small>Employee portal</small>
          </span>
        </Link>
        <SidebarNav />
        <div className="sidebar-trust">
          <ShieldCheck aria-hidden="true" size={17} />
          <span>
            <strong>Governed actions</strong>
            <small>Human authorization required</small>
          </span>
        </div>
        <IdentitySwitcher current={persona} />
      </aside>
      <div className="portal-workspace">
        <header className="mobile-header">
          <Link className="mobile-wordmark" href="/">
            Northstar
          </Link>
          <span>{name}</span>
        </header>
        <main id="main-content">{children}</main>
      </div>
    </div>
  );
}

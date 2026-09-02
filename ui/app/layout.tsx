import type { Metadata } from "next";
import type { ReactNode } from "react";

import { PortalShell } from "@/components/portal-shell";
import { backendFetch } from "@/lib/backend";
import type { EmployeeProfile } from "@/lib/contracts";
import { getServerPersona } from "@/lib/server-persona";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Northstar Employee Portal", template: "%s | Northstar" },
  description: "Trusted employee knowledge and action portal.",
};

export default async function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  const persona = await getServerPersona();
  let profile: EmployeeProfile | null = null;
  try {
    profile = await backendFetch<EmployeeProfile>("/me/profile");
  } catch {
    // Keep navigation available so connection guidance can render in-page.
  }
  return (
    <html lang="en-AU" data-scroll-behavior="smooth">
      <body>
        <a className="skip-link" href="#main-content">
          Skip to content
        </a>
        <PortalShell persona={persona} profile={profile}>
          {children}
        </PortalShell>
      </body>
    </html>
  );
}

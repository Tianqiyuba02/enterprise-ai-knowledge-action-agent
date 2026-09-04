import "server-only";

import { cookies } from "next/headers";

import type { PersonaId } from "@/lib/contracts";
import { isPersonaId } from "@/lib/personas";

export const PERSONA_COOKIE = "enterprise-portal-persona";

const DEMO_TOKENS: Record<PersonaId, string> = {
  alex: "demo-v1-7f4c2a91",
  sam: "demo-v1-3b8e6d50",
};

export async function getServerPersona(): Promise<PersonaId> {
  const value = (await cookies()).get(PERSONA_COOKIE)?.value;
  return isPersonaId(value) ? value : "alex";
}

export function getDemoToken(persona: PersonaId): string {
  return DEMO_TOKENS[persona];
}

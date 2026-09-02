import type { PersonaId } from "@/lib/contracts";

export const PERSONAS: Record<
  PersonaId,
  { id: PersonaId; fullName: string; initials: string; descriptor: string }
> = {
  alex: {
    id: "alex",
    fullName: "Alex Morgan",
    initials: "AM",
    descriptor: "Permanent · Melbourne",
  },
  sam: {
    id: "sam",
    fullName: "Sam Lee",
    initials: "SL",
    descriptor: "Part-time · Melbourne",
  },
};

export function isPersonaId(value: unknown): value is PersonaId {
  return value === "alex" || value === "sam";
}

import { NextResponse } from "next/server";

import { isPersonaId } from "@/lib/personas";
import { PERSONA_COOKIE } from "@/lib/server-persona";

export async function POST(request: Request): Promise<NextResponse> {
  const payload: unknown = await request.json().catch(() => null);
  const persona = (payload as { persona?: unknown } | null)?.persona;
  if (!isPersonaId(persona)) {
    return NextResponse.json({ message: "Unknown demo identity." }, { status: 422 });
  }

  const response = NextResponse.json({ persona });
  response.cookies.set({
    name: PERSONA_COOKIE,
    value: persona,
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 8,
  });
  return response;
}

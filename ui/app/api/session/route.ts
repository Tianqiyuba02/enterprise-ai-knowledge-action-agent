import { NextResponse } from "next/server";

import { isPersonaId } from "@/lib/personas";
import { PERSONA_COOKIE } from "@/lib/server-persona";
import { originIsSameSite } from "@/lib/visitor";

export async function POST(request: Request): Promise<NextResponse> {
  if (!originIsSameSite(request)) {
    return NextResponse.json({ message: "Request origin is not allowed." }, { status: 403 });
  }
  const body = await request.text();
  if (new TextEncoder().encode(body).byteLength > 2_048) {
    return NextResponse.json({ message: "Request is too large." }, { status: 413 });
  }
  let payload: unknown = null;
  try {
    payload = JSON.parse(body);
  } catch {
    payload = null;
  }
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

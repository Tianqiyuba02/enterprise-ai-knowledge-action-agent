import "server-only";

import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";

import type { NextResponse } from "next/server";

export const VISITOR_COOKIE = "northstar-demo-visitor";

function secret(): string {
  const value = process.env.VISITOR_COOKIE_SECRET ?? "local-development-cookie-secret-only";
  if (process.env.NODE_ENV === "production" && value.length < 24) {
    throw new Error("Visitor cookie signing is not configured.");
  }
  return value;
}

function signature(visitorId: string): string {
  return createHmac("sha256", secret()).update(visitorId).digest("base64url");
}

export function validVisitorId(cookieValue: string | undefined): string | null {
  if (!cookieValue) return null;
  const [visitorId, supplied, extra] = cookieValue.split(".");
  if (!visitorId || !supplied || extra || !/^[0-9a-f-]{36}$/.test(visitorId)) return null;
  const expected = signature(visitorId);
  const left = Buffer.from(supplied);
  const right = Buffer.from(expected);
  if (left.length !== right.length || !timingSafeEqual(left, right)) return null;
  return visitorId;
}

export function newVisitor(): { id: string; value: string } {
  const id = randomUUID();
  return { id, value: `${id}.${signature(id)}` };
}

export function setVisitorCookie(response: NextResponse, value: string): void {
  response.cookies.set({
    name: VISITOR_COOKIE,
    value,
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24,
  });
}

export function originIsSameSite(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return process.env.NODE_ENV !== "production";
  try {
    const forwardedHost = request.headers.get("x-forwarded-host")?.split(",")[0]?.trim();
    const host = forwardedHost || request.headers.get("host");
    const forwardedProtocol = request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim();
    const protocol = forwardedProtocol || new URL(request.url).protocol.replace(":", "");
    if (!host || !/^[a-z0-9.:[\]-]+$/i.test(host) || !/^(https?)$/.test(protocol)) return false;
    return new URL(origin).origin === `${protocol}://${host}`;
  } catch {
    return false;
  }
}

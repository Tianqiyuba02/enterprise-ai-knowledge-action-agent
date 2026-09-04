import { NextResponse } from "next/server";
import { cookies } from "next/headers";

import { backendFetch, PortalApiError } from "@/lib/backend";
import {
  newVisitor,
  originIsSameSite,
  setVisitorCookie,
  validVisitorId,
  VISITOR_COOKIE,
} from "@/lib/visitor";

const UUID = "[0-9a-fA-F-]{36}";
const SAFE_SEGMENT = "[A-Za-z0-9._-]+";

const GET_ROUTES = [
  /^me\/profile$/,
  /^me\/leave\/summary$/,
  /^me\/actions$/,
  /^me\/tickets$/,
  new RegExp(`^actions\\/${UUID}\\/detail$`),
  /^knowledge\/documents$/,
  /^demo\/readiness$/,
  /^demo\/guided-scenarios$/,
  new RegExp(`^knowledge\\/documents\\/${SAFE_SEGMENT}\\/versions\\/${SAFE_SEGMENT}$`),
];

const POST_ROUTES = [
  /^assistant\/query$/,
  new RegExp(`^actions\\/${UUID}\\/confirmation-challenges$`),
  new RegExp(`^actions\\/${UUID}\\/confirm$`),
  new RegExp(`^actions\\/${UUID}\\/cancel$`),
  new RegExp(`^actions\\/${UUID}\\/revisions$`),
];

type RouteContext = { params: Promise<{ path: string[] }> };

function allowed(path: string, patterns: RegExp[]): boolean {
  return patterns.some((pattern) => pattern.test(path));
}

function errorResponse(error: unknown): NextResponse {
  if (error instanceof PortalApiError) {
    return NextResponse.json(
      {
        error_code: error.errorCode,
        message: error.message,
        request_id: error.requestId,
      },
      { status: error.status },
    );
  }
  return NextResponse.json(
    { error_code: "portal_gateway_error", message: "The portal gateway is unavailable." },
    { status: 502 },
  );
}

export async function GET(request: Request, context: RouteContext): Promise<NextResponse> {
  const { path: segments } = await context.params;
  const path = segments.join("/");
  if (!allowed(path, GET_ROUTES)) {
    return NextResponse.json({ message: "Route not available." }, { status: 404 });
  }
  const incomingUrl = new URL(request.url);
  const limit = incomingUrl.searchParams.get("limit");
  const query = path === "me/actions" && limit ? `?limit=${encodeURIComponent(limit)}` : "";
  try {
    const payload = await backendFetch<unknown>(`/${path}${query}` as `/${string}`);
    return NextResponse.json(payload);
  } catch (error) {
    return errorResponse(error);
  }
}

export async function POST(request: Request, context: RouteContext): Promise<NextResponse> {
  const { path: segments } = await context.params;
  const path = segments.join("/");
  if (!allowed(path, POST_ROUTES)) {
    return NextResponse.json({ message: "Route not available." }, { status: 404 });
  }
  if (!originIsSameSite(request)) {
    return NextResponse.json({ message: "Request origin is not allowed." }, { status: 403 });
  }
  const contentLength = Number(request.headers.get("content-length") ?? 0);
  if (contentLength > 16_384) {
    return NextResponse.json({ message: "Request is too large." }, { status: 413 });
  }
  const body = await request.text();
  if (new TextEncoder().encode(body).byteLength > 16_384) {
    return NextResponse.json({ message: "Request is too large." }, { status: 413 });
  }
  const existing = validVisitorId((await cookies()).get(VISITOR_COOKIE)?.value);
  const visitor = existing ? { id: existing, value: null } : { ...newVisitor() };
  try {
    const payload = await backendFetch<unknown>(`/${path}`, {
      method: "POST",
      body: body || undefined,
      headers: { "X-Demo-Visitor-ID": visitor.id },
      signal: AbortSignal.timeout(path === "assistant/query" ? 55_000 : 20_000),
    });
    const response = NextResponse.json(payload);
    if (visitor.value) setVisitorCookie(response, visitor.value);
    return response;
  } catch (error) {
    const response = errorResponse(error);
    if (visitor.value) setVisitorCookie(response, visitor.value);
    return response;
  }
}

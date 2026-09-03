import "server-only";

import { getDemoToken, getServerPersona } from "@/lib/server-persona";

const API_ROOT = "/api/v1";

export class PortalApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly errorCode = "portal_request_failed",
    public readonly requestId: string | null = null,
  ) {
    super(message);
  }
}

function backendUrl(): string {
  const configured = process.env.BACKEND_URL ?? process.env.BACKEND_HOSTPORT;
  const value = configured ?? "http://127.0.0.1:8000";
  return `${/^https?:\/\//.test(value) ? "" : "http://"}${value}`.replace(/\/$/, "");
}

export async function backendFetch<T>(
  path: `/${string}`,
  init?: Omit<RequestInit, "headers"> & { headers?: HeadersInit },
): Promise<T> {
  const persona = await getServerPersona();
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  headers.set("Content-Type", "application/json");
  headers.set("X-Demo-Session", getDemoToken(persona));
  const internalKey = process.env.INTERNAL_PORTAL_KEY;
  if (internalKey) headers.set("X-Internal-Portal-Key", internalKey);
  const response = await fetch(`${backendUrl()}${API_ROOT}${path}`, {
    ...init,
    cache: "no-store",
    headers,
    signal: init?.signal ?? AbortSignal.timeout(20_000),
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = payload as Partial<{
      message: string;
      error_code: string;
      request_id: string;
    }> | null;
    throw new PortalApiError(
      error?.message ?? "The employee portal could not load this information.",
      response.status,
      error?.error_code,
      error?.request_id ?? response.headers.get("x-request-id"),
    );
  }
  return payload as T;
}

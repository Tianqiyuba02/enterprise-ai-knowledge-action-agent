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
  return (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
}

export async function backendFetch<T>(
  path: `/${string}`,
  init?: Omit<RequestInit, "headers"> & { headers?: HeadersInit },
): Promise<T> {
  const persona = await getServerPersona();
  const response = await fetch(`${backendUrl()}${API_ROOT}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Demo-Session": getDemoToken(persona),
      ...init?.headers,
    },
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

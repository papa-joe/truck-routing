import type { Place, Plan, PlanRequest } from "./types";

export class ApiError extends Error {
  readonly fieldErrors: Record<string, string[]>;

  constructor(message: string, fieldErrors: Record<string, string[]> = {}) {
    super(message);
    this.name = "ApiError";
    this.fieldErrors = fieldErrors;
  }
}

interface ErrorEnvelope {
  error?: {
    message?: string;
    fields?: Record<string, string[]>;
  };
}

async function readJson<T>(response: Response): Promise<T> {
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const envelope = body as ErrorEnvelope | null;
    throw new ApiError(
      envelope?.error?.message ?? `Request failed (${response.status}).`,
      envelope?.error?.fields ?? {},
    );
  }
  return body as T;
}

export async function requestPlan(payload: PlanRequest): Promise<Plan> {
  const response = await fetch("/api/plan/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson<Plan>(response);
}

export async function searchPlaces(query: string, signal?: AbortSignal): Promise<Place[]> {
  if (query.trim().length < 2) return [];
  const response = await fetch(`/api/geocode/?q=${encodeURIComponent(query)}`, { signal });
  const body = await readJson<{ results: Place[] }>(response);
  return body.results;
}

import type { ZodType } from "zod";
import {
  type EventsResponse,
  eventsResponseSchema,
  type ReplayResponse,
  replayResponseSchema,
} from "./types";

async function fetchValidated<T>(
  path: string,
  schema: ZodType<T>,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    throw new Error(`AtlasPulse API returned HTTP ${response.status}`);
  }
  return schema.parse(await response.json());
}

export function fetchLatest(signal?: AbortSignal): Promise<EventsResponse> {
  return fetchValidated("/api/v1/events?limit=500", eventsResponseSchema, signal);
}

export function fetchReplay(after?: string, signal?: AbortSignal): Promise<ReplayResponse> {
  const search = new URLSearchParams({ limit: "500" });
  if (after) search.set("after", after);
  return fetchValidated(`/api/v1/events/replay?${search}`, replayResponseSchema, signal);
}

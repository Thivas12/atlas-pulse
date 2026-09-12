import type { ZodType } from "zod";
import {
  type EventsResponse,
  eventsResponseSchema,
  type IncidentsResponse,
  incidentsResponseSchema,
  type ReplayResponse,
  replayResponseSchema,
  type SignalsResponse,
  signalsResponseSchema,
  type ViewportBounds,
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

interface CurrentSignalsQuery {
  source?: "usgs" | "nws" | "firms" | "gdelt";
  bounds?: ViewportBounds;
  includeAreaOnly?: boolean;
}

export function fetchCurrentSignals(
  query: CurrentSignalsQuery,
  signal?: AbortSignal,
): Promise<SignalsResponse> {
  const search = new URLSearchParams({ limit: "500", active_only: "true" });
  if (query.source) search.set("source", query.source);
  if (query.bounds) {
    const { west, south, east, north } = query.bounds;
    search.set("bbox", [west, south, east, north].join(","));
  }
  if (query.includeAreaOnly) search.set("include_area_only", "true");
  return fetchValidated(`/api/v1/signals?${search}`, signalsResponseSchema, signal);
}

export function fetchIncidentCandidates(
  bounds?: ViewportBounds,
  signal?: AbortSignal,
): Promise<IncidentsResponse> {
  const search = new URLSearchParams({ limit: "100" });
  if (bounds) {
    const { west, south, east, north } = bounds;
    search.set("bbox", [west, south, east, north].join(","));
  }
  return fetchValidated(`/api/v1/incidents?${search}`, incidentsResponseSchema, signal);
}

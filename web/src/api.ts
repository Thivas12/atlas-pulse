import type { ZodType } from "zod";
import {
  type AgentRunPreflightResponse,
  agentRunPreflightResponseSchema,
  type EventsResponse,
  type EvidencePack,
  eventsResponseSchema,
  evidencePackResponseSchema,
  type IncidentsResponse,
  incidentsResponseSchema,
  type ReplayResponse,
  replayResponseSchema,
  type SearchResponse,
  type SignalsResponse,
  type SourceFreshnessResponse,
  type SourcePollHistoryResponse,
  searchResponseSchema,
  signalsResponseSchema,
  sourceFreshnessResponseSchema,
  sourcePollHistoryResponseSchema,
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

export function fetchSourceFreshness(signal?: AbortSignal): Promise<SourceFreshnessResponse> {
  return fetchValidated("/api/v1/source-freshness", sourceFreshnessResponseSchema, signal);
}

export function fetchSourcePollHistory(signal?: AbortSignal): Promise<SourcePollHistoryResponse> {
  return fetchValidated("/api/v1/source-polls?limit=12", sourcePollHistoryResponseSchema, signal);
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

export interface HybridSearchQuery {
  query: string;
  source?: "usgs" | "nws" | "firms" | "gdelt";
  bounds?: ViewportBounds;
  minMagnitude?: number;
  maxDepthKm?: number;
  tsunami?: boolean;
  alertType?: string;
  minConfidenceRank?: 1 | 2 | 3;
  observationPeriod?: "day" | "night";
}

function appendStructuredSearchFilters(search: URLSearchParams, query: HybridSearchQuery): void {
  if (query.minMagnitude !== undefined) search.set("min_magnitude", String(query.minMagnitude));
  if (query.maxDepthKm !== undefined) search.set("max_depth_km", String(query.maxDepthKm));
  if (query.tsunami !== undefined) search.set("tsunami", String(query.tsunami));
  if (query.alertType !== undefined) search.set("alert_type", query.alertType);
  if (query.minConfidenceRank !== undefined) {
    search.set("min_confidence_rank", String(query.minConfidenceRank));
  }
  if (query.observationPeriod !== undefined) {
    search.set("observation_period", query.observationPeriod);
  }
}

export function fetchHybridSearch(
  query: HybridSearchQuery,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const search = new URLSearchParams({
    q: query.query,
    limit: "20",
    candidate_limit: "100",
    active_only: "true",
  });
  if (query.source) search.set("source", query.source);
  if (query.bounds) {
    const { west, south, east, north } = query.bounds;
    search.set("bbox", [west, south, east, north].join(","));
  }
  appendStructuredSearchFilters(search, query);
  return fetchValidated(`/api/v1/search?${search}`, searchResponseSchema, signal);
}

export function fetchEvidencePack(
  query: HybridSearchQuery,
  signal?: AbortSignal,
): Promise<EvidencePack> {
  const search = new URLSearchParams({
    q: query.query,
    retrieval_limit: "20",
    candidate_limit: "100",
    max_items: "8",
    max_characters_per_item: "2000",
    max_total_characters: "12000",
    active_only: "true",
  });
  if (query.source) search.set("source", query.source);
  if (query.bounds) {
    const { west, south, east, north } = query.bounds;
    search.set("bbox", [west, south, east, north].join(","));
  }
  appendStructuredSearchFilters(search, query);
  return fetchValidated(`/api/v1/evidence-packs?${search}`, evidencePackResponseSchema, signal);
}

export function fetchAgentRunPreflight(
  query: HybridSearchQuery,
  signal?: AbortSignal,
): Promise<AgentRunPreflightResponse> {
  const search = new URLSearchParams({
    q: query.query,
    retrieval_limit: "20",
    candidate_limit: "100",
    max_items: "8",
    max_characters_per_item: "2000",
    max_total_characters: "12000",
    active_only: "true",
  });
  if (query.source) search.set("source", query.source);
  if (query.bounds) {
    const { west, south, east, north } = query.bounds;
    search.set("bbox", [west, south, east, north].join(","));
  }
  appendStructuredSearchFilters(search, query);
  return fetchValidated(
    `/api/v1/agent-runs/preflight?${search}`,
    agentRunPreflightResponseSchema,
    signal,
  );
}

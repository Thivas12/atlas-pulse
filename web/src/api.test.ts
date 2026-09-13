import { describe, expect, it, vi } from "vitest";
import {
  fetchCurrentSignals,
  fetchHybridSearch,
  fetchIncidentCandidates,
  fetchLatest,
  fetchReplay,
} from "./api";
import { makeEnvelope, makeIncident } from "./test/fixtures";

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AtlasPulse API client", () => {
  it("validates the latest event response", async () => {
    const body = { count: 1, items: [makeEnvelope()] };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchLatest()).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/events?limit=500",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
  });

  it("passes an exclusive replay cursor and validates ordering metadata", async () => {
    const body = {
      count: 1,
      items: [makeEnvelope()],
      next_cursor: "1000-0",
      has_more: true,
      order: "oldest_first" as const,
    };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchReplay("999-0")).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/events/replay?limit=500&after=999-0",
      expect.any(Object),
    );
  });

  it("encodes current-state source and viewport filters", async () => {
    const body = {
      count: 1,
      items: [makeEnvelope()],
      next_cursor: "1000-0",
      has_more: false,
      order: "newest_revision_first" as const,
    };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchCurrentSignals({
        source: "gdelt",
        bounds: { west: -10, south: -5, east: 20, north: 30 },
      }),
    ).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/signals?limit=500&active_only=true&source=gdelt&bbox=-10%2C-5%2C20%2C30",
      expect.any(Object),
    );
  });

  it("validates bounded incident candidates and encodes viewport bounds", async () => {
    const body = {
      count: 1,
      total_incidents: 1,
      items: [makeIncident()],
      incidents_truncated: false,
      candidate_edges_truncated: false,
      rule_version: "spatiotemporal-v1",
      caveat:
        "Edges prove bounded spatial and temporal co-occurrence only; they do not establish causation, corroboration, or a shared real-world incident.",
      relationship_rule_version: "structured-claims-v1",
      relationship_caveat:
        "Annotations compare normalized source claims attached to measured edges. Corroboration is agreement at the named predicate and scope, not proof of truth or a shared incident; contradiction is a review flag, not adjudication. Insufficient evidence is not disagreement.",
      parameters: {
        radius_km: 50,
        time_window_minutes: 360,
        lookback_hours: 24,
        candidate_edge_limit: 2_000,
        incident_limit: 100,
        active_only: true,
        bbox: [-10, -5, 20, 30],
      },
    };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchIncidentCandidates({ west: -10, south: -5, east: 20, north: 30 }),
    ).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/incidents?limit=100&bbox=-10%2C-5%2C20%2C30",
      expect.any(Object),
    );
  });

  it("validates hybrid ranks and encodes source plus viewport filters", async () => {
    const envelope = makeEnvelope({ source: "nws", eventId: "alert-1" });
    const body = {
      count: 1,
      candidates_considered: 3,
      items: [
        {
          ...envelope,
          document_text: "Title: Severe thunderstorm warning",
          distance_km: null,
          ranking: {
            lexical_rank: 1,
            lexical_score: 0.8,
            dense_rank: 2,
            dense_similarity: 0.91,
            rrf_score: 0.99,
            exact_phrase_match: true,
            token_coverage: 1,
            rerank_score: 0.99,
          },
          citation: {
            status: "traceable" as const,
            url: "https://api.weather.gov/alerts/alert-1",
            source_field: "source_url",
            reasons: ["public_http_url"],
          },
        },
      ],
      embedding_model: "BAAI/bge-small-en-v1.5",
      ranking_mode: "hybrid" as const,
      ranking_rule: "rrf60-evidence-tiebreak-v2",
      caveat: "Ranked evidence only; no generated answer.",
      parameters: {
        query: "dangerous storm",
        limit: 20,
        candidate_limit: 100,
        source: "nws" as const,
        occurred_after: null,
        occurred_before: null,
        active_only: true,
        bbox: [-10, -5, 20, 30] as [number, number, number, number],
        near: null,
        radius_km: null,
        ranking_mode: "hybrid" as const,
      },
    };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchHybridSearch({
        query: "dangerous storm",
        source: "nws",
        bounds: { west: -10, south: -5, east: 20, north: 30 },
      }),
    ).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/search?q=dangerous+storm&limit=20&candidate_limit=100&active_only=true&source=nws&bbox=-10%2C-5%2C20%2C30",
      expect.any(Object),
    );
  });

  it("surfaces HTTP failures", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({}, 503)));
    await expect(fetchLatest()).rejects.toThrow("AtlasPulse API returned HTTP 503");
  });

  it("rejects a response that violates the browser boundary schema", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse({
          count: 1,
          items: [{ ...makeEnvelope(), stream_id: "not-a-stream-id" }],
        }),
      ),
    );
    await expect(fetchLatest()).rejects.toThrow();
  });
});

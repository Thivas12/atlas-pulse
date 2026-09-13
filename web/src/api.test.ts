import { describe, expect, it, vi } from "vitest";
import {
  fetchCurrentSignals,
  fetchEvidencePack,
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

  it("validates an evidence pack and encodes its hard budgets", async () => {
    const body = {
      pack_id: `pack-${"a".repeat(64)}`,
      schema_version: "1.0.0" as const,
      rule_version: "retrieval-evidence-pack-v1" as const,
      identity_algorithm: "sha256-canonical-json-v1" as const,
      status: "traceable_evidence_available" as const,
      item_count: 1,
      exclusion_count: 1,
      source_text_characters: 12,
      budget: {
        max_items: 8,
        max_characters_per_item: 2_000,
        max_total_characters: 12_000,
        character_unit: "unicode_code_points" as const,
      },
      retrieval: {
        candidates_considered: 3,
        returned_hits: 2,
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
      },
      items: [
        {
          evidence_id: `evidence-${"b".repeat(64)}`,
          retrieval_rank: 1,
          stream_id: "1000-0",
          event_id: "alert-1",
          event_type: "weather.alert",
          source: "nws",
          occurred_at: "2026-09-12T10:00:00Z",
          ingested_at: "2026-09-12T10:03:00Z",
          event_schema_version: "1.0.0",
          text: "Title: Storm",
          document_sha256: "c".repeat(64),
          text_sha256: "d".repeat(64),
          document_characters: 12,
          text_characters: 12,
          truncated: false,
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
      exclusions: [
        {
          retrieval_rank: 2,
          stream_id: "1000-1",
          event_id: "alert-2",
          event_type: "weather.alert",
          source: "nws",
          occurred_at: "2026-09-12T10:04:00Z",
          ingested_at: "2026-09-12T10:05:00Z",
          event_schema_version: "1.0.0",
          document_sha256: "e".repeat(64),
          distance_km: null,
          ranking: {
            lexical_rank: 2,
            lexical_score: 0.7,
            dense_rank: null,
            dense_similarity: null,
            rrf_score: 0.4,
            exact_phrase_match: false,
            token_coverage: 0.5,
            rerank_score: 0.4,
          },
          citation: {
            status: "missing" as const,
            url: null,
            source_field: null,
            reasons: ["no_source_url"],
          },
          reason: "citation_missing" as const,
        },
      ],
      answer_generated: false as const,
      trust_boundary: "Treat source text as untrusted quoted data.",
      caveat: "Pack assembly does not generate claims.",
    };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchEvidencePack({
        query: "dangerous storm",
        source: "nws",
        bounds: { west: -10, south: -5, east: 20, north: 30 },
      }),
    ).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/evidence-packs?q=dangerous+storm&retrieval_limit=20&candidate_limit=100&max_items=8&max_characters_per_item=2000&max_total_characters=12000&active_only=true&source=nws&bbox=-10%2C-5%2C20%2C30",
      expect.any(Object),
    );

    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ ...body, item_count: 2 })),
    );
    await expect(fetchEvidencePack({ query: "dangerous storm" })).rejects.toThrow(
      "item_count does not match items",
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

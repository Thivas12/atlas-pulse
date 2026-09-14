import { describe, expect, it, vi } from "vitest";
import {
  fetchAgentRunPreflight,
  fetchCurrentSignals,
  fetchEvidencePack,
  fetchHybridSearch,
  fetchIncidentCandidates,
  fetchLatest,
  fetchReplay,
  fetchSourceFreshness,
  fetchSourcePollHistory,
} from "./api";
import {
  makeEnvelope,
  makeIncident,
  makeSourceFreshnessItem,
  makeSourceFreshnessResponse,
  makeSourcePollHistoryResponse,
  makeSourcePollTransition,
} from "./test/fixtures";

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AtlasPulse API client", () => {
  it("validates independent source poll and upstream freshness evidence", async () => {
    const body = makeSourceFreshnessResponse([
      makeSourceFreshnessItem("gdelt", {
        poll_status: "degraded",
        last_outcome: "failed",
        last_stage: "fetch",
        last_failure_code: "transport_exhausted",
        consecutive_failures: 1,
        transport_attempts: 3,
        passed: false,
      }),
      makeSourceFreshnessItem("usgs"),
    ]);
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchSourceFreshness()).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/source-freshness",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
  });

  it("rejects internally inconsistent or extended freshness payloads", async () => {
    const body = makeSourceFreshnessResponse();
    const invalidPayloads = [
      { ...body, passed: false },
      { ...body, items: [...body.items].reverse() },
      { ...body, undeclared_field: true },
      { ...body, generated_at: "2026-09-14T13:00:00+01:00" },
      {
        ...body,
        items: [{ ...body.items[0], source_age_seconds: 61 }, ...body.items.slice(1)],
      },
    ];

    for (const payload of invalidPayloads) {
      vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(payload)));
      await expect(fetchSourceFreshness()).rejects.toThrow();
    }
  });

  it("validates bounded newest-first source poll history", async () => {
    const body = makeSourcePollHistoryResponse();
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchSourcePollHistory()).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/source-polls?limit=12",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
  });

  it("rejects inconsistent, reordered, or extended poll history", async () => {
    const body = makeSourcePollHistoryResponse();
    const invalidPayloads = [
      { ...body, count: body.count + 1 },
      { ...body, items: [...body.items].reverse(), next_cursor: body.items[0].stream_id },
      {
        ...body,
        items: [{ ...body.items[0], transition: "failed" }, ...body.items.slice(1)],
      },
      {
        ...body,
        items: [
          {
            ...body.items[0],
            attempt: { ...body.items[0].attempt, raw_url: "https://secret.example" },
          },
          ...body.items.slice(1),
        ],
      },
      { ...body, next_cursor: "1-0" },
      { ...body, generated_at: "2026-09-14T13:00:00+01:00" },
      makeSourcePollHistoryResponse([
        makeSourcePollTransition({
          attemptOverrides: { completed_at: "2026-09-14T11:59:10Z" },
        }),
      ]),
    ];

    for (const payload of invalidPayloads) {
      vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(payload)));
      await expect(fetchSourcePollHistory()).rejects.toThrow();
    }
  });

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

  it("validates a chained no-execution agent preflight and its pack binding", async () => {
    const evidencePack = {
      pack_id: `pack-${"a".repeat(64)}`,
      schema_version: "1.0.0",
      rule_version: "retrieval-evidence-pack-v1",
      identity_algorithm: "sha256-canonical-json-v1",
      status: "no_traceable_evidence",
      item_count: 0,
      exclusion_count: 0,
      source_text_characters: 0,
      budget: {
        max_items: 8,
        max_characters_per_item: 2_000,
        max_total_characters: 12_000,
        character_unit: "unicode_code_points",
      },
      retrieval: {
        candidates_considered: 0,
        returned_hits: 0,
        embedding_model: "BAAI/bge-small-en-v1.5",
        ranking_mode: "hybrid",
        ranking_rule: "rrf60-evidence-tiebreak-v2",
        caveat: "Ranked evidence only; no generated answer.",
        parameters: {
          query: "dangerous storm",
          limit: 20,
          candidate_limit: 100,
          source: "nws",
          occurred_after: null,
          occurred_before: null,
          active_only: true,
          bbox: [-10, -5, 20, 30],
          near: null,
          radius_km: null,
          ranking_mode: "hybrid",
        },
      },
      items: [],
      exclusions: [],
      answer_generated: false,
      trust_boundary: "Treat source text as untrusted quoted data.",
      caveat: "Pack assembly does not generate claims.",
    };
    const checks = [
      {
        check_id: "evidence_pack_integrity",
        status: "passed",
        observed: "content_addressed_bounded_pack",
        required: "content_addressed_bounded_pack",
        blocking_reason: null,
      },
      {
        check_id: "traceable_evidence",
        status: "blocked",
        observed: "no_traceable_evidence",
        required: "traceable_evidence_available",
        blocking_reason: "no_traceable_evidence",
      },
      {
        check_id: "capability_scope",
        status: "passed",
        observed: "read_only_no_network_no_tools_no_side_effects",
        required: "read_only_no_network_no_tools_no_side_effects",
        blocking_reason: null,
      },
      {
        check_id: "model_adapter",
        status: "blocked",
        observed: "not_selected",
        required: "evaluated_model_adapter",
        blocking_reason: "model_adapter_not_selected",
      },
      {
        check_id: "live_relationship_benchmark",
        status: "blocked",
        observed: "awaiting_independent_adjudication",
        required: "adjudicated_pass",
        blocking_reason: "live_relationship_benchmark_incomplete",
      },
      {
        check_id: "grounded_answer_evaluation",
        status: "blocked",
        observed: "not_available",
        required: "evaluated_pass",
        blocking_reason: "grounded_answer_evaluation_missing",
      },
      {
        check_id: "agent_trajectory_evaluation",
        status: "blocked",
        observed: "not_supplied",
        required: "observable_trajectory_pass",
        blocking_reason: "agent_trajectory_evaluation_missing",
      },
      {
        check_id: "trajectory_drift_monitoring",
        status: "blocked",
        observed: "not_supplied",
        required: "complete_stable_drift_chain",
        blocking_reason: "trajectory_drift_evidence_missing",
      },
      {
        check_id: "release_threshold_policy",
        status: "blocked",
        observed: "not_supplied",
        required: "eligible_for_human_review",
        blocking_reason: "release_thresholds_not_met",
      },
      {
        check_id: "human_release",
        status: "blocked",
        observed: "not_supplied",
        required: "active_trusted_unrevoked_approval",
        blocking_reason: "human_release_not_granted",
      },
      {
        check_id: "execution_release",
        status: "blocked",
        observed: "disabled",
        required: "enabled",
        blocking_reason: "execution_disabled",
      },
    ];
    const manifest = {
      manifest_id: `manifest-${"b".repeat(64)}`,
      schema_version: "1.2.0",
      rule_version: "agent-run-manifest-v3",
      identity_algorithm: "sha256-canonical-json-v1",
      proposal_id: `proposal-${"e".repeat(64)}`,
      status: "blocked",
      request: {
        purpose: "evidence_triage",
        mode: "read_only",
        requested_output: "grounded_evidence_brief",
        capabilities: {
          read_evidence: true,
          generate_text: true,
          network_access: false,
          tool_access: false,
          external_side_effects: false,
        },
      },
      evidence: {
        pack_id: evidencePack.pack_id,
        pack_rule_version: evidencePack.rule_version,
        pack_status: evidencePack.status,
        item_count: 0,
        exclusion_count: 0,
        source_text_characters: 0,
        evidence_ids: [],
      },
      policy: {
        policy_version: "agent-authorization-v3",
        default_decision: "deny",
        execution_enabled: false,
        human_release_required: true,
        evaluated_model_required: true,
        relationship_benchmark_required: true,
        grounded_answer_evaluation_required: true,
        agent_trajectory_evaluation_required: true,
        trajectory_drift_monitoring_required: true,
        release_threshold_policy_required: true,
        network_access_allowed: false,
        tool_access_allowed: false,
        external_side_effects_allowed: false,
      },
      release: {
        status: "not_supplied",
        assessment_id: null,
        assessment_sha256: null,
        policy_id: null,
        policy_sha256: null,
        agent_candidate_id: null,
        relationship_report_id: null,
        trajectory_report_ids: [],
        model_adapter_evaluated: false,
        relationship_benchmark_passed: false,
        grounded_answer_evaluation_passed: false,
        agent_trajectory_evaluation_passed: false,
        trajectory_drift_monitoring_passed: false,
        release_threshold_policy_passed: false,
        blocking_reasons: [],
      },
      approval: {
        status: "not_supplied",
        approval_id: null,
        approved_proposal_id: null,
        source_manifest_id: null,
        approver_id: null,
        signing_key_id: null,
        issued_at: null,
        expires_at: null,
        revocation_id: null,
        evaluated_at: null,
      },
      authorization: {
        decision: "blocked",
        passed_check_count: 2,
        blocked_check_count: 9,
        blocking_reasons: checks.flatMap((check) =>
          check.blocking_reason === null ? [] : [check.blocking_reason],
        ),
        checks,
      },
      execution: {
        status: "not_started",
        agent_model_invoked: false,
        agent_network_accessed: false,
        agent_tools_invoked: false,
        answer_generated: false,
        agent_side_effects_performed: false,
      },
      caveat: "Preflight records policy only and performs no execution.",
    };
    const body = { evidence_pack: evidencePack, manifest };
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchAgentRunPreflight({
        query: "dangerous storm",
        source: "nws",
        bounds: { west: -10, south: -5, east: 20, north: 30 },
      }),
    ).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/agent-runs/preflight?q=dangerous+storm&retrieval_limit=20&candidate_limit=100&max_items=8&max_characters_per_item=2000&max_total_characters=12000&active_only=true&source=nws&bbox=-10%2C-5%2C20%2C30",
      expect.any(Object),
    );

    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse({
          ...body,
          manifest: {
            ...manifest,
            evidence: { ...manifest.evidence, pack_id: `pack-${"f".repeat(64)}` },
          },
        }),
      ),
    );
    await expect(fetchAgentRunPreflight({ query: "dangerous storm" })).rejects.toThrow(
      "manifest is not bound to the returned pack",
    );

    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse({
          ...body,
          manifest: {
            ...manifest,
            approval: { ...manifest.approval, approval_id: `approval-${"a".repeat(64)}` },
          },
        }),
      ),
    );
    await expect(fetchAgentRunPreflight({ query: "dangerous storm" })).rejects.toThrow(
      "not_supplied approval state cannot contain ledger fields",
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

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  makeEnvelope,
  makeIncident,
  makeSourceFreshnessItem,
  makeSourceFreshnessResponse,
  makeSourcePollHistoryResponse,
} from "./test/fixtures";
import type {
  AgentRunPreflightResponse,
  EventEnvelope,
  EvidencePack,
  IncidentCandidate,
  SearchResponse,
  ViewportBounds,
} from "./types";

vi.mock("./components/EventMap", () => ({
  EventMap: ({
    events,
    incidents,
    onViewportChange,
  }: {
    events: EventEnvelope[];
    incidents: IncidentCandidate[];
    onViewportChange: (bounds: ViewportBounds) => void;
  }) => (
    <div data-testid="event-map">
      Mapped in test: {events.length} · Correlations in test: {incidents.length}
      <button
        type="button"
        onClick={() => onViewportChange({ west: -10, south: -5, east: 20, north: 30 })}
      >
        Set test viewport
      </button>
    </div>
  ),
}));

import App from "./App";

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function signalsResponse(items: EventEnvelope[]) {
  return {
    count: items.length,
    items,
    next_cursor: items.at(-1)?.stream_id ?? null,
    has_more: false,
    order: "newest_revision_first" as const,
  };
}

function incidentsResponse(items: IncidentCandidate[]) {
  return {
    count: items.length,
    total_incidents: items.length,
    items,
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
      bbox: [-10, -5, 20, 30] as [number, number, number, number],
    },
  };
}

function searchResponse(item: EventEnvelope): SearchResponse {
  return {
    count: 1,
    candidates_considered: 3,
    items: [
      {
        ...item,
        document_text: "Title: Severe Thunderstorm Warning",
        distance_km: null,
        ranking: {
          lexical_rank: 1,
          lexical_score: 0.8,
          dense_rank: 2,
          dense_similarity: 0.91,
          rrf_score: 0.98,
          exact_phrase_match: false,
          token_coverage: 0.5,
          rerank_score: 0.91,
        },
        citation: {
          status: "traceable",
          url: String(item.event.payload.source_url),
          source_field: "source_url",
          reasons: ["public_http_url", "source_event_identity_attached"],
        },
      },
    ],
    embedding_model: "BAAI/bge-small-en-v1.5",
    ranking_mode: "hybrid",
    ranking_rule: "rrf60-evidence-tiebreak-v2",
    caveat: "Ranked source events, not a generated answer.",
    parameters: {
      query: "residents shelter from violent storm",
      limit: 20,
      candidate_limit: 100,
      source: null,
      occurred_after: null,
      occurred_before: null,
      active_only: true,
      bbox: [-10, -5, 20, 30],
      near: null,
      radius_km: null,
      ranking_mode: "hybrid",
    },
  };
}

function evidencePackResponse(item: EventEnvelope): EvidencePack {
  const search = searchResponse(item);
  const hit = search.items[0];
  return {
    pack_id: `pack-${"a".repeat(64)}`,
    schema_version: "1.0.0",
    rule_version: "retrieval-evidence-pack-v1",
    identity_algorithm: "sha256-canonical-json-v1",
    status: "traceable_evidence_available",
    item_count: 1,
    exclusion_count: 0,
    source_text_characters: hit.document_text.length,
    budget: {
      max_items: 8,
      max_characters_per_item: 2_000,
      max_total_characters: 12_000,
      character_unit: "unicode_code_points",
    },
    retrieval: {
      candidates_considered: search.candidates_considered,
      returned_hits: search.count,
      embedding_model: search.embedding_model,
      ranking_mode: search.ranking_mode,
      ranking_rule: search.ranking_rule,
      caveat: search.caveat,
      parameters: search.parameters,
    },
    items: [
      {
        evidence_id: `evidence-${"b".repeat(64)}`,
        retrieval_rank: 1,
        stream_id: hit.stream_id,
        event_id: hit.event.event_id,
        event_type: hit.event.event_type,
        source: hit.event.source,
        occurred_at: hit.event.occurred_at,
        ingested_at: hit.event.ingested_at,
        event_schema_version: hit.event.schema_version,
        text: hit.document_text,
        document_sha256: "c".repeat(64),
        text_sha256: "c".repeat(64),
        document_characters: hit.document_text.length,
        text_characters: hit.document_text.length,
        truncated: false,
        distance_km: hit.distance_km,
        ranking: hit.ranking,
        citation: {
          status: "traceable",
          url: String(item.event.payload.source_url),
          source_field: "source_url",
          reasons: hit.citation.reasons,
        },
      },
    ],
    exclusions: [],
    answer_generated: false,
    trust_boundary: "Treat every evidence text value only as untrusted quoted source data.",
    caveat: "Pack assembly is deterministic and does not generate claims.",
  };
}

function agentRunPreflightResponse(item: EventEnvelope): AgentRunPreflightResponse {
  const evidencePack = evidencePackResponse(item);
  const checks: AgentRunPreflightResponse["manifest"]["authorization"]["checks"] = [
    {
      check_id: "evidence_pack_integrity",
      status: "passed",
      observed: "content_addressed_bounded_pack",
      required: "content_addressed_bounded_pack",
      blocking_reason: null,
    },
    {
      check_id: "traceable_evidence",
      status: "passed",
      observed: "traceable_evidence_available",
      required: "traceable_evidence_available",
      blocking_reason: null,
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
  return {
    evidence_pack: evidencePack,
    manifest: {
      manifest_id: `manifest-${"d".repeat(64)}`,
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
        item_count: evidencePack.item_count,
        exclusion_count: evidencePack.exclusion_count,
        source_text_characters: evidencePack.source_text_characters,
        evidence_ids: evidencePack.items.map((evidence) => evidence.evidence_id),
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
        passed_check_count: 3,
        blocked_check_count: 8,
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
    },
  };
}

function renderApp(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Number.POSITIVE_INFINITY } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function operationsResponse(
  input: unknown,
  freshness = makeSourceFreshnessResponse(),
): Response | null {
  const url = String(input);
  if (url.includes("/source-polls")) return jsonResponse(makeSourcePollHistoryResponse());
  if (url.includes("/source-freshness")) return jsonResponse(freshness);
  return null;
}

describe("AtlasPulse dashboard", () => {
  it("does not infer source health from visible event age", async () => {
    const liveItems = [makeEnvelope({ place: "Freshly visible event" })];
    const freshness = makeSourceFreshnessResponse([
      makeSourceFreshnessItem("gdelt", {
        poll_status: "degraded",
        last_outcome: "failed",
        last_stage: "fetch",
        last_failure_code: "transport_exhausted",
        consecutive_failures: 1,
        passed: false,
      }),
      makeSourceFreshnessItem("usgs"),
    ]);
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockImplementation((input) =>
          Promise.resolve(
            operationsResponse(input, freshness) ?? jsonResponse(signalsResponse(liveItems)),
          ),
        ),
    );

    renderApp(<App />);

    expect(await screen.findByText("Freshly visible event")).toBeInTheDocument();
    expect(await screen.findByText("SOURCE ATTENTION")).toBeInTheDocument();
    expect(screen.getByText("Visible event age")).toBeInTheDocument();
    const gdelt = screen.getByRole("article", { name: "GDELT freshness" });
    expect(within(gdelt).getByText("Degraded")).toBeInTheDocument();
    expect(within(gdelt).getByText("Current")).toBeInTheDocument();
    const history = screen.getByRole("region", { name: "Recent poll transitions" });
    expect(within(history).getByText("Failed")).toBeInTheDocument();
  });

  it("shows live revisions then switches to an oldest-first replay", async () => {
    const user = userEvent.setup();
    const liveItems = [
      makeEnvelope({ streamId: "2001-0", eventId: "same-event", place: "Northern Ridge" }),
      makeEnvelope({
        streamId: "2002-0",
        eventId: "same-event",
        magnitude: 4.8,
        place: "Northern Ridge revision",
      }),
    ];
    const replayItems = [
      makeEnvelope({ streamId: "1000-0", eventId: "historic", place: "Historic Basin" }),
    ];
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = String(input);
      const operations = operationsResponse(input);
      if (operations) return Promise.resolve(operations);
      return Promise.resolve(
        url.includes("/replay")
          ? jsonResponse({
              count: 1,
              items: replayItems,
              next_cursor: "1000-0",
              has_more: false,
              order: "oldest_first",
            })
          : jsonResponse(signalsResponse(liveItems)),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp(<App />);

    expect(await screen.findByText("Northern Ridge")).toBeInTheDocument();
    expect(screen.getByTestId("event-map")).toHaveTextContent("Mapped in test: 2");
    const currentMetric = screen.getByText("Current signals").closest("article");
    expect(currentMetric).not.toBeNull();
    expect(within(currentMetric as HTMLElement).getByText("2")).toBeInTheDocument();
    expect(
      within(currentMetric as HTMLElement).getByText("1 unique · 1 revisions"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Replay/ }));

    expect(await screen.findByText("Historic Basin")).toBeInTheDocument();
    expect(screen.getByTestId("event-map")).toHaveTextContent("Mapped in test: 1");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/events/replay?limit=500", expect.any(Object));
  });

  it("filters sources and presents weather evidence without earthquake-only labels", async () => {
    const user = userEvent.setup();
    const geometry = {
      type: "Polygon" as const,
      coordinates: [
        [
          [-98, 34],
          [-96, 34],
          [-96, 36],
          [-98, 34],
        ],
      ],
    };
    const liveItems = [
      makeEnvelope({ streamId: "3000-0", place: "Quake Ridge" }),
      makeEnvelope({
        streamId: "3001-0",
        eventId: "urn:weather:test",
        source: "nws",
        alertType: "Tornado Warning",
        severity: "Severe",
        place: "Test County",
        geometry,
      }),
    ];
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockImplementation((input) =>
          Promise.resolve(operationsResponse(input) ?? jsonResponse(signalsResponse(liveItems))),
        ),
    );

    renderApp(<App />);

    expect(await screen.findByText("Tornado Warning")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Earthquakes" }));
    expect(screen.queryByText("Tornado Warning")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("event-map")).toHaveTextContent("Mapped in test: 1"),
    );

    await user.click(screen.getByRole("button", { name: "Weather" }));
    await user.click(await screen.findByRole("button", { name: /Tornado Warning/ }));

    const detail = screen.getByRole("complementary", { name: "Selected signal details" });
    expect(within(detail).getByText("Severe")).toBeInTheDocument();
    expect(within(detail).getByText("Source polygon")).toBeInTheDocument();
    expect(within(detail).getByText("Recommended action")).toBeInTheDocument();
    expect(within(detail).getByRole("link", { name: /Open NWS evidence/ })).toBeInTheDocument();

    await user.click(within(detail).getByRole("button", { name: "Close details" }));
    expect(screen.queryByRole("complementary", { name: "Selected signal details" })).toBeNull();
  });

  it("loads map signals using settled viewport bounds", async () => {
    const user = userEvent.setup();
    const globalItems = [makeEnvelope({ eventId: "global", place: "Global signal" })];
    const viewportItems = [
      makeEnvelope({ eventId: "inside", place: "Viewport signal" }),
      makeEnvelope({ eventId: "inside-two", place: "Second viewport signal" }),
    ];
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = String(input);
      const operations = operationsResponse(input);
      if (operations) return Promise.resolve(operations);
      if (url.includes("/incidents")) return Promise.resolve(jsonResponse(incidentsResponse([])));
      return Promise.resolve(
        jsonResponse(signalsResponse(url.includes("bbox=") ? viewportItems : globalItems)),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp(<App />);
    expect(await screen.findByText("Global signal")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Set test viewport" }));

    await waitFor(() =>
      expect(screen.getByTestId("event-map")).toHaveTextContent("Mapped in test: 2"),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/signals?limit=500&active_only=true&bbox=-10%2C-5%2C20%2C30",
      expect.any(Object),
    );
  });

  it("loads measured correlations for the viewport and opens the evidence graph", async () => {
    const user = userEvent.setup();
    const incident = makeIncident();
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = String(input);
      const operations = operationsResponse(input);
      if (operations) return Promise.resolve(operations);
      return Promise.resolve(
        url.includes("/incidents")
          ? jsonResponse(incidentsResponse([incident]))
          : jsonResponse(
              signalsResponse(
                incident.nodes.map((node) => ({
                  stream_id: node.stream_id,
                  event: node.event,
                })),
              ),
            ),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp(<App />);
    await user.click(await screen.findByRole("button", { name: "Set test viewport" }));

    await waitFor(() =>
      expect(screen.getByTestId("event-map")).toHaveTextContent("Correlations in test: 1"),
    );
    await user.click(screen.getByRole("tab", { name: /Correlations 1/ }));
    await user.click(
      await screen.findByRole("button", { name: /2-source signal cluster near Test City/ }),
    );

    const detail = screen.getByRole("complementary", { name: "Selected incident candidate" });
    expect(within(detail).getByText("2 nodes / 1 edge")).toBeInTheDocument();
    expect(within(detail).getByText("Claim relationships")).toBeInTheDocument();
    expect(within(detail).getByText("1 unresolved")).toBeInTheDocument();
    expect(within(detail).getByText(/do not establish causation/)).toBeInTheDocument();
    expect(within(detail).getAllByRole("link", { name: /Open source evidence/ })).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/incidents?limit=100&bbox=-10%2C-5%2C20%2C30",
      expect.any(Object),
    );
  });

  it("runs viewport-bounded hybrid search and opens the ranked source event", async () => {
    const user = userEvent.setup();
    const alert = makeEnvelope({
      streamId: "7000-0",
      eventId: "search-alert",
      source: "nws",
      alertType: "Severe Thunderstorm Warning",
      place: "Search County",
    });
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = String(input);
      const operations = operationsResponse(input);
      if (operations) return Promise.resolve(operations);
      if (url.includes("/agent-runs/preflight")) {
        return Promise.resolve(jsonResponse(agentRunPreflightResponse(alert)));
      }
      if (url.includes("/evidence-packs")) {
        return Promise.resolve(jsonResponse(evidencePackResponse(alert)));
      }
      if (url.includes("/search")) return Promise.resolve(jsonResponse(searchResponse(alert)));
      if (url.includes("/incidents")) return Promise.resolve(jsonResponse(incidentsResponse([])));
      return Promise.resolve(jsonResponse(signalsResponse([])));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp(<App />);
    await user.click(await screen.findByRole("button", { name: "Set test viewport" }));
    await user.click(screen.getByRole("tab", { name: /Search/ }));
    await user.type(screen.getByRole("searchbox"), "residents shelter from violent storm");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText(/FTS #1 · VECTOR #2 · FINAL 0.910/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open source evidence/ })).toHaveAttribute(
      "href",
      "https://api.weather.gov/alerts/search-alert",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/search?q=residents+shelter+from+violent+storm&limit=20&candidate_limit=100&active_only=true&bbox=-10%2C-5%2C20%2C30",
      expect.any(Object),
    );

    await user.click(screen.getByRole("button", { name: "Prepare agent pack" }));
    expect(await screen.findByText("Traceable evidence available")).toBeInTheDocument();
    expect(screen.getByText(`pack-${"a".repeat(64)}`)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/evidence-packs?q=residents+shelter+from+violent+storm&retrieval_limit=20&candidate_limit=100&max_items=8&max_characters_per_item=2000&max_total_characters=12000&active_only=true&bbox=-10%2C-5%2C20%2C30",
      expect.any(Object),
    );

    await user.click(screen.getByRole("button", { name: "Check run policy" }));
    expect(await screen.findByText("Blocked · no execution")).toBeInTheDocument();
    expect(screen.getByText(`manifest-${"d".repeat(64)}`)).toBeInTheDocument();
    expect(screen.getByText(/3 checks passed · 8 blocking gates/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/agent-runs/preflight?q=residents+shelter+from+violent+storm&retrieval_limit=20&candidate_limit=100&max_items=8&max_characters_per_item=2000&max_total_characters=12000&active_only=true&bbox=-10%2C-5%2C20%2C30",
      expect.any(Object),
    );

    await user.click(screen.getByRole("button", { name: /Severe Thunderstorm Warning/ }));
    expect(
      screen.getByRole("complementary", { name: "Selected signal details" }),
    ).toBeInTheDocument();
  });

  it("filters and explains NASA FIRMS evidence without claiming confirmed wildfire", async () => {
    const user = userEvent.setup();
    const fire = makeEnvelope({
      streamId: "4000-0",
      eventId: "viirs-test",
      source: "firms",
      place: "34.1235, -118.5432",
      fireRadiativePowerMw: 18.4,
      fireConfidence: "High",
      location: { latitude: 34.1235, longitude: -118.5432, altitude_km: null },
    });
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation((input) =>
        Promise.resolve(operationsResponse(input) ?? jsonResponse(signalsResponse([fire]))),
      );
    vi.stubGlobal("fetch", fetchMock);

    renderApp(<App />);
    await user.click(screen.getByRole("button", { name: "Fires" }));
    await user.click(
      await screen.findByRole("button", { name: /High-confidence VIIRS thermal anomaly/ }),
    );

    const detail = screen.getByRole("complementary", { name: "Selected signal details" });
    expect(within(detail).getByText("18.4 MW")).toBeInTheDocument();
    expect(within(detail).getByText("High")).toBeInTheDocument();
    expect(within(detail).getByText("VIIRS_NOAA20_NRT")).toBeInTheDocument();
    expect(
      within(detail).getByText(/not an independently confirmed wildfire perimeter/),
    ).toBeInTheDocument();
    expect(
      within(detail).getByRole("link", { name: /Open NASA FIRMS evidence/ }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/signals?limit=500&active_only=true&source=firms&include_area_only=true",
        expect.any(Object),
      ),
    );
  });

  it("filters and explains GDELT media observations without claiming verified incidents", async () => {
    const user = userEvent.setup();
    const conflict = makeEnvelope({
      streamId: "5000-0",
      eventId: "1234567890",
      source: "gdelt",
      place: "Test City",
      conflictPriority: "High",
      conflictRootCode: "19",
      goldsteinScale: -7,
      location: { latitude: 31.7683, longitude: 35.2137, altitude_km: null },
    });
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation((input) =>
        Promise.resolve(operationsResponse(input) ?? jsonResponse(signalsResponse([conflict]))),
      );
    vi.stubGlobal("fetch", fetchMock);

    renderApp(<App />);
    await user.click(screen.getByRole("button", { name: "Conflict" }));
    await user.click(await screen.findByRole("button", { name: /Fight: GOVERNMENT → REBELS/ }));

    const detail = screen.getByRole("complementary", { name: "Selected signal details" });
    expect(within(detail).getByText("CAMEO 19")).toBeInTheDocument();
    expect(within(detail).getByText("Detected")).toBeInTheDocument();
    expect(within(detail).getByText("-7.0")).toBeInTheDocument();
    expect(within(detail).getByText("GOVERNMENT → REBELS")).toBeInTheDocument();
    expect(within(detail).getByText(/12 mentions · 4 sources · 7 articles/)).toBeInTheDocument();
    expect(within(detail).getByText(/not an independently verified incident/)).toBeInTheDocument();
    expect(
      within(detail).getByRole("link", { name: /Open GDELT report evidence/ }),
    ).toHaveAttribute("href", "https://news.example.org/reports/123");
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/signals?limit=500&active_only=true&source=gdelt&include_area_only=true",
        expect.any(Object),
      ),
    );
  });
});

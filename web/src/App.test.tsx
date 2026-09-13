import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { makeEnvelope, makeIncident } from "./test/fixtures";
import type { EventEnvelope, IncidentCandidate, SearchResponse, ViewportBounds } from "./types";

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
    ranking_rule: "rrf60-transparent-rerank-v1",
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
    },
  };
}

function renderApp(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Number.POSITIVE_INFINITY } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("AtlasPulse dashboard", () => {
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
        .mockImplementation(() => Promise.resolve(jsonResponse(signalsResponse(liveItems)))),
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
      .mockImplementation(() => Promise.resolve(jsonResponse(signalsResponse([fire]))));
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
      .mockImplementation(() => Promise.resolve(jsonResponse(signalsResponse([conflict]))));
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

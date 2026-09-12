import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { makeEnvelope } from "./test/fixtures";
import type { EventEnvelope, ViewportBounds } from "./types";

vi.mock("./components/EventMap", () => ({
  EventMap: ({
    events,
    onViewportChange,
  }: {
    events: EventEnvelope[];
    onViewportChange: (bounds: ViewportBounds) => void;
  }) => (
    <div data-testid="event-map">
      Mapped in test: {events.length}
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
    const uniqueMetric = screen.getByText("Unique events").closest("article");
    expect(uniqueMetric).not.toBeNull();
    expect(within(uniqueMetric as HTMLElement).getByText("1")).toBeInTheDocument();
    expect(within(uniqueMetric as HTMLElement).getByText("1 revisions")).toBeInTheDocument();

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
});

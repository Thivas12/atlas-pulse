import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { fetchLatest, fetchReplay } from "./api";
import { EventFeed } from "./components/EventFeed";
import { ReplayControls } from "./components/ReplayControls";
import {
  formatTimestamp,
  isActiveAt,
  isWeatherAlert,
  magnitudeOf,
  newestUpdate,
  placeOf,
  relativeAge,
  severityOf,
  severityRankOf,
  strongestMagnitude,
  titleOf,
} from "./event-utils";
import type { EventEnvelope } from "./types";

type ViewMode = "live" | "replay";
type SourceFilter = "all" | "usgs" | "nws";

const EventMap = lazy(() =>
  import("./components/EventMap").then(({ EventMap: component }) => ({ default: component })),
);

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function EventDetail({ item, onClose }: { item: EventEnvelope; onClose: () => void }) {
  const { event } = item;
  const weather = isWeatherAlert(event);
  const magnitude = magnitudeOf(event);
  const severity = severityOf(event);
  const sourceUrl = event.payload.source_url;
  const depth = event.payload.depth_km;
  const status = event.payload.status;
  const certainty = event.payload.certainty;
  const urgency = event.payload.urgency;
  const expiresAt = event.payload.expires_at;
  const senderName = event.payload.sender_name;
  const messageType = event.payload.message_type;
  const description = event.payload.description;
  const instruction = event.payload.instruction;
  return (
    <aside className="event-detail" aria-label="Selected signal details">
      <button className="detail-close" type="button" onClick={onClose} aria-label="Close details">
        ×
      </button>
      <p className="eyebrow">Selected signal</p>
      <div className={`detail-magnitude ${weather ? "weather" : ""}`}>
        {weather ? (severity ?? "Weather") : `M ${magnitude === null ? "?" : magnitude.toFixed(2)}`}
      </div>
      <h2>{titleOf(event)}</h2>
      {weather && <p className="detail-place">{placeOf(event)}</p>}
      <dl>
        <div>
          <dt>Occurred</dt>
          <dd>{formatTimestamp(event.occurred_at)} UTC</dd>
        </div>
        {weather ? (
          <div>
            <dt>Expires</dt>
            <dd>
              {typeof expiresAt === "string" ? `${formatTimestamp(expiresAt)} UTC` : "Unknown"}
            </dd>
          </div>
        ) : (
          <div>
            <dt>Depth</dt>
            <dd>{typeof depth === "number" ? `${depth.toFixed(1)} km` : "Unknown"}</dd>
          </div>
        )}
        <div>
          <dt>{weather ? "Urgency" : "Review"}</dt>
          <dd>
            {weather
              ? typeof urgency === "string"
                ? urgency
                : "Unknown"
              : typeof status === "string"
                ? status
                : "Unknown"}
          </dd>
        </div>
        <div>
          <dt>{weather ? "Certainty" : "Coordinates"}</dt>
          <dd>
            {weather
              ? typeof certainty === "string"
                ? certainty
                : "Unknown"
              : event.location
                ? `${event.location.latitude.toFixed(3)}, ${event.location.longitude.toFixed(3)}`
                : "Unknown"}
          </dd>
        </div>
        {weather && (
          <div>
            <dt>Alert state</dt>
            <dd>
              {typeof messageType === "string" ? messageType : "Unknown"} ·{" "}
              {typeof status === "string" ? status : "Unknown"}
            </dd>
          </div>
        )}
        {weather && (
          <div>
            <dt>Geometry</dt>
            <dd>{event.payload.geometry ? "Source polygon" : "Area codes only"}</dd>
          </div>
        )}
        {weather && (
          <div>
            <dt>Office</dt>
            <dd>{typeof senderName === "string" ? senderName : "Unknown"}</dd>
          </div>
        )}
        <div>
          <dt>Event ID</dt>
          <dd>{event.event_id}</dd>
        </div>
        <div>
          <dt>Replay ID</dt>
          <dd>{item.stream_id}</dd>
        </div>
      </dl>
      {weather && typeof description === "string" && (
        <p className="detail-summary">{description}</p>
      )}
      {weather && typeof instruction === "string" && (
        <p className="detail-instruction">
          <strong>Recommended action</strong>
          {instruction}
        </p>
      )}
      {typeof sourceUrl === "string" && sourceUrl.startsWith("https://") && (
        <a href={sourceUrl} target="_blank" rel="noreferrer" className="source-link">
          Open {weather ? "NWS" : "USGS"} evidence ↗
        </a>
      )}
    </aside>
  );
}

export default function App() {
  const [mode, setMode] = useState<ViewMode>("live");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [selectedStreamId, setSelectedStreamId] = useState<string | null>(null);
  const [replayIndex, setReplayIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  const latestQuery = useQuery({
    queryKey: ["events", "latest"],
    queryFn: ({ signal }) => fetchLatest(signal),
    refetchInterval: 10_000,
  });
  const replayQuery = useInfiniteQuery({
    queryKey: ["events", "replay"],
    queryFn: ({ pageParam, signal }) => fetchReplay(pageParam, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_cursor ?? undefined) : undefined,
    enabled: mode === "replay",
  });

  const latestItems = latestQuery.data?.items ?? [];
  const replayItems = useMemo(
    () => replayQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [replayQuery.data],
  );
  const replayVisible = replayItems.slice(0, Math.min(replayIndex + 1, replayItems.length));
  const activeLatestItems = latestItems.filter(({ event }) => isActiveAt(event));
  const currentItems = mode === "live" ? activeLatestItems : replayVisible;
  const mappedEvents = currentItems.filter(
    ({ event }) => sourceFilter === "all" || event.source === sourceFilter,
  );
  const feedEvents = mode === "live" ? mappedEvents : [...mappedEvents].reverse();
  const selected = mappedEvents.find((item) => item.stream_id === selectedStreamId) ?? null;

  useEffect(() => {
    if (mode !== "replay" || !playing || replayItems.length === 0) return;
    const timer = window.setInterval(() => {
      setReplayIndex((current) => {
        if (current >= replayItems.length - 1) {
          setPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, 900 / speed);
    return () => window.clearInterval(timer);
  }, [mode, playing, replayItems.length, speed]);

  useEffect(() => {
    if (
      mode === "replay" &&
      replayIndex >= replayItems.length - 10 &&
      replayQuery.hasNextPage &&
      !replayQuery.isFetchingNextPage
    ) {
      void replayQuery.fetchNextPage();
    }
  }, [mode, replayIndex, replayItems.length, replayQuery]);

  const switchMode = (nextMode: ViewMode) => {
    setMode(nextMode);
    setSelectedStreamId(null);
    setPlaying(false);
    if (nextMode === "replay") setReplayIndex(0);
  };

  const switchSource = (nextSource: SourceFilter) => {
    setSourceFilter(nextSource);
    setSelectedStreamId(null);
  };

  const uniqueEvents = new Set(mappedEvents.map(({ event }) => event.event_id)).size;
  const revisions = Math.max(0, mappedEvents.length - uniqueEvents);
  const earthquakes = mappedEvents.filter(({ event }) => event.source === "usgs");
  const weatherAlerts = mappedEvents.filter(({ event }) => isWeatherAlert(event));
  const strongest = strongestMagnitude(earthquakes);
  const highSeverity = weatherAlerts.filter(({ event }) => severityRankOf(event) >= 3).length;
  const areaOnly = weatherAlerts.filter(({ event }) => !event.payload.geometry).length;
  const usgsFreshness = relativeAge(
    newestUpdate(latestItems.filter(({ event }) => event.source === "usgs")),
  );
  const nwsFreshness = relativeAge(
    newestUpdate(latestItems.filter(({ event }) => event.source === "nws")),
  );
  const loading = mode === "live" ? latestQuery.isPending : replayQuery.isPending;
  const error = mode === "live" ? latestQuery.error : replayQuery.error;

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="AtlasPulse home">
          <span className="brand-mark" aria-hidden="true">
            ◒
          </span>
          <span>
            ATLAS<strong>PULSE</strong>
          </span>
        </a>
        <div className="mission-copy">
          <span>GLOBAL DISRUPTION INTELLIGENCE</span>
          <small>USGS + NWS · AUDITABLE · REPLAYABLE</small>
        </div>
        <div className="system-state">
          <span className={`live-dot ${latestQuery.isError ? "error" : ""}`} />
          <span>{latestQuery.isError ? "SOURCE DEGRADED" : "SYSTEM LIVE"}</span>
        </div>
      </header>

      <section className="control-strip">
        <fieldset className="mode-switch" aria-label="Dashboard mode">
          <button
            type="button"
            className={mode === "live" ? "active" : ""}
            onClick={() => switchMode("live")}
          >
            <span className="live-dot" /> Live
          </button>
          <button
            type="button"
            className={mode === "replay" ? "active" : ""}
            onClick={() => switchMode("replay")}
          >
            ◷ Replay
          </button>
        </fieldset>
        <fieldset className="source-switch" aria-label="Signal source">
          {(
            [
              ["all", "All"],
              ["usgs", "Earthquakes"],
              ["nws", "Weather"],
            ] as const
          ).map(([value, label]) => (
            <button
              type="button"
              className={sourceFilter === value ? "active" : ""}
              onClick={() => switchSource(value)}
              key={value}
            >
              {label}
            </button>
          ))}
        </fieldset>
        <p>
          {mode === "live"
            ? "Newest stream revisions · refreshes every 10 seconds"
            : "Oldest-first immutable event history · cursor deterministic"}
        </p>
        <span className="utc-clock">UTC · {new Date().toISOString().slice(11, 19)}</span>
      </section>

      <section className="metrics" aria-label="Current stream metrics">
        <Metric
          label="Visible revisions"
          value={String(mappedEvents.length)}
          detail={`${currentItems.length} across all sources`}
        />
        <Metric
          label="Unique events"
          value={String(uniqueEvents)}
          detail={`${revisions} revisions`}
        />
        <Metric
          label="Earthquakes"
          value={String(earthquakes.length)}
          detail={strongest === null ? "no magnitude" : `strongest M ${strongest.toFixed(1)}`}
        />
        <Metric
          label="Weather alerts"
          value={String(weatherAlerts.length)}
          detail={`${areaOnly} area-code only`}
        />
        <Metric label="High severity" value={String(highSeverity)} detail="severe or extreme" />
        <Metric
          label="Source freshness"
          value={`USGS ${usgsFreshness}`}
          detail={`NWS ${nwsFreshness}`}
        />
      </section>

      <section className="command-grid">
        <div className="map-panel">
          <Suspense fallback={<div className="map-message">Loading spatial renderer…</div>}>
            <EventMap
              events={mappedEvents}
              selectedStreamId={selectedStreamId}
              onSelect={setSelectedStreamId}
            />
          </Suspense>
          <div className="map-scanline" aria-hidden="true" />
          <div className="map-legend">
            <span>QUAKE</span>
            <i className="mild" /> low
            <i className="strong" /> M5+
            <span className="weather-legend">WEATHER</span>
            <i className="weather-moderate" /> moderate
            <i className="weather-severe" /> severe+
          </div>
          {loading && <div className="map-message">Synchronising event stream…</div>}
          {error && <div className="map-message error">{error.message}</div>}
          {selected && <EventDetail item={selected} onClose={() => setSelectedStreamId(null)} />}
          {mode === "replay" && (
            <ReplayControls
              current={replayIndex}
              total={replayItems.length}
              playing={playing}
              speed={speed}
              hasMore={Boolean(replayQuery.hasNextPage)}
              loadingMore={replayQuery.isFetchingNextPage}
              onCurrentChange={(value) => {
                setReplayIndex(value);
                setPlaying(false);
              }}
              onPlayingChange={setPlaying}
              onSpeedChange={setSpeed}
              onLoadMore={() => void replayQuery.fetchNextPage()}
            />
          )}
        </div>
        <EventFeed
          events={feedEvents}
          selectedStreamId={selectedStreamId}
          onSelect={setSelectedStreamId}
        />
      </section>

      <footer>
        <span>ATLASPULSE / MULTI-SOURCE SLICE / v0.2.0</span>
        <span>Evidence: USGS + NOAA/NWS · Basemap: OpenFreeMap/OpenStreetMap</span>
      </footer>
    </main>
  );
}

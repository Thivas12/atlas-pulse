import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { fetchLatest, fetchReplay } from "./api";
import { EventFeed } from "./components/EventFeed";
import { ReplayControls } from "./components/ReplayControls";
import {
  formatTimestamp,
  magnitudeOf,
  newestUpdate,
  placeOf,
  relativeAge,
  strongestMagnitude,
} from "./event-utils";
import type { EventEnvelope } from "./types";

type ViewMode = "live" | "replay";

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
  const magnitude = magnitudeOf(event);
  const sourceUrl = event.payload.source_url;
  const depth = event.payload.depth_km;
  const status = event.payload.status;
  return (
    <aside className="event-detail" aria-label="Selected earthquake details">
      <button className="detail-close" type="button" onClick={onClose} aria-label="Close details">
        ×
      </button>
      <p className="eyebrow">Selected signal</p>
      <div className="detail-magnitude">M {magnitude === null ? "?" : magnitude.toFixed(2)}</div>
      <h2>{placeOf(event)}</h2>
      <dl>
        <div>
          <dt>Occurred</dt>
          <dd>{formatTimestamp(event.occurred_at)} UTC</dd>
        </div>
        <div>
          <dt>Depth</dt>
          <dd>{typeof depth === "number" ? `${depth.toFixed(1)} km` : "Unknown"}</dd>
        </div>
        <div>
          <dt>Review</dt>
          <dd>{typeof status === "string" ? status : "Unknown"}</dd>
        </div>
        <div>
          <dt>Coordinates</dt>
          <dd>
            {event.location
              ? `${event.location.latitude.toFixed(3)}, ${event.location.longitude.toFixed(3)}`
              : "Unknown"}
          </dd>
        </div>
        <div>
          <dt>Event ID</dt>
          <dd>{event.event_id}</dd>
        </div>
        <div>
          <dt>Replay ID</dt>
          <dd>{item.stream_id}</dd>
        </div>
      </dl>
      {typeof sourceUrl === "string" && sourceUrl.startsWith("https://") && (
        <a href={sourceUrl} target="_blank" rel="noreferrer" className="source-link">
          Open USGS evidence ↗
        </a>
      )}
    </aside>
  );
}

export default function App() {
  const [mode, setMode] = useState<ViewMode>("live");
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
  const mappedEvents = mode === "live" ? latestItems : replayVisible;
  const feedEvents = mode === "live" ? latestItems : [...replayVisible].reverse();
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

  const uniqueEvents = new Set(mappedEvents.map(({ event }) => event.event_id)).size;
  const revisions = Math.max(0, mappedEvents.length - uniqueEvents);
  const strongest = strongestMagnitude(mappedEvents);
  const freshness = relativeAge(newestUpdate(latestItems));
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
          <span>GLOBAL SEISMIC INTELLIGENCE</span>
          <small>USGS · AUDITABLE · REPLAYABLE</small>
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
        <p>
          {mode === "live"
            ? "Newest stream revisions · refreshes every 10 seconds"
            : "Oldest-first immutable event history · cursor deterministic"}
        </p>
        <span className="utc-clock">UTC · {new Date().toISOString().slice(11, 19)}</span>
      </section>

      <section className="metrics" aria-label="Current stream metrics">
        <Metric
          label="Mapped revisions"
          value={String(mappedEvents.length)}
          detail="current view"
        />
        <Metric
          label="Unique events"
          value={String(uniqueEvents)}
          detail={`${revisions} revisions`}
        />
        <Metric
          label="Strongest signal"
          value={strongest === null ? "—" : `M ${strongest.toFixed(1)}`}
          detail="current view"
        />
        <Metric label="Source freshness" value={freshness} detail="since latest update" />
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
            <span>MAGNITUDE</span>
            <i className="mild" /> 0–3
            <i className="moderate" /> 3–5
            <i className="strong" /> 5+
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
        <span>ATLASPULSE / SEISMIC SLICE / v0.1.0</span>
        <span>Evidence: USGS · Basemap: OpenFreeMap/OpenStreetMap</span>
      </footer>
    </main>
  );
}

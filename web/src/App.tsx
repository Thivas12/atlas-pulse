import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import {
  fetchAgentRunPreflight,
  fetchCurrentSignals,
  fetchEvidencePack,
  fetchHybridSearch,
  fetchIncidentCandidates,
  fetchReplay,
} from "./api";
import { EventFeed } from "./components/EventFeed";
import { IncidentDetail } from "./components/IncidentDetail";
import { IncidentFeed } from "./components/IncidentFeed";
import { ReplayControls } from "./components/ReplayControls";
import { SearchPanel } from "./components/SearchPanel";
import {
  cameoRootCodeOf,
  conflictPriorityOf,
  conflictPriorityRankOf,
  fireConfidenceOf,
  fireConfidenceRankOf,
  fireRadiativePowerOf,
  formatTimestamp,
  goldsteinScaleOf,
  isFireDetection,
  isGeopoliticalEvent,
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
import type { EventEnvelope, ViewportBounds } from "./types";

type ViewMode = "live" | "replay";
type SourceFilter = "all" | "usgs" | "nws" | "firms" | "gdelt";
type PanelMode = "signals" | "incidents" | "search";

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

function payloadString(event: EventEnvelope["event"], key: string): string | null {
  const value = event.payload[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function payloadNumber(event: EventEnvelope["event"], key: string): number | null {
  const value = event.payload[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function safeSourceUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password
      ? value
      : null;
  } catch {
    return null;
  }
}

function EventDetail({
  item,
  onClose,
  allowSourceLink = true,
}: {
  item: EventEnvelope;
  onClose: () => void;
  allowSourceLink?: boolean;
}) {
  const { event } = item;
  const weather = isWeatherAlert(event);
  const fire = isFireDetection(event);
  const conflict = isGeopoliticalEvent(event);
  const magnitude = magnitudeOf(event);
  const severity = severityOf(event);
  const fireConfidence = fireConfidenceOf(event);
  const fireRadiativePower = fireRadiativePowerOf(event);
  const conflictPriority = conflictPriorityOf(event);
  const conflictRootCode = cameoRootCodeOf(event);
  const goldsteinScale = goldsteinScaleOf(event);
  const sourceUrl = allowSourceLink ? safeSourceUrl(event.payload.source_url) : null;
  const depth = event.payload.depth_km;
  const status = payloadString(event, "status");
  const certainty = payloadString(event, "certainty");
  const urgency = payloadString(event, "urgency");
  const expiresAt = payloadString(event, "expires_at");
  const senderName = payloadString(event, "sender_name");
  const messageType = payloadString(event, "message_type");
  const description = payloadString(event, "description");
  const instruction = payloadString(event, "instruction");
  const satellite = payloadString(event, "satellite");
  const product = payloadString(event, "product");
  const dayNight = payloadString(event, "day_night");
  const brightness = payloadNumber(event, "brightness_ti4_k");
  const cameoEventCode = payloadString(event, "cameo_event_code");
  const actor1 = payloadString(event, "actor1");
  const actor2 = payloadString(event, "actor2");
  const actors = [actor1, actor2].filter((value): value is string => value !== null).join(" → ");
  const mentions = payloadNumber(event, "mentions");
  const sources = payloadNumber(event, "sources");
  const articles = payloadNumber(event, "articles");
  const averageTone = payloadNumber(event, "average_tone");
  const reportedEventDate = payloadString(event, "reported_event_date");
  const geoPrecision = payloadString(event, "geo_precision");
  const sourceLabel =
    event.source === "firms"
      ? "NASA FIRMS"
      : event.source === "gdelt"
        ? "GDELT report"
        : event.source.toUpperCase();
  const primaryMetric = weather
    ? (severity ?? "Weather")
    : fire
      ? fireRadiativePower === null
        ? "Thermal"
        : `${fireRadiativePower.toFixed(1)} MW`
      : conflict
        ? conflictRootCode
          ? `CAMEO ${conflictRootCode}`
          : "Conflict"
        : `M ${magnitude === null ? "?" : magnitude.toFixed(2)}`;
  return (
    <aside
      className={`event-detail ${conflict ? "conflict" : ""}`}
      aria-label="Selected signal details"
    >
      <button className="detail-close" type="button" onClick={onClose} aria-label="Close details">
        ×
      </button>
      <p className="eyebrow">Selected signal</p>
      <div
        className={`detail-magnitude ${weather ? "weather" : fire ? "fire" : conflict ? "conflict" : ""}`}
      >
        {primaryMetric}
      </div>
      <h2>{titleOf(event)}</h2>
      {(weather || fire || conflict) && <p className="detail-place">{placeOf(event)}</p>}
      <dl>
        <div>
          <dt>{conflict ? "Detected" : "Occurred"}</dt>
          <dd>{formatTimestamp(event.occurred_at)} UTC</dd>
        </div>
        {weather || fire || conflict ? (
          <div>
            <dt>{fire || conflict ? "Display until" : "Expires"}</dt>
            <dd>{expiresAt ? `${formatTimestamp(expiresAt)} UTC` : "Unknown"}</dd>
          </div>
        ) : (
          <div>
            <dt>Depth</dt>
            <dd>{typeof depth === "number" ? `${depth.toFixed(1)} km` : "Unknown"}</dd>
          </div>
        )}
        <div>
          <dt>{weather ? "Urgency" : fire ? "Confidence" : conflict ? "Priority" : "Review"}</dt>
          <dd>
            {weather
              ? (urgency ?? "Unknown")
              : fire
                ? (fireConfidence ?? "Unknown")
                : conflict
                  ? (conflictPriority ?? "Unknown")
                  : (status ?? "Unknown")}
          </dd>
        </div>
        <div>
          <dt>
            {weather
              ? "Certainty"
              : fire
                ? "Satellite"
                : conflict
                  ? "Goldstein scale"
                  : "Coordinates"}
          </dt>
          <dd>
            {weather
              ? (certainty ?? "Unknown")
              : fire
                ? (satellite ?? "Unknown")
                : conflict
                  ? goldsteinScale === null
                    ? "Unknown"
                    : goldsteinScale.toFixed(1)
                  : event.location
                    ? `${event.location.latitude.toFixed(3)}, ${event.location.longitude.toFixed(3)}`
                    : "Unknown"}
          </dd>
        </div>
        {weather && (
          <div>
            <dt>Alert state</dt>
            <dd>
              {messageType ?? "Unknown"} · {status ?? "Unknown"}
            </dd>
          </div>
        )}
        {fire && (
          <div>
            <dt>Sensor product</dt>
            <dd>{product ?? "Unknown"}</dd>
          </div>
        )}
        {fire && (
          <div>
            <dt>Observation</dt>
            <dd>{dayNight ?? "Unknown"}</dd>
          </div>
        )}
        {fire && (
          <div>
            <dt>Brightness</dt>
            <dd>{brightness === null ? "Unknown" : `${brightness.toFixed(1)} K`}</dd>
          </div>
        )}
        {fire && (
          <div>
            <dt>Coordinates</dt>
            <dd>
              {event.location
                ? `${event.location.latitude.toFixed(3)}, ${event.location.longitude.toFixed(3)}`
                : "Unknown"}
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
            <dd>{senderName ?? "Unknown"}</dd>
          </div>
        )}
        {conflict && (
          <div>
            <dt>Actors</dt>
            <dd>{actors || "Unknown"}</dd>
          </div>
        )}
        {conflict && (
          <div>
            <dt>CAMEO classification</dt>
            <dd>
              Root {conflictRootCode ?? "?"} · event {cameoEventCode ?? "?"}
            </dd>
          </div>
        )}
        {conflict && (
          <div>
            <dt>Media coverage</dt>
            <dd>
              {mentions ?? 0} mentions · {sources ?? 0} sources · {articles ?? 0} articles
            </dd>
          </div>
        )}
        {conflict && (
          <div>
            <dt>Average tone</dt>
            <dd>{averageTone === null ? "Unknown" : averageTone.toFixed(2)}</dd>
          </div>
        )}
        {conflict && (
          <div>
            <dt>Reported event date</dt>
            <dd>{reportedEventDate ?? "Unknown"}</dd>
          </div>
        )}
        {conflict && (
          <div>
            <dt>Location precision</dt>
            <dd>{geoPrecision ?? "Unknown"}</dd>
          </div>
        )}
        {conflict && (
          <div>
            <dt>Coordinates</dt>
            <dd>
              {event.location
                ? `${event.location.latitude.toFixed(3)}, ${event.location.longitude.toFixed(3)}`
                : "Unknown"}
            </dd>
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
      {weather && description && <p className="detail-summary">{description}</p>}
      {weather && instruction && (
        <p className="detail-instruction">
          <strong>Recommended action</strong>
          {instruction}
        </p>
      )}
      {fire && (
        <p className="detail-summary">
          Satellite thermal anomaly. It may represent fire or another heat source and is not an
          independently confirmed wildfire perimeter.
        </p>
      )}
      {conflict && (
        <p className="detail-summary">
          Machine-coded media observation. It can contain NLP, reporting, or geocoding errors and is
          not an independently verified incident.
        </p>
      )}
      {sourceUrl && (
        <a href={sourceUrl} target="_blank" rel="noreferrer" className="source-link">
          Open {sourceLabel} evidence ↗
        </a>
      )}
    </aside>
  );
}

export default function App() {
  const [mode, setMode] = useState<ViewMode>("live");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [panelMode, setPanelMode] = useState<PanelMode>("signals");
  const [selectedStreamId, setSelectedStreamId] = useState<string | null>(null);
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null);
  const [replayIndex, setReplayIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [viewport, setViewport] = useState<ViewportBounds | null>(null);
  const [searchDraft, setSearchDraft] = useState("");
  const [searchText, setSearchText] = useState("");
  const [searchViewport, setSearchViewport] = useState(true);

  const liveQuery = useQuery({
    queryKey: ["signals", "current", sourceFilter],
    queryFn: ({ signal }) =>
      fetchCurrentSignals(
        {
          source: sourceFilter === "all" ? undefined : sourceFilter,
          includeAreaOnly: true,
        },
        signal,
      ),
    refetchInterval: 10_000,
    enabled: mode === "live",
  });
  const viewportQuery = useQuery({
    queryKey: ["signals", "viewport", sourceFilter, viewport],
    queryFn: ({ signal }) =>
      fetchCurrentSignals(
        {
          source: sourceFilter === "all" ? undefined : sourceFilter,
          bounds: viewport ?? undefined,
        },
        signal,
      ),
    refetchInterval: 10_000,
    enabled: mode === "live" && viewport !== null,
  });
  const incidentQuery = useQuery({
    queryKey: ["incidents", "viewport", viewport],
    queryFn: ({ signal }) => fetchIncidentCandidates(viewport ?? undefined, signal),
    refetchInterval: 10_000,
    enabled: mode === "live" && viewport !== null,
  });
  const replayQuery = useInfiniteQuery({
    queryKey: ["events", "replay"],
    queryFn: ({ pageParam, signal }) => fetchReplay(pageParam, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_cursor ?? undefined) : undefined,
    enabled: mode === "replay",
  });
  const searchQuery = useQuery({
    queryKey: ["retrieval", searchText, sourceFilter, searchViewport ? viewport : null],
    queryFn: ({ signal }) =>
      fetchHybridSearch(
        {
          query: searchText,
          source: sourceFilter === "all" ? undefined : sourceFilter,
          bounds: searchViewport ? (viewport ?? undefined) : undefined,
        },
        signal,
      ),
    enabled: mode === "live" && panelMode === "search" && searchText.length >= 2,
    refetchInterval: 30_000,
  });
  const evidencePackQuery = useQuery({
    queryKey: ["evidence-pack", searchText, sourceFilter, searchViewport ? viewport : null],
    queryFn: ({ signal }) =>
      fetchEvidencePack(
        {
          query: searchText,
          source: sourceFilter === "all" ? undefined : sourceFilter,
          bounds: searchViewport ? (viewport ?? undefined) : undefined,
        },
        signal,
      ),
    enabled: false,
    staleTime: Number.POSITIVE_INFINITY,
  });
  const agentRunPreflightQuery = useQuery({
    queryKey: ["agent-run-preflight", searchText, sourceFilter, searchViewport ? viewport : null],
    queryFn: ({ signal }) =>
      fetchAgentRunPreflight(
        {
          query: searchText,
          source: sourceFilter === "all" ? undefined : sourceFilter,
          bounds: searchViewport ? (viewport ?? undefined) : undefined,
        },
        signal,
      ),
    enabled: false,
    staleTime: Number.POSITIVE_INFINITY,
  });

  const liveItems = liveQuery.data?.items ?? [];
  const replayItems = useMemo(
    () => replayQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [replayQuery.data],
  );
  const replayVisible = replayItems.slice(0, Math.min(replayIndex + 1, replayItems.length));
  const currentItems = mode === "live" ? liveItems : replayVisible;
  const filteredEvents = currentItems.filter(
    ({ event }) => sourceFilter === "all" || event.source === sourceFilter,
  );
  const mappedEvents =
    mode === "live" && viewport !== null
      ? (viewportQuery.data?.items ?? []).filter(
          ({ event }) => sourceFilter === "all" || event.source === sourceFilter,
        )
      : filteredEvents;
  const feedEvents = mode === "live" ? filteredEvents : [...filteredEvents].reverse();
  const incidents = incidentQuery.data?.items ?? [];
  const visibleIncidents =
    sourceFilter === "all"
      ? incidents
      : incidents.filter((incident) => incident.sources.includes(sourceFilter));
  const searchItems = searchQuery.data?.items ?? [];
  const selectedSearchHit = searchItems.find((item) => item.stream_id === selectedStreamId);
  const selected =
    mappedEvents.find((item) => item.stream_id === selectedStreamId) ??
    filteredEvents.find((item) => item.stream_id === selectedStreamId) ??
    (selectedSearchHit
      ? { stream_id: selectedSearchHit.stream_id, event: selectedSearchHit.event }
      : null) ??
    null;
  const selectedIncident =
    visibleIncidents.find((incident) => incident.incident_id === selectedIncidentId) ?? null;

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
    setSelectedIncidentId(null);
    setPlaying(false);
    if (nextMode === "replay") {
      setReplayIndex(0);
      setPanelMode("signals");
    }
  };

  const switchSource = (nextSource: SourceFilter) => {
    setSourceFilter(nextSource);
    setSelectedStreamId(null);
    setSelectedIncidentId(null);
  };

  const selectEvent = (streamId: string) => {
    setSelectedStreamId(streamId);
    setSelectedIncidentId(null);
  };

  const selectIncident = (incidentId: string) => {
    setSelectedIncidentId(incidentId);
    setSelectedStreamId(null);
    setPanelMode("incidents");
  };

  const uniqueEvents = new Set(filteredEvents.map(({ event }) => event.event_id)).size;
  const revisions = Math.max(0, filteredEvents.length - uniqueEvents);
  const earthquakes = filteredEvents.filter(({ event }) => event.source === "usgs");
  const weatherAlerts = filteredEvents.filter(({ event }) => isWeatherAlert(event));
  const fireDetections = filteredEvents.filter(({ event }) => isFireDetection(event));
  const conflictSignals = filteredEvents.filter(({ event }) => isGeopoliticalEvent(event));
  const strongest = strongestMagnitude(earthquakes);
  const highSeverity = weatherAlerts.filter(({ event }) => severityRankOf(event) >= 3).length;
  const highConfidenceFires = fireDetections.filter(
    ({ event }) => fireConfidenceRankOf(event) >= 3,
  ).length;
  const highPriorityConflicts = conflictSignals.filter(
    ({ event }) => conflictPriorityRankOf(event) >= 3,
  ).length;
  const areaOnly = weatherAlerts.filter(({ event }) => !event.payload.geometry).length;
  const usgsFreshness = relativeAge(
    newestUpdate(liveItems.filter(({ event }) => event.source === "usgs")),
  );
  const nwsFreshness = relativeAge(
    newestUpdate(liveItems.filter(({ event }) => event.source === "nws")),
  );
  const firmsFreshness = relativeAge(
    newestUpdate(liveItems.filter(({ event }) => event.source === "firms")),
  );
  const gdeltFreshness = relativeAge(
    newestUpdate(liveItems.filter(({ event }) => event.source === "gdelt")),
  );
  const loading =
    mode === "live"
      ? liveQuery.isPending ||
        (viewport !== null && (viewportQuery.isPending || incidentQuery.isPending))
      : replayQuery.isPending;
  const error =
    mode === "live"
      ? liveQuery.error || viewportQuery.error || incidentQuery.error
      : replayQuery.error;

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
          <small>FOUR LIVE SOURCES · HYBRID SEARCH · CORRELATED · AUDITABLE</small>
        </div>
        <div className="system-state">
          <span className={`live-dot ${liveQuery.isError ? "error" : ""}`} />
          <span>{liveQuery.isError ? "SOURCE DEGRADED" : "SYSTEM LIVE"}</span>
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
              ["firms", "Fires"],
              ["gdelt", "Conflict"],
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
            ? "Durable current state · viewport queried · refreshes every 10 seconds"
            : "Oldest-first immutable event history · cursor deterministic"}
        </p>
        <span className="utc-clock">UTC · {new Date().toISOString().slice(11, 19)}</span>
      </section>

      <section className="metrics" aria-label="Current stream metrics">
        <Metric
          label={mode === "live" ? "Current signals" : "Visible revisions"}
          value={String(filteredEvents.length)}
          detail={`${uniqueEvents} unique · ${revisions} revisions`}
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
        <Metric
          label="Thermal anomalies"
          value={String(fireDetections.length)}
          detail={`${mappedEvents.length} total signals in viewport`}
        />
        <Metric
          label="Conflict signals"
          value={String(conflictSignals.length)}
          detail={`${highPriorityConflicts} high or critical`}
        />
        <Metric
          label="Correlated clusters"
          value={mode === "live" ? String(visibleIncidents.length) : "—"}
          detail="measured cross-source edges"
        />
        <Metric
          label="High priority"
          value={String(highSeverity + highConfidenceFires + highPriorityConflicts)}
          detail={`${highSeverity} weather · ${highConfidenceFires} fire · ${highPriorityConflicts} conflict`}
        />
        <Metric
          label="Source freshness"
          value={`USGS ${usgsFreshness}`}
          detail={`NWS ${nwsFreshness} · FIRMS ${firmsFreshness} · GDELT ${gdeltFreshness}`}
        />
      </section>

      <section className="command-grid">
        <div className="map-panel">
          <Suspense fallback={<div className="map-message">Loading spatial renderer…</div>}>
            <EventMap
              events={mappedEvents}
              incidents={mode === "live" ? visibleIncidents : []}
              selectedStreamId={selectedStreamId}
              selectedIncidentId={selectedIncidentId}
              onSelect={selectEvent}
              onIncidentSelect={selectIncident}
              onViewportChange={setViewport}
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
            <span className="fire-legend">FIRE</span>
            <i className="fire-high" /> high confidence
            <span className="conflict-legend">CONFLICT</span>
            <i className="conflict-high" /> high / critical
            <span className="graph-legend">LINK</span>
            <i className="graph-link" /> measured co-occurrence
          </div>
          {loading && <div className="map-message">Synchronising event stream…</div>}
          {error && <div className="map-message error">{error.message}</div>}
          {selected && (
            <EventDetail
              item={selected}
              onClose={() => setSelectedStreamId(null)}
              allowSourceLink={
                selectedSearchHit === undefined || selectedSearchHit.citation.status === "traceable"
              }
            />
          )}
          {selectedIncident && (
            <IncidentDetail
              incident={selectedIncident}
              onClose={() => setSelectedIncidentId(null)}
            />
          )}
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
        <div className="intelligence-panel">
          <div className="panel-tabs" role="tablist" aria-label="Intelligence panel">
            <button
              type="button"
              role="tab"
              aria-selected={panelMode === "signals"}
              className={panelMode === "signals" ? "active" : ""}
              onClick={() => setPanelMode("signals")}
            >
              Signals
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={panelMode === "incidents"}
              className={panelMode === "incidents" ? "active" : ""}
              onClick={() => setPanelMode("incidents")}
              disabled={mode === "replay"}
            >
              Correlations <span>{visibleIncidents.length}</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={panelMode === "search"}
              className={panelMode === "search" ? "active" : ""}
              onClick={() => setPanelMode("search")}
              disabled={mode === "replay"}
            >
              Search <span>{searchItems.length}</span>
            </button>
          </div>
          {panelMode === "signals" || mode === "replay" ? (
            <EventFeed
              events={feedEvents}
              selectedStreamId={selectedStreamId}
              onSelect={selectEvent}
            />
          ) : panelMode === "incidents" ? (
            <IncidentFeed
              incidents={visibleIncidents}
              selectedIncidentId={selectedIncidentId}
              candidateEdgesTruncated={Boolean(incidentQuery.data?.candidate_edges_truncated)}
              onSelect={selectIncident}
            />
          ) : (
            <SearchPanel
              query={searchText}
              draft={searchDraft}
              useViewport={searchViewport}
              viewportAvailable={viewport !== null}
              response={searchQuery.data}
              evidencePack={agentRunPreflightQuery.data?.evidence_pack ?? evidencePackQuery.data}
              agentRunManifest={agentRunPreflightQuery.data?.manifest}
              loading={searchQuery.isFetching}
              error={searchQuery.error}
              evidencePackLoading={evidencePackQuery.isFetching}
              evidencePackError={evidencePackQuery.error}
              agentRunPreflightLoading={agentRunPreflightQuery.isFetching}
              agentRunPreflightError={agentRunPreflightQuery.error}
              selectedStreamId={selectedStreamId}
              onDraftChange={setSearchDraft}
              onUseViewportChange={setSearchViewport}
              onSubmit={() => {
                const normalized = searchDraft.trim();
                if (normalized === searchText) {
                  void searchQuery.refetch();
                } else {
                  setSearchText(normalized);
                }
                setSelectedIncidentId(null);
              }}
              onBuildEvidencePack={() => void evidencePackQuery.refetch()}
              onBuildAgentRunPreflight={() => void agentRunPreflightQuery.refetch()}
              onSelect={selectEvent}
            />
          )}
        </div>
      </section>

      <footer>
        <span>ATLASPULSE / SOURCE FRESHNESS CONTROL / v0.10.0</span>
        <span>Proposal-scoped approval · Ed25519 ledger · default deny · zero execution</span>
      </footer>
    </main>
  );
}

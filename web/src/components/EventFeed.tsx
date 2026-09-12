import {
  cameoRootCodeOf,
  conflictPriorityOf,
  fireConfidenceOf,
  fireRadiativePowerOf,
  formatTimestamp,
  isFireDetection,
  isGeopoliticalEvent,
  isWeatherAlert,
  magnitudeOf,
  placeOf,
  severityOf,
  titleOf,
} from "../event-utils";
import type { EventEnvelope } from "../types";

interface EventFeedProps {
  events: EventEnvelope[];
  selectedStreamId: string | null;
  onSelect: (streamId: string) => void;
}

export function EventFeed({ events, selectedStreamId, onSelect }: EventFeedProps) {
  return (
    <section className="event-feed" aria-labelledby="event-feed-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Stream intelligence</p>
          <h2 id="event-feed-title">Latest signals</h2>
        </div>
        <span className="record-count">{events.length}</span>
      </div>
      <div className="event-list">
        {events.length === 0 ? (
          <p className="empty-state">Waiting for the first mapped event…</p>
        ) : (
          events.map((item) => {
            const magnitude = magnitudeOf(item.event);
            const weather = isWeatherAlert(item.event);
            const fire = isFireDetection(item.event);
            const conflict = isGeopoliticalEvent(item.event);
            const severity = severityOf(item.event);
            const confidence = fireConfidenceOf(item.event);
            const conflictPriority = conflictPriorityOf(item.event);
            const conflictRootCode = cameoRootCodeOf(item.event);
            const fireRadiativePower = fireRadiativePowerOf(item.event);
            return (
              <button
                className={`event-row ${selectedStreamId === item.stream_id ? "selected" : ""}`}
                key={item.stream_id}
                onClick={() => onSelect(item.stream_id)}
                type="button"
              >
                <span
                  className={`signal-marker ${
                    weather
                      ? `weather severity-${severity?.toLowerCase() ?? "unknown"}`
                      : fire
                        ? `fire confidence-${confidence?.toLowerCase() ?? "unknown"}`
                        : conflict
                          ? `conflict priority-${conflictPriority?.toLowerCase() ?? "unknown"}`
                          : "quake"
                  }`}
                >
                  {weather
                    ? (severity?.slice(0, 3).toUpperCase() ?? "WX")
                    : fire
                      ? `${fireRadiativePower?.toFixed(0) ?? "?"}MW`
                      : conflict
                        ? `C${conflictRootCode ?? "?"}`
                        : (magnitude?.toFixed(1) ?? "?")}
                </span>
                <span className="event-copy">
                  <strong>{titleOf(item.event)}</strong>
                  <small>
                    <span className={`source-badge ${item.event.source}`}>{item.event.source}</span>
                    {weather || fire || conflict ? `${placeOf(item.event)} · ` : ""}
                    {formatTimestamp(item.event.occurred_at)} UTC
                  </small>
                </span>
                <span className="row-arrow" aria-hidden="true">
                  ↗
                </span>
              </button>
            );
          })
        )}
      </div>
    </section>
  );
}

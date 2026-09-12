import { formatTimestamp, magnitudeOf, placeOf } from "../event-utils";
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
            return (
              <button
                className={`event-row ${selectedStreamId === item.stream_id ? "selected" : ""}`}
                key={item.stream_id}
                onClick={() => onSelect(item.stream_id)}
                type="button"
              >
                <span className="magnitude">{magnitude === null ? "?" : magnitude.toFixed(1)}</span>
                <span className="event-copy">
                  <strong>{placeOf(item.event)}</strong>
                  <small>{formatTimestamp(item.event.occurred_at)} UTC</small>
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

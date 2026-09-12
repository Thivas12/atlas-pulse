import { formatTimestamp } from "../event-utils";
import type { IncidentCandidate } from "../types";

interface IncidentFeedProps {
  incidents: IncidentCandidate[];
  selectedIncidentId: string | null;
  candidateEdgesTruncated: boolean;
  onSelect: (incidentId: string) => void;
}

export function IncidentFeed({
  incidents,
  selectedIncidentId,
  candidateEdgesTruncated,
  onSelect,
}: IncidentFeedProps) {
  return (
    <section
      className={`event-feed incident-feed ${candidateEdgesTruncated ? "truncated" : ""}`}
      aria-labelledby="incident-feed-title"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Evidence graph</p>
          <h2 id="incident-feed-title">Correlated clusters</h2>
        </div>
        <span className="record-count">{incidents.length}</span>
      </div>
      {candidateEdgesTruncated && (
        <p className="graph-warning">Edge limit reached · narrow the map to inspect safely</p>
      )}
      <div className="event-list">
        {incidents.length === 0 ? (
          <p className="empty-state">No cross-source co-occurrence in this viewport…</p>
        ) : (
          incidents.map((incident) => (
            <button
              className={`event-row incident-row ${
                selectedIncidentId === incident.incident_id ? "selected" : ""
              }`}
              key={incident.incident_id}
              onClick={() => onSelect(incident.incident_id)}
              type="button"
            >
              <span className="signal-marker graph">{incident.node_count}N</span>
              <span className="event-copy">
                <strong>{incident.title}</strong>
                <small>
                  <span className="incident-sources">
                    {incident.sources.map((source) => (
                      <span className={`source-badge ${source}`} key={source}>
                        {source}
                      </span>
                    ))}
                  </span>
                  {incident.edge_count} edge{incident.edge_count === 1 ? "" : "s"} · max{" "}
                  {incident.max_distance_km.toFixed(1)} km ·{" "}
                  {formatTimestamp(incident.latest_signal_at)} UTC
                </small>
              </span>
              <span className="row-arrow" aria-hidden="true">
                ↗
              </span>
            </button>
          ))
        )}
      </div>
    </section>
  );
}

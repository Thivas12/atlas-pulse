import { formatTimestamp, placeOf, titleOf } from "../event-utils";
import type { IncidentCandidate } from "../types";

interface IncidentDetailProps {
  incident: IncidentCandidate;
  onClose: () => void;
}

function safeEvidenceUrl(value: unknown): string | null {
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

export function IncidentDetail({ incident, onClose }: IncidentDetailProps) {
  const analysis = incident.relationship_analysis;
  return (
    <aside className="event-detail incident-detail" aria-label="Selected incident candidate">
      <button className="detail-close" type="button" onClick={onClose} aria-label="Close details">
        ×
      </button>
      <p className="eyebrow">Evidence graph</p>
      <div className="detail-magnitude graph">
        {incident.node_count} node{incident.node_count === 1 ? "" : "s"} / {incident.edge_count}{" "}
        edge
        {incident.edge_count === 1 ? "" : "s"}
      </div>
      <h2>{incident.title}</h2>
      <ul className="detail-source-list" aria-label="Participating sources">
        {incident.sources.map((source) => (
          <li className={`source-badge ${source}`} key={source}>
            {source}
          </li>
        ))}
      </ul>
      <dl>
        <div>
          <dt>First signal</dt>
          <dd>{formatTimestamp(incident.started_at)} UTC</dd>
        </div>
        <div>
          <dt>Latest signal</dt>
          <dd>{formatTimestamp(incident.latest_signal_at)} UTC</dd>
        </div>
        <div>
          <dt>Maximum edge</dt>
          <dd>{incident.max_distance_km.toFixed(2)} km</dd>
        </div>
        <div>
          <dt>Graph time span</dt>
          <dd>{incident.time_span_minutes.toFixed(1)} min</dd>
        </div>
        <div>
          <dt>Rule</dt>
          <dd>{incident.rule_version}</dd>
        </div>
        <div>
          <dt>Candidate ID</dt>
          <dd>{incident.incident_id}</dd>
        </div>
      </dl>
      <section className="graph-section" aria-labelledby="graph-nodes-title">
        <h3 id="graph-nodes-title">Source evidence</h3>
        <ol className="graph-nodes">
          {incident.nodes.map((node) => {
            const sourceUrl = safeEvidenceUrl(node.event.payload.source_url);
            return (
              <li key={node.node_id}>
                <span className={`source-badge ${node.event.source}`}>{node.event.source}</span>
                <strong>{titleOf(node.event)}</strong>
                <small>
                  {placeOf(node.event)} · {formatTimestamp(node.event.occurred_at)} UTC
                </small>
                {sourceUrl && (
                  <a href={sourceUrl} target="_blank" rel="noreferrer">
                    Open source evidence ↗
                  </a>
                )}
              </li>
            );
          })}
        </ol>
      </section>
      <section className="graph-section" aria-labelledby="graph-edges-title">
        <h3 id="graph-edges-title">Measured edges</h3>
        <ul className="graph-edges">
          {incident.edges.map((edge) => (
            <li key={edge.edge_id}>
              <strong>
                {edge.distance_km.toFixed(2)} km · {edge.time_delta_minutes.toFixed(1)} min
              </strong>
              <small>
                {edge.spatial_relation.replace("_", " ")} · {edge.from_geometry_basis} →{" "}
                {edge.to_geometry_basis}
              </small>
            </li>
          ))}
        </ul>
      </section>
      <section className="graph-section" aria-labelledby="relationship-title">
        <h3 id="relationship-title">Claim relationships</h3>
        <div className="relationship-summary">
          <span className="corroborates">{analysis.corroboration_count} corroborating</span>
          <span className="contradicts">{analysis.contradiction_count} conflicting</span>
          <span className="insufficient-evidence">
            {analysis.insufficient_evidence_count} unresolved
          </span>
        </div>
        <ul className="relationship-list">
          {analysis.relationships.map((relationship) => (
            <li key={relationship.relationship_id}>
              <span className={`relationship-label ${relationship.label}`}>
                {relationship.label.replace("_", " ")}
              </span>
              <strong>
                {relationship.predicate?.replaceAll("_", " ") ?? "No decisive claim pair"}
                {relationship.normalized_value ? ` · ${relationship.normalized_value}` : ""}
              </strong>
              <small>{relationship.rationale}</small>
            </li>
          ))}
        </ul>
      </section>
      <section className="graph-section" aria-labelledby="claims-title">
        <h3 id="claims-title">Normalized source claims</h3>
        {analysis.claims.length === 0 ? (
          <p className="empty-claims">No supported claim extractor matched these nodes.</p>
        ) : (
          <ul className="claim-list">
            {analysis.claims.map((claim) => (
              <li key={claim.claim_id}>
                <strong>
                  {claim.predicate.replaceAll("_", " ")} · {claim.value}
                </strong>
                <small>
                  {claim.node_id} · {claim.evidence_field}
                </small>
                <blockquote>{claim.evidence_excerpt}</blockquote>
                {claim.qualifier && <small>{claim.qualifier}</small>}
              </li>
            ))}
          </ul>
        )}
      </section>
      <p className="detail-summary relationship-caveat">
        {analysis.rule_version} · {analysis.caveat}
      </p>
      <p className="detail-summary graph-caveat">{incident.caveat}</p>
    </aside>
  );
}

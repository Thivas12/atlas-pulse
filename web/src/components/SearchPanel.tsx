import { formatTimestamp, placeOf, titleOf } from "../event-utils";
import type { EvidencePack, SearchHit, SearchResponse } from "../types";

interface SearchPanelProps {
  query: string;
  draft: string;
  useViewport: boolean;
  viewportAvailable: boolean;
  response?: SearchResponse;
  evidencePack?: EvidencePack;
  loading: boolean;
  error: Error | null;
  evidencePackLoading: boolean;
  evidencePackError: Error | null;
  selectedStreamId: string | null;
  onDraftChange: (value: string) => void;
  onUseViewportChange: (value: boolean) => void;
  onSubmit: () => void;
  onBuildEvidencePack: () => void;
  onSelect: (streamId: string) => void;
}

function channelRank(hit: SearchHit): string {
  const channels = [
    hit.ranking.lexical_rank === null ? null : `FTS #${hit.ranking.lexical_rank}`,
    hit.ranking.dense_rank === null ? null : `VECTOR #${hit.ranking.dense_rank}`,
  ].filter((value): value is string => value !== null);
  return channels.join(" · ");
}

export function SearchPanel({
  query,
  draft,
  useViewport,
  viewportAvailable,
  response,
  evidencePack,
  loading,
  error,
  evidencePackLoading,
  evidencePackError,
  selectedStreamId,
  onDraftChange,
  onUseViewportChange,
  onSubmit,
  onBuildEvidencePack,
  onSelect,
}: SearchPanelProps) {
  const items = response?.items ?? [];
  return (
    <section className="search-panel" aria-labelledby="retrieval-title">
      <div className="panel-heading retrieval-heading">
        <div>
          <p className="eyebrow">Hybrid retrieval</p>
          <h2 id="retrieval-title">Search live evidence</h2>
        </div>
        <span className="record-count">{response?.candidates_considered ?? 0} candidates</span>
      </div>
      <form
        className="retrieval-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (draft.trim().length >= 2) onSubmit();
        }}
      >
        <label htmlFor="retrieval-query">Describe the disruption or evidence</label>
        <div className="retrieval-input-row">
          <input
            id="retrieval-query"
            type="search"
            minLength={2}
            maxLength={500}
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            placeholder="e.g. residents ordered to shelter from a violent storm"
          />
          <button type="submit" disabled={draft.trim().length < 2 || loading}>
            {loading ? "Searching…" : "Search"}
          </button>
        </div>
        <label className="viewport-check">
          <input
            type="checkbox"
            checked={useViewport && viewportAvailable}
            disabled={!viewportAvailable}
            onChange={(event) => onUseViewportChange(event.target.checked)}
          />
          Limit to current map viewport
        </label>
        <p>
          Local BGE embeddings · PostgreSQL FTS · pgvector HNSW ·{" "}
          {response === undefined || response.ranking_mode === "hybrid"
            ? "RRF + evidence tie-break"
            : `${response.ranking_mode.toUpperCase()} evaluation mode`}
        </p>
      </form>
      {error && <p className="retrieval-state error">{error.message}</p>}
      {!error && !loading && query && items.length === 0 && (
        <p className="retrieval-state">No current evidence matched this bounded search.</p>
      )}
      {!query && (
        <p className="retrieval-state">
          Search uses semantic and exact-language channels. It returns source events, never a
          generated answer.
        </p>
      )}
      {response && (
        <div className="evidence-pack-control">
          <div className="evidence-pack-heading">
            <div>
              <span>Agent boundary</span>
              <strong>Deterministic evidence handoff</strong>
            </div>
            <button type="button" disabled={evidencePackLoading} onClick={onBuildEvidencePack}>
              {evidencePackLoading ? "Preparing…" : "Prepare agent pack"}
            </button>
          </div>
          {evidencePackError && <p className="error">{evidencePackError.message}</p>}
          {!evidencePack && !evidencePackError && (
            <p>Traceable citations only · 8 items · 12,000 source characters maximum</p>
          )}
          {evidencePack && (
            <div className="evidence-pack-summary">
              <span className={evidencePack.status}>
                {evidencePack.status === "traceable_evidence_available"
                  ? "Traceable evidence available"
                  : "No traceable evidence"}
              </span>
              <code>{evidencePack.pack_id}</code>
              <p>
                {evidencePack.item_count} included · {evidencePack.source_text_characters} source
                characters · {evidencePack.exclusion_count} excluded
              </p>
              <small>{evidencePack.trust_boundary}</small>
            </div>
          )}
        </div>
      )}
      {response && (
        <div className="retrieval-results">
          {items.map((hit) => (
            <article
              className={`retrieval-hit ${selectedStreamId === hit.stream_id ? "selected" : ""}`}
              key={hit.stream_id}
            >
              <button type="button" onClick={() => onSelect(hit.stream_id)}>
                <span className={`source-badge ${hit.event.source}`}>{hit.event.source}</span>
                <strong>{titleOf(hit.event)}</strong>
                <small>
                  {placeOf(hit.event)} · {formatTimestamp(hit.event.occurred_at)} UTC
                </small>
                <span className="rank-line">
                  {channelRank(hit)} · FINAL {hit.ranking.rerank_score.toFixed(3)}
                  {hit.distance_km === null ? "" : ` · ${hit.distance_km.toFixed(1)} km`}
                </span>
              </button>
              <div className="citation-line">
                <span className={hit.citation.status}>{hit.citation.status}</span>
                {hit.citation.url && (
                  <a href={hit.citation.url} target="_blank" rel="noreferrer">
                    Open source evidence ↗
                  </a>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
      {response && <p className="retrieval-caveat">{response.caveat}</p>}
    </section>
  );
}

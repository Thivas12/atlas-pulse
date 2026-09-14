import { formatTimestamp, placeOf, titleOf } from "../event-utils";
import type { AgentRunManifest, EvidencePack, SearchHit, SearchResponse } from "../types";

interface SearchPanelProps {
  query: string;
  draft: string;
  useViewport: boolean;
  viewportAvailable: boolean;
  response?: SearchResponse;
  evidencePack?: EvidencePack;
  agentRunManifest?: AgentRunManifest;
  loading: boolean;
  error: Error | null;
  evidencePackLoading: boolean;
  evidencePackError: Error | null;
  agentRunPreflightLoading: boolean;
  agentRunPreflightError: Error | null;
  selectedStreamId: string | null;
  onDraftChange: (value: string) => void;
  onUseViewportChange: (value: boolean) => void;
  onSubmit: () => void;
  onBuildEvidencePack: () => void;
  onBuildAgentRunPreflight: () => void;
  onSelect: (streamId: string) => void;
}

function channelRank(hit: SearchHit): string {
  const channels = [
    hit.ranking.lexical_rank === null ? null : `FTS #${hit.ranking.lexical_rank}`,
    hit.ranking.dense_rank === null ? null : `VECTOR #${hit.ranking.dense_rank}`,
  ].filter((value): value is string => value !== null);
  return channels.join(" · ");
}

function policyLabel(value: string): string {
  return value.replaceAll("_", " ");
}

export function SearchPanel({
  query,
  draft,
  useViewport,
  viewportAvailable,
  response,
  evidencePack,
  agentRunManifest,
  loading,
  error,
  evidencePackLoading,
  evidencePackError,
  agentRunPreflightLoading,
  agentRunPreflightError,
  selectedStreamId,
  onDraftChange,
  onUseViewportChange,
  onSubmit,
  onBuildEvidencePack,
  onBuildAgentRunPreflight,
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
          {evidencePack && (
            <div className="agent-run-control">
              <div className="agent-run-heading">
                <div>
                  <span>Execution gate</span>
                  <strong>Governed evidence-triage preflight</strong>
                </div>
                <button
                  type="button"
                  disabled={agentRunPreflightLoading}
                  onClick={onBuildAgentRunPreflight}
                >
                  {agentRunPreflightLoading ? "Checking…" : "Check run policy"}
                </button>
              </div>
              {agentRunPreflightError && <p className="error">{agentRunPreflightError.message}</p>}
              {!agentRunManifest && !agentRunPreflightError && (
                <p>
                  Fresh pack + manifest pair · default deny · agent model, network, and tools stay
                  off
                </p>
              )}
              {agentRunManifest && (
                <div className="agent-run-summary">
                  <span className="agent-run-state blocked">Blocked · no execution</span>
                  <code>{agentRunManifest.manifest_id}</code>
                  <small className="agent-run-approval-state">
                    Approval: {policyLabel(agentRunManifest.approval.status)} · proposal{" "}
                    {agentRunManifest.proposal_id.slice(0, 21)}…
                  </small>
                  <p>
                    {agentRunManifest.authorization.passed_check_count} checks passed ·{" "}
                    {agentRunManifest.authorization.blocked_check_count} blocking gates
                  </p>
                  <p className="blocking-reasons">
                    {agentRunManifest.authorization.blocking_reasons.map(policyLabel).join(" · ")}
                  </p>
                  <details>
                    <summary>Review authorization checks</summary>
                    <ul>
                      {agentRunManifest.authorization.checks.map((check) => (
                        <li className={check.status} key={check.check_id}>
                          <span className="agent-check-name">{policyLabel(check.check_id)}</span>
                          <strong className="agent-check-status">{check.status}</strong>
                          <small className="agent-check-detail">
                            {policyLabel(check.observed)} → {policyLabel(check.required)}
                          </small>
                        </li>
                      ))}
                    </ul>
                  </details>
                  <small className="agent-run-execution-note">
                    Agent model not invoked · no agent network, tools, answer, or side effect
                  </small>
                </div>
              )}
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

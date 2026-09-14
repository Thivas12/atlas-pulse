import type { SourcePollHistoryResponse, SourcePollTransition } from "../types";

const SOURCE_LABELS: Record<SourcePollTransition["source"], string> = {
  firms: "NASA FIRMS",
  gdelt: "GDELT",
  nws: "NOAA / NWS",
  usgs: "USGS",
};

const TRANSITION_LABELS: Record<SourcePollTransition["transition"], string> = {
  failed: "Failed",
  started: "Started",
  succeeded: "Succeeded",
};

function formatTimestamp(value: string): string {
  return new Date(value).toISOString().replace("T", " ").replace(".000Z", "Z");
}

function formatCode(value: string): string {
  return value.replaceAll("_", " ");
}

function transportLabel(count: number): string {
  return `${count} transport ${count === 1 ? "attempt" : "attempts"}`;
}

function transitionDetail(record: SourcePollTransition): string {
  const { attempt } = record;
  if (record.transition === "started") {
    return `Entered ${formatCode(attempt.stage)} · awaiting terminal outcome`;
  }
  if (record.transition === "failed") {
    return `${formatCode(attempt.failure_code ?? "unexpected_failure")} at ${formatCode(attempt.stage)} · ${transportLabel(attempt.transport_attempts)}`;
  }
  return `${attempt.fetched_events ?? 0} fetched · ${attempt.published_events ?? 0} published · ${attempt.deduplicated_events ?? 0} deduplicated · ${transportLabel(attempt.transport_attempts)}`;
}

function TransitionRow({ record }: { record: SourcePollTransition }) {
  const observedAt = record.attempt.completed_at ?? record.attempt.started_at;
  return (
    <li className={`poll-transition ${record.transition}`}>
      <span className="poll-transition-marker" aria-hidden="true" />
      <div className="poll-transition-main">
        <div className="poll-transition-title">
          <strong>{SOURCE_LABELS[record.source]}</strong>
          <b>{TRANSITION_LABELS[record.transition]}</b>
        </div>
        <p>{transitionDetail(record)}</p>
      </div>
      <div className="poll-transition-identity">
        <time dateTime={observedAt}>{formatTimestamp(observedAt)}</time>
        <span>attempt {record.attempt.attempt_id.slice(-8)}</span>
      </div>
    </li>
  );
}

interface SourcePollHistoryProps {
  response: SourcePollHistoryResponse | undefined;
  loading: boolean;
  error: Error | null;
}

export function SourcePollHistory({ response, loading, error }: SourcePollHistoryProps) {
  const unavailable = error !== null || (!loading && response === undefined);

  return (
    <section className="source-poll-history" aria-labelledby="source-poll-history-title">
      <div className="source-poll-history-heading">
        <div>
          <p className="source-poll-history-eyebrow">Recovery evidence</p>
          <h2 id="source-poll-history-title">Recent poll transitions</h2>
          <p>Newest first. Starts and terminal outcomes retain the same attempt identity.</p>
        </div>
        <div className="poll-history-summary" aria-live="polite">
          <span className={unavailable ? "unknown" : "retained"}>
            {loading && !response
              ? "Loading"
              : unavailable
                ? "Unavailable"
                : `${response?.count ?? 0} retained`}
          </span>
          <small>
            {response?.has_more ? "Older retained entries available" : "Bounded ledger view"}
          </small>
        </div>
      </div>

      {loading && !response ? (
        <div className="poll-history-message">Loading retained poll transitions…</div>
      ) : unavailable ? (
        <div className="poll-history-message error" role="alert">
          Poll history is unavailable. Current freshness above remains independently evaluated.
        </div>
      ) : response && response.items.length > 0 ? (
        <ol className="poll-transition-list">
          {response.items.map((record) => (
            <TransitionRow key={record.stream_id} record={record} />
          ))}
        </ol>
      ) : (
        <div className="poll-history-message">No poll transitions are retained yet.</div>
      )}
      {response && !unavailable ? (
        <p className="source-poll-history-caveat">{response.caveat}</p>
      ) : null}
    </section>
  );
}

import type { SourceFreshnessItem, SourceFreshnessResponse } from "../types";

const SOURCE_LABELS: Record<SourceFreshnessItem["source"], string> = {
  firms: "NASA FIRMS",
  gdelt: "GDELT",
  nws: "NOAA / NWS",
  usgs: "USGS",
};

const POLL_LABELS: Record<SourceFreshnessItem["poll_status"], string> = {
  clock_skew: "Clock skew",
  degraded: "Degraded",
  healthy: "Healthy",
  stale: "Stale",
  starting: "Starting",
};

const DATA_LABELS: Record<SourceFreshnessItem["source_data_status"], string> = {
  current: "Current",
  future_clock_skew: "Clock skew",
  not_reported: "Not reported",
  stale: "Stale",
};

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "Not reported";
  const future = seconds < 0;
  const absolute = Math.abs(seconds);
  let duration: string;
  if (absolute < 60) {
    duration = `${Math.round(absolute)}s`;
  } else if (absolute < 3_600) {
    duration = `${Math.floor(absolute / 60)}m ${Math.round(absolute % 60)}s`;
  } else if (absolute < 86_400) {
    duration = `${Math.floor(absolute / 3_600)}h ${Math.floor((absolute % 3_600) / 60)}m`;
  } else {
    duration = `${Math.floor(absolute / 86_400)}d ${Math.floor((absolute % 86_400) / 3_600)}h`;
  }
  return future ? `${duration} in future` : `${duration} ago`;
}

function formatThreshold(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3_600) return `${seconds / 60}m`;
  if (seconds < 86_400) return `${seconds / 3_600}h`;
  return `${seconds / 86_400}d`;
}

function formatTimestamp(value: string): string {
  return new Date(value).toISOString().replace("T", " ").replace(".000Z", "Z");
}

function formatCode(value: string): string {
  return value.replaceAll("_", " ");
}

function SourceCard({ item }: { item: SourceFreshnessItem }) {
  const attemptSummary =
    item.last_attempt_at === null
      ? "No poll attempt recorded"
      : `${formatCode(item.last_outcome ?? "unknown")} · ${formatTimestamp(item.last_attempt_at)}`;
  const detail = item.last_failure_code
    ? `Failure: ${formatCode(item.last_failure_code)} at ${formatCode(item.last_stage ?? "unknown")}`
    : item.timestamp_basis
      ? `Timestamp basis: ${formatCode(item.timestamp_basis)}`
      : "Waiting for first successful source timestamp";

  return (
    <article
      className={`source-health-card ${item.passed ? "passed" : "attention"}`}
      aria-label={`${SOURCE_LABELS[item.source]} freshness`}
    >
      <header>
        <div>
          <span className="source-health-name">{SOURCE_LABELS[item.source]}</span>
          <small className="source-health-attempt">{attemptSummary}</small>
        </div>
        <span className={`source-health-result ${item.passed ? "passed" : "attention"}`}>
          {item.passed ? "Current" : "Attention"}
        </span>
      </header>

      <div className="freshness-dimensions">
        <div>
          <span className="freshness-dimension-label">Poll heartbeat</span>
          <strong className={`freshness-state ${item.poll_status}`}>
            {POLL_LABELS[item.poll_status]}
          </strong>
          <small className="freshness-threshold">
            every {formatThreshold(item.interval_seconds)} · stale after{" "}
            {formatThreshold(item.poll_stale_after_seconds)}
          </small>
        </div>
        <div>
          <span className="freshness-dimension-label">Upstream data</span>
          <strong className={`freshness-state ${item.source_data_status}`}>
            {DATA_LABELS[item.source_data_status]}
          </strong>
          <small className="freshness-threshold">
            {formatDuration(item.source_age_seconds)} · limit{" "}
            {formatThreshold(item.source_stale_after_seconds)}
          </small>
        </div>
      </div>

      <div className="source-health-meta">
        <span className="source-health-detail">{detail}</span>
        <span className="source-health-attempts">
          {item.transport_attempts} transport{" "}
          {item.transport_attempts === 1 ? "attempt" : "attempts"}
          {item.consecutive_failures > 0
            ? ` · ${item.consecutive_failures} consecutive failures`
            : ""}
        </span>
      </div>
    </article>
  );
}

interface SourceFreshnessPanelProps {
  response: SourceFreshnessResponse | undefined;
  loading: boolean;
  error: Error | null;
}

export function SourceFreshnessPanel({ response, loading, error }: SourceFreshnessPanelProps) {
  const passingSources = response?.items.filter((item) => item.passed).length ?? 0;

  return (
    <section className="source-freshness-panel" aria-labelledby="source-freshness-title">
      <div className="source-freshness-heading">
        <div>
          <p className="source-freshness-eyebrow">Operations beacon</p>
          <h2 id="source-freshness-title">Poll heartbeat and upstream age</h2>
          <p>
            Independent evidence. A visible event is not proof that its source is still polling.
          </p>
        </div>
        <div className="freshness-summary" aria-live="polite">
          <span className={error ? "unknown" : response?.passed ? "passed" : "attention"}>
            {loading
              ? "Checking"
              : error || !response
                ? "Unknown"
                : response.passed
                  ? "All current"
                  : "Attention"}
          </span>
          <small>
            {response
              ? `${passingSources} / ${response.items.length} sources pass · ${formatTimestamp(response.generated_at)}`
              : "Point-in-time API evidence"}
          </small>
        </div>
      </div>

      {loading && !response ? (
        <div className="freshness-message">Checking source poll evidence…</div>
      ) : error || !response ? (
        <div className="freshness-message error" role="alert">
          Source freshness evidence is unavailable. Event visibility below is not a substitute.
        </div>
      ) : (
        <>
          <div className="source-health-grid">
            {response.items.map((item) => (
              <SourceCard item={item} key={item.source} />
            ))}
          </div>
          <p className="source-freshness-caveat">{response.caveat}</p>
        </>
      )}
    </section>
  );
}

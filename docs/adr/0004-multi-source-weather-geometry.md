# ADR 0004: Isolate source adapters and preserve NWS alert geometry

- Status: Accepted
- Date: 2026-09-12

## Context

AtlasPulse must add authoritative live sources without coupling transport, scheduling, schema,
or geometry rules to USGS. NOAA/NWS active alerts use CAP semantics inside GeoJSON. Rapid-onset
warnings commonly include a `Polygon`; county watches and broad forecast-zone alerts can have
`geometry: null` and instead identify areas through UGC/SAME codes and zone URLs. Treating every
record as a point would invent precision, while dropping null geometry would hide valid alerts.

The NWS API is public and keyless but requires an identifiable `User-Agent`. Its active-alert
collection is also substantially larger than the USGS hourly earthquake feed.

## Decision

Define a source adapter as four explicit capabilities: source name, snapshot extension, fetch,
and normalize. Use one shared HTTP transport for identity headers, timeout, retry classification,
and ownership-aware cleanup. Run one polling task per adapter with independent intervals and
failure handling. Keep snapshot-before-validation and the shared `Event`/Valkey publication
boundary source-neutral.

Poll `https://api.weather.gov/alerts/active?status=actual` every 120 seconds by default. This
excludes Test/Exercise records but deliberately retains both Alert and Update messages. Send
`Accept: application/geo+json` and a configurable identifying User-Agent.

Validate CAP status, message type, severity, certainty, urgency, category, timestamps, and
GeoJSON ring invariants. Preserve the complete source geometry in `payload.geometry`. For a
polygon, calculate a deterministic bounding-box center only for map focus; the polygon remains
the spatial evidence. For null geometry, keep the event and its UGC/SAME/affected-zone fields,
leave `location` null, and label it area-only in the UI. Live mode hides records after
`expires_at`; replay keeps the historical revision.

## Consequences

- A source outage or slow request cannot delay another source's polling loop.
- Adding another provider requires a source schema and adapter, not changes to ingestion
  orchestration.
- Polygon alerts render with severity-aware fills while point events retain clustering.
- AtlasPulse never claims point accuracy for zone-encoded alerts.
- The bbox center is not a centroid and must not be used for impact computation.
- Identical response bytes reuse a content-addressed snapshot, but changed NWS collections can
  grow raw storage. Automated retention and zone-boundary enrichment remain explicit future
  operational work.

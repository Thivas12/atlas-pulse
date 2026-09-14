# ADR 0027: Explicit source-poll and upstream freshness evidence

- Status: accepted
- Date: 2026-09-14

## Context

The public event stream proves that an event revision was published, but it cannot prove that a
source is still being polled. Identical responses are deliberately deduplicated, and some valid
feeds can contain no active records. Treating the latest visible event as a poll heartbeat would
therefore turn correct idempotency and quiet sources into false outages while also hiding a stuck
worker behind an old event.

AtlasPulse needs evidence that distinguishes three times: the worker's poll attempt, the last
successful ingestion, and the upstream timestamp reported by the source. It must retain the
project's zero-cost deployment and closed model/agent execution boundaries.

## Decision

1. Every enabled adapter receives a canonical policy with its expected interval, a poll-heartbeat
   stale threshold, and an independent upstream-data stale threshold. The default heartbeat bound
   is three poll intervals.
2. The ingestion worker writes a start and one terminal transition around the existing
   fetch → snapshot → normalize → publish boundary. Terminal records contain only bounded status,
   stage, attempt count, timestamp provenance, and event counters. They never retain response
   bodies, URLs, credentials, or exception text.
3. Valkey stores the latest state per source and a bounded transition stream. Lua scripts reject
   duplicate, older, or superseded transitions atomically. A successful completion updates the
   last-success timestamp and resets consecutive failures; a failure preserves the last successful
   source timestamp and increments the counter.
4. Adapters identify upstream time as `source_metadata`, `latest_record`, or `fetch_fallback`.
   HTTP transport errors carry the actual bounded attempt count, including both GDELT manifest and
   export requests.
5. `GET /v1/source-freshness` evaluates state using the API host's UTC clock and reports two
   independent statuses. Poll state is `healthy`, `degraded`, `stale`, `starting`, or `clock_skew`;
   upstream data is `current`, `stale`, `not_reported`, or `future_clock_skew`. A source passes only
   when both dimensions are healthy/current.
6. Operational probes add the freshness endpoint to their fixed same-origin allowlist and retain
   its strict typed observations. The sampled campaign requires every deployment-target source to
   pass freshness on 30 consecutive UTC dates. Bounded event visibility remains descriptive and
   no longer substitutes for polling evidence.
7. Readiness semantics remain dependency-focused. A stale upstream does not restart the API or
   erase replay data; it is exposed explicitly for degraded/replay operation and operator action.

## Consequences

- Quiet or deduplicated feeds can demonstrate a live worker without manufacturing events.
- Poll failures, stale workers, stale publisher timestamps, startup, and clock skew are separately
  inspectable and cannot be collapsed into one optimistic health flag.
- Valkey remains the only added runtime dependency, so the OCI Always Free and local Compose paths
  add no paid service.
- The public result is still sampled, host-clock-dependent evidence—not an SLA, independent monitor,
  upstream completeness guarantee, or software attestation.
- No model, agent, tool, approval, or execution-release behavior changes. Execution remains disabled.

# ADR 0001: Use Valkey Streams for the first event bus

- Status: Accepted
- Date: 2026-09-12

## Context

The first AtlasPulse slice must run on a student workstation and a zero-cost deployment while
still demonstrating replay, consumer groups, backpressure, bounded retention, health checks,
and duplicate-delivery handling. USGS updates the selected feed once per minute, so the initial
throughput does not justify a multi-broker Kafka-compatible cluster.

## Decision

Use Valkey Streams behind the small `EventBus` protocol. Fingerprint canonical event content
while excluding the poll-specific ingestion timestamp, so identical deliveries collapse but a
source correction becomes a new immutable revision. Publish with a server-side Lua script that
checks the SHA-256 fingerprint, appends the serialized shared event, and records the stream ID in
one atomic operation. Keep the stream and dedupe keys in the `{atlas}` hash slot so the key layout
remains compatible with a future clustered topology.

Use an approximate maximum stream length of 100,000 and expire dedupe records after seven days
by default. Preserve source bytes outside the stream so stream retention never destroys the
audit trail.

## Consequences

- Local and CI environments need only one lightweight open-source service.
- Consumers gain stable replay positions and can later use native consumer groups.
- Idempotency is explicit and testable, including against a real Valkey process in CI.
- The stream is not an indefinite system of record; snapshots and future analytical storage
  fill that role.
- The seven-day window is idempotent, not global exactly-once delivery.
- Revisit Redpanda/Kafka when measured throughput, independent retention policies, or a larger
  consumer topology makes the additional operational cost worthwhile.

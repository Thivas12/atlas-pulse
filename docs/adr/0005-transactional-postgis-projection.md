# ADR 0005: Project current disruption state transactionally into PostGIS

- Status: Accepted
- Date: 2026-09-12

## Context

Valkey Streams provide low-latency immutable delivery and deterministic replay, but a bounded
stream is not the right query engine for current-state identity, time, severity, expiry, and
polygon intersection. Reading hundreds of raw revisions into the browser also makes response
cost grow with history and leaves de-duplication to UI code.

The projection must survive a worker restart without skipping committed evidence or producing a
partially advanced cursor. It must preserve source corrections as immutable revisions while
showing only the newest known revision for each `(source, event_id)` identity.

## Decision

Use PostgreSQL 17 with PostGIS 3.5 as the durable query store. Keep three explicit relations:

- `event_revisions` stores every stream revision once, including authoritative event JSON and
  extracted query columns. Points and source polygons have WGS84 GiST indexes.
- `current_signals` maps each source identity to its newest numeric Valkey stream position.
- `projection_checkpoints` records the last position committed by a named projection.

Run a dedicated projector, independent of ingestion and API processes. Each bounded cycle reads
Valkey oldest-first strictly after the database checkpoint. In one PostgreSQL transaction it
inserts immutable revisions idempotently, advances current pointers only when the incoming
numeric stream position is newer, and advances the checkpoint monotonically to the batch tail.
The database also constrains each current pointer to a revision with the same source identity.

On first start, a missing checkpoint means backfill begins at the oldest retained stream entry.
A failure before transaction commit leaves the checkpoint unchanged, so the entire batch is
retried. A failure after commit resumes strictly after the committed tail. This is explicit
at-least-once projection with idempotent materialization, not an exactly-once claim.

Expose current state through `/v1/signals`, ordered newest revision first with an exclusive
keyset cursor. Translate source, severity, aware occurrence-time, active-expiry, and non-wrapping
WGS84 viewport filters into SQL. A viewport includes source points or polygons that intersect
its envelope. Geometry-less area-code alerts remain queryable through `include_area_only`; the
map omits them rather than inventing coordinates.

Apply schema changes with Alembic before API and projector startup. Keep PostGIS in the free
local Compose stack and run both mocked unit tests and a real PostGIS integration proof in CI.

## Consequences

- Live response size scales with current signals and the viewport, not retained revision count.
- Source corrections remain auditable while the dashboard receives one current row per identity.
- Database availability is part of API readiness but not process liveness.
- Valkey retention still bounds automatic first-run backfill. Rebuilding history older than the
  retained stream requires the future raw-snapshot reprocessor.
- Non-wrapping bounding boxes keep SQL and cache keys simple; dateline-crossing viewports must be
  split into two requests in a future global-map refinement.
- Projection lag is observable through cycle counts and checkpoint attributes; a dedicated lag
  endpoint and alert threshold remain future operational work.

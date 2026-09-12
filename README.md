# AtlasPulse

[![CI](https://github.com/Thivas12/atlas-pulse/actions/workflows/ci.yml/badge.svg)](https://github.com/Thivas12/atlas-pulse/actions/workflows/ci.yml)

Real-time, evidence-grounded global disruption intelligence built from authoritative public
data. AtlasPulse is designed as a production system, not a notebook: source bytes remain
auditable, contracts are strict, delivery is replayable, failures are observable, and every
component can run without a paid API key.

> **Current milestone — satellite-aware, spatially queried disruption state.** Independent
> workers poll official USGS earthquakes every 60 seconds, NOAA/NWS actual alerts every 120
> seconds, and opt-in NASA FIRMS VIIRS thermal anomalies every 15 minutes. Every unmodified
> response is preserved, strictly validated, normalized, and atomically published to Valkey
> Streams. A restart-safe worker transactionally projects every revision, current event pointer,
> and its checkpoint into PostGIS. The dashboard reads de-duplicated live state by source and map
> viewport while preserving deterministic replay.

## Why this is portfolio-grade

| Capability | Concrete proof in this repository |
| --- | --- |
| Real public data | Independent USGS, NOAA/NWS, and NASA FIRMS near-real-time feeds |
| Auditability | SHA-256 content-addressed raw snapshots are written before parsing |
| Reliable delivery | Bounded HTTP retry plus pipelined, revision-aware atomic Lua deduplication |
| Credential safety | Free FIRMS key is ingestor-only and redacted from events, errors, and spans |
| Shared contracts | Immutable `Event` and `GeoPoint` models pinned to `agent-rag-core` commit `7732801` |
| Deterministic replay | Exclusive Valkey Stream cursors page retained history oldest-first without boundary duplicates |
| Durable current state | Immutable PostgreSQL revisions plus atomic current pointers and restart-safe checkpoint |
| Spatial access | Indexed PostGIS point/polygon intersection, severity, source, time, expiry, and keyset filters |
| Operations | Liveness, dependency readiness, JSON logs, OpenTelemetry traces, graceful shutdown |
| Decision UI | Mixed-geometry map, source filters, severity metrics, live expiry, replay, evidence links |
| Engineering quality | Strict mypy/TypeScript, locked dependencies, branch coverage, real Valkey/PostGIS CI |
| Supply-chain hygiene | Read-only workflow permissions, commit-pinned Actions, weekly dependency updates |

## Architecture

```mermaid
flowchart TD
    USGS["USGS earthquakes"] --> Adapters["Independent source adapters"]
    NWS["NWS active alerts"] --> Adapters
    FIRMS["NASA FIRMS VIIRS"] --> Adapters
    Adapters --> Raw["Immutable raw snapshots"]
    Adapters --> Validate["Strict source validation"]
    Validate --> Contract["Shared Event contract"]
    Contract --> Stream["Valkey Streams + atomic dedupe"]
    Stream --> Projector["Restart-safe projector"]
    Projector --> PostGIS["PostGIS revisions + current state"]
    PostGIS --> API["FastAPI current-state API"]
    Stream --> API
    API --> Web["Viewport-driven command center"]
    Stream --> Agents["RAG and agent consumers — next milestones"]
```

Each source is at-least-once and failure-isolated: a slow or unavailable source cannot stop the
other pollers. Identical semantic content is idempotent for the configured seven-day dedupe
window, while a source correction remains a new immutable revision. AtlasPulse deliberately does
not claim impossible end-to-end "exactly once" semantics.

Projection is also at-least-once. On every cycle, the worker reads strictly after the checkpoint
stored in PostgreSQL, then commits immutable revisions, newer-only current pointers, and the new
checkpoint in one transaction. A crash before commit replays the batch; idempotent constraints
make that safe. A crash after commit resumes after it. The first run backfills the oldest entries
still retained by Valkey.

## Run the full slice

Prerequisites: Docker Engine with Compose v2. Docker Desktop is fine for personal or
educational use; Podman is a fully open-source alternative.

```bash
git clone https://github.com/Thivas12/atlas-pulse.git
cd atlas-pulse
docker compose up --build
```

That zero-configuration command runs USGS and NWS. NASA FIRMS needs a free, email-issued
`MAP_KEY` because its servers meter transactions. Request one from the
[official FIRMS key page](https://firms.modaps.eosdis.nasa.gov/api/map_key/), then enable only the
ingestor-facing credential:

```bash
cp .env.example .env
# Edit .env: set ATLAS_FIRMS_ENABLED=true and ATLAS_FIRMS_MAP_KEY=<your key>
docker compose up --build
```

Do not commit `.env`; it is ignored by Git. The key lives only in the ingestor container and is
removed from source metadata, raised errors, and OpenTelemetry URL attributes.

Compose waits for PostGIS, applies Alembic migrations once, and starts the ingestor, projector,
API, and web edge. After the first ingestion and projection cycles, open the command center at
<http://localhost:3000>. The API and its operational probes remain directly available:

```bash
curl -s http://localhost:8000/healthz
curl -s http://localhost:8000/readyz
curl -s 'http://localhost:8000/v1/events?limit=5'
curl -s 'http://localhost:8000/v1/events/replay?limit=5'
curl -s 'http://localhost:8000/v1/signals?limit=5&active_only=true'
curl -s 'http://localhost:8000/v1/signals?source=nws&min_severity=3&bbox=-125,24,-66,50'
curl -s 'http://localhost:8000/v1/signals?source=firms&bbox=-120,33,-117,36'
```

Switch between **Live** and **Replay**, then filter **All**, **Earthquakes**, **Weather**, or
**Fires**.
Live mode is served from current PostGIS state, omits expired alerts/detections, and refreshes the
map with an indexed bounding-box query after every settled pan or zoom. Geometry-less NWS alerts
remain in the global feed without being falsely placed on the map. Replay starts
at the oldest retained stream entry; its controls operate on cursor-paged history. Interactive
OpenAPI documentation is at <http://localhost:8000/docs>. Stop the stack with
`docker compose down`. Add `--volumes` only when you intentionally want to delete local stream,
PostGIS, and raw snapshot data.

## Develop without rebuilding containers

[uv](https://docs.astral.sh/uv/) manages Python and installs the exact lockfile. Keep only
Valkey and PostGIS in Docker and run the Python processes in WSL:

```bash
cp .env.example .env
docker compose up -d valkey postgres
uv sync --frozen --all-groups
uv run alembic upgrade head
ATLAS_VALKEY_URL=valkey://localhost:6379/0 uv run atlas-pulse-ingest
```

In a second terminal, follow the retained stream into PostGIS:

```bash
ATLAS_VALKEY_URL=valkey://localhost:6379/0 \
ATLAS_DATABASE_URL=postgresql+asyncpg://atlas:atlas@localhost:5432/atlas \
  uv run atlas-pulse-project
```

In a third terminal:

```bash
ATLAS_VALKEY_URL=valkey://localhost:6379/0 \
  uv run uvicorn atlas_pulse.asgi:app --reload --port 8000
```

Run the dashboard with Vite in a fourth terminal. Its development proxy forwards `/api` to the
local FastAPI process:

```bash
cd web
npm ci
npm run dev
```

Run the same quality gate as CI:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest --cov=atlas_pulse --cov-branch --cov-report=term-missing
cd web
npm run lint
npm run test:coverage
npm run build
```

The real-service integration tests activate when their explicit test URLs are present:

```bash
ATLAS_TEST_VALKEY_URL=valkey://localhost:6379/0 \
ATLAS_TEST_DATABASE_URL=postgresql+asyncpg://atlas:atlas@localhost:5432/atlas \
  uv run pytest -m integration
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Process liveness; does not depend on Valkey |
| `GET` | `/readyz` | Returns `503` when Valkey or PostGIS is unavailable |
| `GET` | `/v1/events?limit=50` | Newest normalized events and their replay IDs |
| `GET` | `/v1/events/replay?limit=100&after=<stream-id>` | Oldest-first page strictly after an optional cursor |
| `GET` | `/v1/signals?limit=100&after=<stream-id>` | Newest-first, de-duplicated current signals with keyset pagination |

`/v1/signals` accepts `source=usgs|nws|firms`, `min_severity=0..4`, aware
`occurred_after`/`occurred_before` timestamps, `active_only`, and a non-wrapping WGS84
`bbox=west,south,east,north`. `min_severity` applies to NWS severity ranks. Spatial requests return
intersecting point or polygon evidence.
Set `include_area_only=true` only when a viewport consumer explicitly wants valid NWS alerts
that have area codes but no source geometry.

Every event contains a stable source ID, an aware occurrence time, ingestion time, semantic
type, optional WGS84 focus point, source name, schema version, and JSON-safe payload. USGS depth
is kept as positive-down `payload.depth_km`; the shared geographic altitude is its negative
value. NWS `Polygon`/`MultiPolygon` geometry is preserved in `payload.geometry`; a deterministic
bounding-box center is only a map focus. Official alerts whose geometry is `null` remain in the
feed with UGC/SAME codes and are reported as area-only rather than silently discarded.
FIRMS rows become stable `fire.thermal_anomaly` point events with satellite, product, confidence,
brightness, day/night, and fire-radiative-power evidence. A deterministic identity hash excludes
revisable measurements, so a corrected measurement becomes a new revision of the same detection.
AtlasPulse applies a documented 24-hour display window; this is an operational freshness rule,
not a NASA-declared incident closure. A satellite thermal anomaly can be fire or another heat
source and is never presented as a confirmed wildfire perimeter.
Replay requests read one extra entry to compute `has_more`, return at most 500 items, and expose
the final visible stream ID as `next_cursor`. Stream retention is bounded, so replay is
deterministic for retained entries rather than an indefinite event archive.

## Failure behavior

- HTTP transport errors, `429`, and `5xx` responses retry with bounded exponential backoff.
- Permanent non-success responses fail immediately; `429` and `5xx` are the retryable exceptions.
- USGS, NWS, and enabled FIRMS poll on separate async tasks, so one failure does not block others.
- Valid HTTP bodies are snapshotted before schema parsing, so upstream schema drift remains
  inspectable.
- A canonical content fingerprint ignores poll time but preserves source revisions; pipelined Lua
  transactions make each lookup, stream append, and registration atomic without one network
  round trip per detection.
- `MAXLEN ~ 100000` bounds local stream growth; original snapshots remain independently
  retained.
- Snapshots are content-addressed, so byte-identical polls consume no additional space. Raw
  retention is currently operator-managed; NWS defaults to a two-minute poll because its active
  collection is materially larger than the USGS hourly feed.
- FIRMS defaults to one day of global NOAA-20 VIIRS NRT detections every 15 minutes. Its API
  permits day ranges from one to five; narrow `ATLAS_FIRMS_AREA` for smaller deployments.
- Revision insert, newer-only current pointer, and projection checkpoint commit together. A
  failed batch is retried from the unchanged cursor and duplicate revision inserts are harmless.
- Readiness fails closed when the stream or durable query store is unavailable; liveness remains
  available.

See [ADR 0001](docs/adr/0001-use-valkey-streams.md) for the event-bus decision,
[ADR 0002](docs/adr/0002-snapshot-before-validation.md) for the evidence boundary, and
[ADR 0003](docs/adr/0003-cursor-based-replay.md) for replay semantics, and
[ADR 0004](docs/adr/0004-multi-source-weather-geometry.md) for source isolation and NWS geometry,
and [ADR 0005](docs/adr/0005-transactional-postgis-projection.md) for durable projection and
checkpoint semantics, and [ADR 0006](docs/adr/0006-firms-thermal-anomaly-ingestion.md) for FIRMS
identity, expiry, and credential boundaries.
A reproducible
[60-second demo](docs/demo.md) is included for project reviews.

## Free stack

No part of this milestone needs a paid model, dataset, API, or hosted service. FIRMS uses a free
credential solely for transaction metering.

| Layer | Tool/data | Cost for this project |
| --- | --- | --- |
| Live source | [USGS Earthquake GeoJSON feeds](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php) | Public, no key |
| Live source | [NOAA/NWS API](https://www.weather.gov/documentation/services-web-api) | Public, no key; identifying User-Agent required |
| Live source | [NASA FIRMS Area API](https://firms.modaps.eosdis.nasa.gov/api/area/) | Public data; free MAP_KEY, no paid tier required |
| API/contracts | Python, FastAPI, Pydantic | Open source |
| Event stream | Valkey + `valkey-py` | Open source |
| Durable geospatial state | PostgreSQL + PostGIS + SQLAlchemy/Alembic | Open source |
| Web command center | React, TypeScript, TanStack Query, Zod | Open source |
| Geospatial UI | MapLibre GL + OpenFreeMap/OpenStreetMap | Open source/public, no key |
| Static serving | Caddy | Open source |
| Observability | OpenTelemetry | Open source; console export by default |
| Toolchain | uv, Ruff, mypy, pytest, Vite, Vitest, Biome | Open source |
| Runtime | Docker Engine/Compose or Podman | Free/open-source options |
| CI | GitHub Actions on this public repository | Free hosted runners for public repos |

## Next milestones

1. GDELT news signals using the same source-adapter contract and durable projection boundary.
2. Hybrid sparse/dense/geospatial retrieval, reranking, temporal filtering, and citation
   verification.
3. A hierarchy of specialist agents for signal fusion, contradiction detection, impact
   assessment, forecasting, and human approval.
4. Evaluation datasets, RAGAS-style retrieval metrics, agent trajectory scoring, drift
   monitoring, and a fully free deployment path.

## Data and attribution

Earthquake data comes from the
[U.S. Geological Survey](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php).
Weather alerts come from the [NOAA/National Weather Service API](https://www.weather.gov/documentation/services-web-api),
using `status=actual` so exercises and tests are excluded while Alert and Update message types
remain visible. NWS requires an identifiable User-Agent; configure
`ATLAS_SOURCE_USER_AGENT` with a public project or contact URL before deployment. Follow all
providers' policies before operating a public mirror. Frozen fixtures are synthetic
source-shaped records, not claims about real events.

Thermal-anomaly observations come from NASA's
[Fire Information for Resource Management System](https://firms.modaps.eosdis.nasa.gov/). FIRMS
states that global NRT detections are generally available within three hours of satellite
observation, with faster RT/URT availability for the US and Canada. FIRMS detections identify
active-fire/hotspot or thermal-anomaly pixels; AtlasPulse preserves that uncertainty. The MAP_KEY
is free and the official documented limit is 5,000 transactions per ten-minute interval. The
repository contains only synthetic FIRMS-shaped fixtures and never a real key.

Licensed under the [MIT License](LICENSE).

# AtlasPulse: 60-second review demo

Start from a populated local stack with `docker compose up -d --build` and open
<http://localhost:3000>.

## 0–20 seconds: prove durable current state and spatial loading

Point out separate USGS/NWS freshness, earthquake points, and severity-colored NWS polygons.
Switch among **All**, **Earthquakes**, and **Weather**. Select a weather signal and show its CAP
severity, urgency, certainty, expiry, geometry status, issuing office, stable event ID, replay ID,
and direct NWS evidence link. Point out an area-code-only record if one is active: it stays in the
feed without fabricated coordinates. Pan or zoom the map and show the viewport request in the
browser network panel: live state is filtered by indexed PostGIS geometry rather than downloading
the full revision stream.

```bash
curl -fsS 'http://localhost:8000/v1/signals?source=nws&min_severity=3&bbox=-125,24,-66,50' \
  | jq '{count, next_cursor, has_more, order}'
```

## 20–38 seconds: prove the history is deterministic

Select **Replay**. Explain that the API reads Valkey Streams oldest-first and that each next page
starts strictly after its cursor. Move the slider, press play, and change from 1× to 4×. The map
and feed now represent exactly the visible replay prefix.

In a terminal, make the contract observable:

```bash
page="$(curl -fsS 'http://localhost:8000/v1/events/replay?limit=2')"
printf '%s' "$page" | jq '{count, next_cursor, has_more, order}'
cursor="$(printf '%s' "$page" | jq -r '.next_cursor')"
curl -fsS --get 'http://localhost:8000/v1/events/replay' \
  --data-urlencode 'limit=2' \
  --data-urlencode "after=$cursor" | jq '{count, next_cursor, order}'
```

## 38–52 seconds: prove revisions, checkpoint, and evidence survive

Explain that repeated identical polls are atomically deduplicated, while an upstream correction
becomes a new immutable revision. Raw HTTP bodies are stored by SHA-256 before parsing, so a bad
or changed source response remains inspectable. The projector commits the revision, current
pointer, and checkpoint together; restarting it safely resumes after that checkpoint.

```bash
docker compose exec ingestor sh -lc 'find /app/data/raw -type f | sort | head -n 6'
docker compose logs --tail=20 ingestor
docker compose logs --tail=20 projector
docker compose exec postgres psql -U atlas -d atlas -c \
  'select projection_name,last_stream_id,updated_at from projection_checkpoints;'
```

## 52–60 seconds: close on engineering quality

Show the three CI jobs: strict Python/Ruff/mypy/real-Valkey-and-PostGIS coverage,
TypeScript/Biome/Vitest/production build, and container/edge smoke test. State the boundary
honestly: this milestone performs multi-source transport, normalization, durable current-state
projection, spatial querying, mapping, and replay;
cross-source causal fusion, retrieval, and agents are later milestones rather than hidden claims.

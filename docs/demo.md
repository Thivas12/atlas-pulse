# AtlasPulse: 60-second review demo

Start from a populated local stack with `docker compose up -d --build` and open
<http://localhost:3000>.

## 0–25 seconds: prove four-source durable state and spatial loading

Point out separate USGS/NWS/FIRMS/GDELT freshness, earthquake points, severity-colored NWS
polygons, confidence-colored NASA FIRMS points, and priority-colored GDELT conflict observations.
Switch among **All**, **Earthquakes**, **Weather**, **Fires**, and **Conflict**. Select a GDELT
signal and show detection time, reported event date, CAMEO classification, actors, Goldstein
scale, media-coverage counts, geography precision, operational expiry, stable provider ID, replay
ID, and first-report evidence link. State the exact boundary shown by the UI: it is a machine-coded
media observation, not an independently verified incident. Select a FIRMS signal and show
acquisition time, confidence, satellite product,
radiative power, brightness, deterministic event ID, replay ID, and direct NASA evidence. State
the honest boundary shown by the UI: it is a thermal anomaly, not a confirmed wildfire perimeter.
Select a weather signal and show its CAP
severity, urgency, certainty, expiry, geometry status, issuing office, stable event ID, replay ID,
and direct NWS evidence link. Point out an area-code-only record if one is active: it stays in the
feed without fabricated coordinates. Pan or zoom the map and show the viewport request in the
browser network panel: live state is filtered by indexed PostGIS geometry rather than downloading
the full revision stream.

```bash
curl -fsS 'http://localhost:8000/v1/signals?source=nws&min_severity=3&bbox=-125,24,-66,50' \
  | jq '{count, next_cursor, has_more, order}'
curl -fsS 'http://localhost:8000/v1/signals?source=gdelt&min_severity=3' \
  | jq '{count, next_cursor, has_more, order}'
```

## 25–42 seconds: prove the history is deterministic

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

## 42–54 seconds: prove revisions, checkpoint, evidence, and secrets survive

Explain that repeated identical polls are atomically deduplicated, while an upstream correction
becomes a new immutable revision. Raw HTTP bodies are stored by SHA-256 before parsing, so a bad
or changed source response remains inspectable. The projector commits the revision, current
pointer, and checkpoint together; restarting it safely resumes after that checkpoint. The free
FIRMS key is only available to the ingestor and is redacted from spans and public evidence URLs.

```bash
docker compose exec ingestor sh -lc 'find /app/data/raw -type f | sort | head -n 6'
docker compose logs --tail=20 ingestor
docker compose logs --tail=20 projector
docker compose exec postgres psql -U atlas -d atlas -c \
  'select projection_name,last_stream_id,updated_at from projection_checkpoints;'
```

## 54–60 seconds: close on engineering quality

Show the three CI jobs: strict Python/Ruff/mypy/real-Valkey-and-PostGIS coverage,
TypeScript/Biome/Vitest/production build, and container/edge smoke test. State the boundary
honestly: this milestone performs multi-source transport, normalization, durable current-state
projection, spatial querying, mapping, and replay;
cross-source causal fusion, retrieval, and agents are later milestones rather than hidden claims.

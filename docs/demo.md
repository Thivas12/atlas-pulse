# AtlasPulse: 60-second review demo

Start from a populated local stack with `docker compose up -d --build` and open
<http://localhost:3000>.

## 0–15 seconds: prove the data is live

Point out the system state, source-freshness metric, and mapped USGS revisions. Select one signal
from the feed; show its source status, coordinates, stable event ID, replay ID, and direct USGS
evidence link.

## 15–35 seconds: prove the history is deterministic

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

## 35–50 seconds: prove revisions and evidence survive

Explain that repeated identical polls are atomically deduplicated, while an upstream correction
becomes a new immutable revision. Raw HTTP bodies are stored by SHA-256 before parsing, so a bad
or changed source response remains inspectable.

```bash
docker compose exec ingestor sh -lc 'find /app/data/raw -type f | head -n 3'
docker compose logs --tail=20 ingestor
```

## 50–60 seconds: close on engineering quality

Show the two CI jobs: strict Python/Ruff/mypy/real-Valkey coverage and
TypeScript/Biome/Vitest/production build. State the boundary honestly: this milestone is the
replayable seismic vertical slice; multi-source fusion, retrieval, and agents are the next
milestones rather than hidden claims.

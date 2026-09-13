# AtlasPulse: 60-second review demo

Start from a populated local stack with `docker compose up -d --build` and open
<http://localhost:3000>.

## 0–18 seconds: prove live public evidence and indexed spatial state

Point out separate USGS/NWS/FIRMS/GDELT freshness, earthquake points, severity-colored NWS
polygons, confidence-colored NASA FIRMS points, and priority-colored GDELT observations. Switch
among **Earthquakes**, **Weather**, **Fires**, and **Conflict**. Pan the map and show that the
browser requests an indexed PostGIS viewport rather than downloading the full revision stream.
Open one signal and follow its public evidence link. State its source-specific uncertainty: an NWS
alert is authoritative but can change, a FIRMS pixel is a thermal anomaly rather than a confirmed
wildfire perimeter, and GDELT is a machine-coded media observation rather than verified ground
truth.

```bash
curl -fsS 'http://localhost:8000/v1/signals?source=nws&min_severity=3&bbox=-125,24,-66,50' \
  | jq '{count, next_cursor, has_more, order}'
```

## 18–33 seconds: prove evidence-first hybrid retrieval

Open **Search** and enter a paraphrase such as “residents ordered to shelter from a dangerous
storm.” Point out the independent FTS and vector ranks, final transparent rerank score, and
traceable evidence link. Toggle viewport scope. State the boundary visible in the UI: these are
ranked source events, not an LLM answer, and citation validation is structural rather than a truth
claim.

```bash
curl -fsS --get 'http://localhost:8000/v1/search' \
  --data-urlencode 'q=residents ordered to shelter from a dangerous storm' \
  --data-urlencode 'candidate_limit=100' \
  --data-urlencode 'limit=3' \
  | jq '{count, candidates_considered, embedding_model, ranking_mode, ranking_rule, caveat,
         first: (.items[0] | {event_id: .event.event_id, source: .event.source,
                              ranking, citation})}'
```

## 33–45 seconds: prove explainable cross-source correlation

Select **Correlations**. Choose a cluster and trace its dashed map links. In the detail panel show
the participating source nodes, direct evidence URLs, measured kilometres, time deltas, point or
polygon basis, stable candidate ID, and `spatiotemporal-v1` rule. Read the visible boundary:
co-occurrence does not establish causation, corroboration, or one shared incident.

The live distribution of public events is unpredictable. If the current viewport has no cluster,
demonstrate the bounded API over a wider investigation window and then narrow it if the truncation
flag is true:

```bash
curl -fsS --get 'http://localhost:8000/v1/incidents' \
  --data-urlencode 'radius_km=500' \
  --data-urlencode 'time_window_minutes=1440' \
  --data-urlencode 'lookback_hours=168' \
  --data-urlencode 'active_only=false' \
  --data-urlencode 'limit=3' \
  | jq '{count, total_incidents, incidents_truncated, candidate_edges_truncated,
         rule_version, parameters,
         first: (.items[0] | {incident_id, sources, node_count, edge_count,
                              max_distance_km, caveat})}'
```

## 45–53 seconds: prove deterministic replay

Select **Replay**. Explain that Valkey Streams are read oldest-first and that each page starts
strictly after its cursor. Move the slider, press play, and change from 1× to 4×.

```bash
page="$(curl -fsS 'http://localhost:8000/v1/events/replay?limit=2')"
printf '%s' "$page" | jq '{count, next_cursor, has_more, order}'
cursor="$(printf '%s' "$page" | jq -r '.next_cursor')"
curl -fsS --get 'http://localhost:8000/v1/events/replay' \
  --data-urlencode 'limit=2' \
  --data-urlencode "after=$cursor" | jq '{count, next_cursor, order}'
```

## 53–58 seconds: prove recovery and auditability

Explain that identical polls are atomically deduplicated while a source correction becomes a new
immutable revision. Raw HTTP bodies are stored by SHA-256 before parsing. The canonical projector
and retrieval indexer have independent atomic checkpoints, so a cold or failed embedding model
cannot stall live state. The free FIRMS key exists only in the ingestor and is redacted from
public evidence and telemetry.

```bash
docker compose exec ingestor sh -lc 'find /app/data/raw -type f | sort | head -n 6'
docker compose exec postgres psql -U atlas -d atlas -c \
  'select projection_name,last_stream_id,updated_at from projection_checkpoints;'
```

## 58–60 seconds: close on engineering quality

Show the versioned 15-query set and rank-blind review sheet workflow. Explain that lexical, dense,
RRF, and hybrid rankings are scored with pooled Recall, MRR, graded nDCG, citation traceability,
latency, and per-source/intent slices; no relevance score or gate exists until a named human
reviews the captured evidence. Then show CI: strict Ruff/mypy/pytest with real Valkey, PostGIS,
and pgvector; TypeScript/Biome/Vitest; a production build; and a container/edge smoke test. Close
with the architectural boundary: generation, contradiction detection, and agents remain
separately versioned layers rather than hidden claims.

```bash
uv run atlas-pulse-evaluate --help
jq '{query_set_id, pool_depth, modes, query_count: (.queries | length)}' \
  evals/retrieval/live-disruptions-v1.json
```

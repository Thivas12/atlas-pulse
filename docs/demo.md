# AtlasPulse: 60-second review demo

Start from a populated local stack with `docker compose up -d --build` and open
<http://localhost:3000>.

## 0–18 seconds: prove live public evidence and indexed spatial state

Start with the **Operations beacon**. Point out that each source has separate poll-heartbeat and
upstream-data states, while the metric below is labelled only as bounded visible-event age. A green
event list is never substituted for missing freshness evidence. Directly below it, show the
**Recovery evidence** timeline: each start and terminal outcome keeps one attempt identity, failed
stages use bounded codes, and a later success is shown as a distinct observation rather than an
inferred recovery claim. Then show earthquake points,
severity-colored NWS polygons, confidence-colored NASA FIRMS points, and priority-colored GDELT
observations. Switch among **Earthquakes**, **Weather**, **Fires**, and **Conflict**. Pan the map and
show that the browser requests an indexed PostGIS viewport rather than downloading the full
revision stream. Open one signal and follow its public evidence link. State its source-specific
uncertainty: an NWS alert is authoritative but can change, a FIRMS pixel is a thermal anomaly rather
than a confirmed wildfire perimeter, and GDELT is a machine-coded media observation rather than
verified ground truth.

```bash
curl -fsS 'http://localhost:8000/v1/source-freshness' \
  | jq '{generated_at, passed, sources: [.items[] |
        {source, poll_status, source_data_status, source_age_seconds, passed}]}'
curl -fsS 'http://localhost:8000/v1/source-polls?limit=6' \
  | jq '{count, next_cursor, has_more, order, transitions: [.items[] |
        {stream_id, source, transition, attempt_id: .attempt.attempt_id,
         stage: .attempt.stage, failure_code: .attempt.failure_code}]}'
curl -fsS 'http://localhost:8000/v1/signals?source=nws&min_severity=3&bbox=-125,24,-66,50' \
  | jq '{count, next_cursor, has_more, order}'
```

## 18–33 seconds: prove evidence-first hybrid retrieval

Open **Search** and enter a paraphrase such as “residents ordered to shelter from a dangerous
storm.” Point out the independent FTS and vector ranks, final monotonic RRF score, evidence
tie-break features, and traceable evidence link. Toggle viewport scope. State the boundary visible
in the UI: these are ranked source events, not an LLM answer, and citation validation is structural
rather than a truth claim. Choose **Prepare agent pack**. Show the content-addressed pack ID, hard
item/source-text limits, included and excluded counts, and the untrusted-source-text rule. The pack
is an auditable handoff, not an answer. Choose **Check run policy**. Show the second content ID,
the default-deny result, all 11 gate outcomes, the separate quality-evidence and approval states,
and the expandable checks. Point out that
the proposed agent model, agent network/tools, answer generation, and side effects all remain off;
ordinary evidence retrieval still uses the local BGE embedding model.

```bash
curl -fsS --get 'http://localhost:8000/v1/search' \
  --data-urlencode 'q=residents ordered to shelter from a dangerous storm' \
  --data-urlencode 'candidate_limit=100' \
  --data-urlencode 'limit=3' \
  | jq '{count, candidates_considered, embedding_model, ranking_mode, ranking_rule, caveat,
         first: (.items[0] | {event_id: .event.event_id, source: .event.source,
                              ranking, citation})}'
curl -fsS --get 'http://localhost:8000/v1/evidence-packs' \
  --data-urlencode 'q=residents ordered to shelter from a dangerous storm' \
  --data-urlencode 'candidate_limit=100' \
  --data-urlencode 'retrieval_limit=20' \
  | jq '{pack_id, status, answer_generated, item_count, exclusion_count,
         source_text_characters, budget, retrieval: (.retrieval |
           {embedding_model, ranking_mode, ranking_rule, parameters}), trust_boundary}'
curl -fsS --get 'http://localhost:8000/v1/agent-runs/preflight' \
  --data-urlencode 'q=residents ordered to shelter from a dangerous storm' \
  --data-urlencode 'candidate_limit=100' \
  --data-urlencode 'retrieval_limit=20' \
  | jq '{pack_id: .evidence_pack.pack_id,
         manifest: (.manifest |
           {manifest_id, proposal_id, release, approval, status, authorization, execution})}'
```

## 33–45 seconds: prove measured correlation and claim analysis stay separate

Select **Correlations**. Choose a cluster and trace its dashed map links. In the detail panel show
the participating source nodes, direct evidence URLs, measured kilometres, time deltas, point or
polygon basis, stable candidate ID, and `spatiotemporal-v1` rule. Then show the separate
`structured-claims-v1` relationships, exact source fields, and abstentions. Read both visible
boundaries: co-occurrence does not establish causation or one shared incident, while normalized
claim agreement does not prove truth.

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
         rule_version, relationship_rule_version, parameters,
         first: (.items[0] | {incident_id, sources, node_count, edge_count,
                              max_distance_km,
                              relationship_analysis: (.relationship_analysis |
                                {corroboration_count, contradiction_count,
                                 insufficient_evidence_count, rule_version}),
                              caveat})}'
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
candidate coverage, latency, and per-source/intent slices. Point out that the first reviewed
baseline exposed zero lexical coverage and a degrading weighted hybrid rule, so the next version
repaired candidate recall and limited evidence features to RRF tie-breaking before considering a
cross-encoder. Show the content-addressed campaign: unchanged evidence reuses only exact prior
grades, and every reviewed capture is rescored against one global union across the complete
series. The trajectory exposes mode, slice, coverage, and latency movement without inventing a
model-release verdict. No relevance score or gate exists until a named human reviews the captured
evidence. Then show CI: strict
Ruff/mypy/pytest with real Valkey, PostGIS,
and pgvector; TypeScript/Biome/Vitest; a production build; and a container/edge smoke test. Close
with the architectural boundary: generated proposals and agents remain separately versioned
layers rather than hidden claims. The new evidence pack establishes bounded, citation-safe input
for those future layers without bypassing evaluation. Show that semantic changes have their own
prediction-blind human benchmark rather than relying on synthetic examples or an LLM judge.
Explain that public or promotion-oriented results require two exact first-pass reviews: AtlasPulse
reports observed agreement and Cohen's kappa, sends only disagreements to a third system-blind
adjudicator, and content-addresses the final gold pool with both review hashes. Close on the
observable trajectory chain: it records action metadata rather than hidden reasoning, requires
exact grounded adjudication, compares consecutive captures for drift, and can reach only human
review eligibility under a content-addressed policy. Finish on the default-deny preflight and
signed ledger: the proposal makes approval scope stable, Ed25519 and
trusted key IDs make intent verifiable, expiry and revocation fail closed, and the remaining gates
still prove that no agent ran.

```bash
uv run atlas-pulse-evaluate --help
uv run atlas-pulse-evaluate-relationships --help
uv run atlas-pulse-evaluate-agent-trajectories --help
jq '{query_set_id, pool_depth, modes, query_count: (.queries | length)}' \
  evals/retrieval/live-disruptions-v1.json
jq '{benchmark_id, predicates, max_edges_per_source_pair, parameters}' \
  evals/relationships/live-claim-pairs-v1.json
```

# AtlasPulse

[![CI](https://github.com/Thivas12/atlas-pulse/actions/workflows/ci.yml/badge.svg)](https://github.com/Thivas12/atlas-pulse/actions/workflows/ci.yml)

Real-time, evidence-grounded global disruption intelligence built from authoritative public
data. AtlasPulse is designed as a production system, not a notebook: source bytes remain
auditable, contracts are strict, delivery is replayable, failures are observable, and every
component can run without a paid API key.

> **Current milestone — longitudinal evidence quality and governed model/agent release.** Independent workers
> poll official USGS earthquakes every 60 seconds, NOAA/NWS actual alerts every 120 seconds,
> opt-in NASA FIRMS VIIRS thermal anomalies every 15 minutes, and GDELT 2.0 material-conflict
> observations every 15 minutes. Every unmodified source response is preserved, strictly
> validated, normalized, and atomically published to Valkey Streams. A restart-safe worker
> transactionally projects every revision, current event pointer, and its checkpoint into
> PostGIS. A bounded query-time correlation engine measures cross-source spatial and temporal
> co-occurrence. A separate restart-safe worker renders and locally embeds current evidence into
> PostgreSQL full-text search plus pgvector. `/v1/search` applies the same source, time, expiry, and
> PostGIS filters to both channels, fuses ranks with RRF, applies evidence-only tie-breaks, validates
> credential-safe citations, and exposes every score in the dashboard. A pooled, rank-blind human
> judgment workflow compares lexical, dense, RRF, and hybrid modes with standard IR metrics,
> source/intent slices, content-addressed reports, and explicit regression gates. Two or more
> reviewed captures can now form one content-addressed campaign: every point is rescored against
> the same global judged union and exposes baseline-relative quality, coverage, slice, and latency
> movement without inventing a model-release verdict. Every measured
> incident edge now receives a separate `structured-claims-v1` annotation: source-backed claims,
> exact field provenance, narrow corroboration/contradiction rules, and an explicit insufficient-
> evidence result. These annotations never rewrite measured distance/time facts and do not claim
> truth, causation, or a verified shared incident. A separate live evaluation now samples measured
> edges by source pair, blinds every deployed prediction from reviewers, imports protected human
> labels, and reports per-label precision/recall/F1 with abstention and predicate/source slices.
> Two exact first-pass reviews can now be compared with observed agreement and Cohen's kappa;
> only disagreements enter a separately protected, system-blind adjudication sheet, and the final
> gold pool retains both review hashes plus the third-person decision provenance. That pool can
> now produce a content-addressed, gold-blind candidate task and protected prediction sheet.
> Strict model/artifact/template/runtime identities bind each imported batch; paired reports expose
> gains, regressions, abstention shifts, slices, and descriptive latency while remaining explicitly
> blocked from production promotion. A separate CPU-only runner now executes one predeclared,
> revision-pinned DeBERTa-v3-small ONNX candidate without access to gold: it balances both source
> records, evaluates predicate-specific corroboration and contradiction hypotheses, abstains behind
> fixed thresholds, hashes the exact local model bytes, and emits a content-addressed inference
> trace. Before any generated answer or agent action is introduced,
> `/v1/evidence-packs` now converts deployed search
> results into deterministic, content-addressed JSON. It preserves retrieval order and provenance,
> admits only structurally traceable citations, applies hard item and source-text character budgets,
> records every exclusion, and marks all included text as untrusted data. A separate gold-free
> grounded-answer workflow now captures those exact live packs, accepts only atomic cited claims or
> explicit abstentions from an externally run candidate, enforces tokenizer/context measurements,
> protects model-blind human review sheets, and reports faithfulness, citation, relevance,
> abstention, slice, token, and latency evidence. A separate CPU-only runner can now execute one
> checked-in, revision-pinned Qwen3 1.7B Q8 candidate without review or gold access. It verifies the
> exact GGUF and llama.cpp bytes, uses authenticated loopback-only offline inference with tools and
> thinking disabled, constrains citations to each case, checks tokenizer context before generation,
> and emits a content-addressed blocked trace. Two exact first-pass grounded-answer reviews can now
> be compared per rubric field with observed agreement and Cohen's kappa. Only disputed rows enter
> a candidate- and reviewer-blind sheet; agreed fields are immutable, review A/B order is swapped
> per row, and a distinct adjudicator resolves only disputed fields. The finalized review retains
> both review hashes and stays blocked. No live quality result is claimed. A second no-execution
> boundary binds an exact pack into an immutable run manifest, evaluates eight explicit
> default-deny authorization checks, reports every unmet release gate, and proves that no proposed
> agent, generative model, agent network/tool access, answer, or side effect occurred.

## Why this is portfolio-grade

| Capability | Concrete proof in this repository |
| --- | --- |
| Real public data | Independent USGS, NOAA/NWS, NASA FIRMS, and GDELT 2.0 near-real-time feeds |
| Auditability | SHA-256 content-addressed raw snapshots are written before parsing |
| Reliable delivery | Bounded HTTP retry plus pipelined, revision-aware atomic Lua deduplication |
| Credential safety | Free FIRMS key is ingestor-only and redacted from events, errors, and spans |
| Shared contracts | Immutable `Event` and `GeoPoint` models pinned to `agent-rag-core` commit `7732801` |
| Deterministic replay | Exclusive Valkey Stream cursors page retained history oldest-first without boundary duplicates |
| Durable current state | Immutable PostgreSQL revisions plus atomic current pointers and restart-safe checkpoint |
| Spatial access | Indexed PostGIS point/polygon intersection, severity, source, time, expiry, and keyset filters |
| Transparent correlation | Versioned cross-source rules, exact geography distance/time evidence, stable graph IDs, hard result caps, and explicit non-causal semantics |
| Claim relationships | Stable source-field claims, conservative corroboration/contradiction rules, explicit abstention, immutable parent-edge references |
| Relationship evaluation | Dual prediction-blind reviews, adjudicated gold, a revision-pinned local ONNX NLI runner, paired regressions, abstention/slice metrics, and a closed promotion boundary |
| Hybrid retrieval | PostgreSQL FTS + local BGE embeddings + pgvector HNSW, shared time/geography filters, deterministic RRF, inspectable evidence tie-breaks |
| Retrieval evaluation | Versioned live queries, four ablations, rank-blind grading, exact judgment reuse, global-union campaign trajectories, slice reports, explicit gates |
| Grounding boundary | Source events stay verbatim; citation URLs fail closed on credentials/private targets; search never manufactures an answer |
| Agent handoff | Content-addressed evidence packs, exact retrieval provenance, hard source-text budgets, explicit exclusions, and an untrusted-data policy |
| Grounded-answer evaluation | Gold-free live tasks, a revision-pinned local Qwen/llama.cpp runner, case-local citation schemas, tokenizer/context checks, dual model-blind reviews, per-field agreement, disagreement-only blind adjudication, descriptive metrics, and a closed promotion boundary |
| Agent governance | Pack-bound immutable run manifests, default-deny policy snapshots, explicit human/evaluation gates, and zero-execution proof |
| Operations | Liveness, dependency readiness, JSON logs, OpenTelemetry traces, graceful shutdown |
| Decision UI | Mixed-geometry map, graph inspection, semantic search ranks, four source filters, replay, evidence links, uncertainty labels |
| Engineering quality | Strict mypy/TypeScript, locked dependencies, branch coverage, real Valkey/PostGIS/pgvector CI |
| Supply-chain hygiene | Read-only workflow permissions, commit-pinned Actions, weekly dependency updates |

## Architecture

```mermaid
flowchart TD
    subgraph SENSE["01 · SENSE / PUBLIC SIGNALS"]
        direction LR
        USGS["USGS · seismic"]:::source
        NWS["NOAA · weather"]:::source
        FIRMS["NASA · thermal"]:::source
        GDELT["GDELT · conflict"]:::source
        ADAPTERS["Failure-isolated adapters"]:::ingest
        USGS --> ADAPTERS
        NWS --> ADAPTERS
        FIRMS --> ADAPTERS
        GDELT --> ADAPTERS
    end

    subgraph TRUST["02 · TRUST / REPLAYABLE CORE"]
        direction LR
        RAW["SHA-256 raw vault"]:::trust --> VALIDATE["Strict validation"]:::trust
        VALIDATE --> EVENT["Shared Event contract"]:::trust
        EVENT --> STREAM["Valkey atomic stream"]:::trust
    end

    subgraph INTEL["03 · FUSE / EVIDENCE INTELLIGENCE"]
        direction LR
        PROJECTOR["Restart-safe projector"]:::state --> POSTGIS["PostGIS current state"]:::state
        POSTGIS --> GRAPH["Measured evidence graph"]:::intel
        GRAPH --> CLAIMS["Structured claim relations"]:::intel
        INDEXER["Independent indexer"]:::state --> SEARCHDB["FTS + pgvector"]:::state
        SEARCHDB --> SEARCH["Hybrid evidence search"]:::intel
        SEARCH --> PACK["Bounded evidence pack"]:::intel
    end

    subgraph GOVERN["04 · GOVERN / RELEASE CONTROL"]
        direction TB
        RETRIEVAL_REVIEW["Longitudinal retrieval campaign"]:::review
        RELATION_REVIEW["Dual semantic review"]:::review
        CANDIDATE["Pinned relation NLI · gold blind"]:::sandbox
        ANSWER_RUNNER["Pinned Qwen brief · gold blind"]:::sandbox
        ANSWER_REVIEW["Dual grounded brief review"]:::review
        ANSWER_ADJ["Blind field adjudication"]:::review
        PREFLIGHT["Default-deny preflight"]:::gate
        MANIFEST["Immutable run manifest"]:::gate
        HUMAN["Explicit human release"]:::human
    end

    subgraph SERVE["05 · SERVE / OPERATOR SURFACE"]
        direction LR
        API["FastAPI evidence surface"]:::surface --> WEB["Atlas command center"]:::surface
        AGENTS["Specialist agents · future"]:::future
    end

    ADAPTERS --> RAW
    STREAM --> PROJECTOR
    STREAM --> INDEXER
    SEARCH --> RETRIEVAL_REVIEW --> PREFLIGHT
    CLAIMS --> RELATION_REVIEW --> CANDIDATE --> PREFLIGHT
    PACK --> ANSWER_RUNNER --> ANSWER_REVIEW --> ANSWER_ADJ
    ANSWER_ADJ -.-> PREFLIGHT
    PACK --> PREFLIGHT --> MANIFEST
    STREAM --> API
    POSTGIS --> API
    GRAPH --> API
    CLAIMS --> API
    SEARCH --> API
    PACK --> API
    MANIFEST --> API
    MANIFEST -.-> HUMAN
    WEB -.-> HUMAN
    HUMAN -.-> AGENTS

    classDef source fill:#082f49,stroke:#38bdf8,color:#e0f2fe,stroke-width:1px
    classDef ingest fill:#083344,stroke:#22d3ee,color:#cffafe,stroke-width:1.5px
    classDef trust fill:#0f2f2b,stroke:#2dd4bf,color:#ccfbf1,stroke-width:1px
    classDef state fill:#102a24,stroke:#34d399,color:#d1fae5,stroke-width:1px
    classDef intel fill:#211a3a,stroke:#a78bfa,color:#ede9fe,stroke-width:1px
    classDef review fill:#2d2414,stroke:#fbbf24,color:#fef3c7,stroke-width:1px
    classDef sandbox fill:#2a1838,stroke:#c084fc,color:#f3e8ff,stroke-width:1.5px
    classDef gate fill:#3a1d24,stroke:#fb7185,color:#ffe4e6,stroke-width:1.5px
    classDef human fill:#352a12,stroke:#facc15,color:#fef9c3,stroke-width:1.5px
    classDef surface fill:#172554,stroke:#60a5fa,color:#dbeafe,stroke-width:1px
    classDef future fill:#171b24,stroke:#94a3b8,color:#cbd5e1,stroke-width:1px,stroke-dasharray:5 4
    style SENSE fill:#061521,stroke:#164e63,color:#bae6fd
    style TRUST fill:#071c1b,stroke:#115e59,color:#99f6e4
    style INTEL fill:#121126,stroke:#4c1d95,color:#ddd6fe
    style GOVERN fill:#20151a,stroke:#881337,color:#fecdd3
    style SERVE fill:#0d1730,stroke:#1e3a8a,color:#bfdbfe
    linkStyle default stroke:#64748b,stroke-width:1.4px
```

Solid paths are operational tooling today; they do not imply that a live candidate run or human
review has occurred. The violet candidate nodes are isolated local evaluation sandboxes, not
production inference services. Dashed paths mark deliberately closed release connections: even an
adjudicated grounded-answer report cannot satisfy preflight without representative evidence,
approved thresholds, target-hardware reproduction, and explicit release, and no specialist agent
can run until every evaluation, model, execution, and human-release gate passes.

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

That zero-configuration command runs USGS, NWS, and keyless GDELT. NASA FIRMS needs a free,
email-issued `MAP_KEY` because its servers meter transactions. Request one from the
[official FIRMS key page](https://firms.modaps.eosdis.nasa.gov/api/map_key/), then enable only the
ingestor-facing credential:

```bash
cp .env.example .env
# Edit .env: set ATLAS_FIRMS_ENABLED=true and ATLAS_FIRMS_MAP_KEY=<your key>
docker compose up --build
```

Do not commit `.env`; it is ignored by Git. The key lives only in the ingestor container and is
removed from source metadata, raised errors, and OpenTelemetry URL attributes.

The first image build also downloads a commit-pinned copy of the free 67 MB BGE ONNX model; later
builds use Docker's cache and runtime inference is offline. Compose waits for PostGIS plus
pgvector, applies Alembic migrations once, and starts the ingestor, canonical projector, retrieval
indexer, API, and web edge. After the first ingestion,
projection, and indexing cycles, open the command center at
<http://localhost:3000>. The API and its operational probes remain directly available:

```bash
curl -s http://localhost:8000/healthz
curl -s http://localhost:8000/readyz
curl -s 'http://localhost:8000/v1/events?limit=5'
curl -s 'http://localhost:8000/v1/events/replay?limit=5'
curl -s 'http://localhost:8000/v1/signals?limit=5&active_only=true'
curl -s 'http://localhost:8000/v1/signals?source=nws&min_severity=3&bbox=-125,24,-66,50'
curl -s 'http://localhost:8000/v1/signals?source=firms&bbox=-120,33,-117,36'
curl -s 'http://localhost:8000/v1/signals?source=gdelt&min_severity=3&bbox=-20,-40,60,60'
curl -s 'http://localhost:8000/v1/incidents?bbox=-120,30,-110,40&radius_km=50'
curl -s --get 'http://localhost:8000/v1/search' \
  --data-urlencode 'q=residents ordered to shelter from a dangerous storm' \
  --data-urlencode 'bbox=-125,24,-66,50'
curl -s --get 'http://localhost:8000/v1/evidence-packs' \
  --data-urlencode 'q=residents ordered to shelter from a dangerous storm' \
  --data-urlencode 'bbox=-125,24,-66,50'
curl -s --get 'http://localhost:8000/v1/agent-runs/preflight' \
  --data-urlencode 'q=residents ordered to shelter from a dangerous storm' \
  --data-urlencode 'bbox=-125,24,-66,50'
```

Switch between **Live** and **Replay**, then filter **All**, **Earthquakes**, **Weather**, or
**Fires**, or **Conflict**. Open **Correlations** to inspect measured cross-source clusters and
follow each node back to its public source evidence. The incident panel separately shows
corroborating, conflicting, and unresolved claim comparisons, including each normalized claim's
source field and qualifying uncertainty. Open **Search** and try a paraphrase rather than copying
a source headline. Every result shows its lexical rank, dense rank, fused/final score, and citation
status. Choose **Prepare agent pack** to create a bounded handoff and inspect its full content ID,
included/excluded counts, exact source-text character use, availability state, and prompt-injection
trust boundary. The pack contains evidence only and keeps `answer_generated` false. The complete
contract and consumer rules are in [`docs/evidence-packs.md`](docs/evidence-packs.md). Then choose
**Check run policy**. The dashboard displays the content-addressed manifest, every passed and
blocked authorization check, and the exact no-execution state. It does not start the proposed
generative agent or grant approval. Ordinary local retrieval still uses the documented BGE
embedding model. See
[`docs/agent-run-preflight.md`](docs/agent-run-preflight.md).
Live mode is served from current PostGIS state, omits expired alerts/detections, and refreshes the
map with an indexed bounding-box query after every settled pan or zoom. Geometry-less NWS alerts
remain in the global feed without being falsely placed on the map. Replay starts
at the oldest retained stream entry; its controls operate on cursor-paged history. Interactive
OpenAPI documentation is at <http://localhost:8000/docs>. Stop the stack with
`docker compose down`. Add `--volumes` only when you intentionally want to delete local stream,
PostGIS, and raw snapshot data.

To measure the live retriever rather than judge a few hand-picked examples, capture the checked-in
operator query set, grade the separate rank-blind CSV, and score its content-addressed pool:

```bash
mkdir -p artifacts/evaluation
uv run atlas-pulse-evaluate capture \
  --queries evals/retrieval/live-disruptions-v1.json \
  --base-url http://localhost:8000 \
  --output artifacts/evaluation/pool.json \
  --judgments-output artifacts/evaluation/judgments.csv
```

The complete review/import/score workflow and `0..3` rubric are in
[`evals/retrieval/README.md`](evals/retrieval/README.md). AtlasPulse does not ship invented labels
or quality floors; gates become valid only after a named human reviews a captured corpus. Once at
least two reviewed pools exist, the same CLI builds a content-addressed campaign that scores every
capture against one global judged union and reports quality, coverage, latency, and slice
trajectories.

The separate [`structured-claims-v1` contract cases](evals/relationships/README.md) freeze exact
corroboration, contradiction, and abstention behavior. They are synthetic regression cases, not a
claim of live relationship accuracy. Capture and score the checked-in live benchmark before
interpreting or promoting any local NLI or LLM proposal:

```bash
mkdir -p artifacts/relationship-evaluation
uv run atlas-pulse-evaluate-relationships capture \
  --definition evals/relationships/live-claim-pairs-v1.json \
  --base-url http://localhost:8000 \
  --output artifacts/relationship-evaluation/pool.json \
  --judgments-output artifacts/relationship-evaluation/judgments.csv
```

The complete prediction-blind rubric, strict dual review, adjudication, exact reuse, deployed-rule
score, and gold-blind candidate workflow is in
[`evals/relationships/README.md`](evals/relationships/README.md). After adjudication, the CLI can
export model-visible evidence without gold or deployed labels. A separate local runner can execute
the checked-in, revision-pinned NLI candidate, emit its exact system identity and inference trace,
then feed its complete predictions into a content-addressed paired comparison. Every candidate
report remains blocked from promotion until representative evidence, an explicit quality/latency
policy, regression verification, and human approval exist.

Grounded briefs have a separate evaluator so a generator never receives human answers or grades.
Capture the live evidence task first; model execution remains a deliberately separate local step:

```bash
mkdir -p artifacts/grounded-answer-evaluation
uv run atlas-pulse-evaluate-grounded-answers capture \
  --benchmark evals/grounded-answers/live-grounded-briefs-v1.json \
  --base-url http://localhost:8000 \
  --output-task artifacts/grounded-answer-evaluation/task.json \
  --output-submission artifacts/grounded-answer-evaluation/submission.json
```

The checked-in runner can then execute one exact Qwen3 1.7B Q8 GGUF through a caller-supplied
`llama-server`. It is gold-free, local CPU-only, authenticated and loopback-only, runs llama.cpp in
offline mode with agent tools and thinking disabled, and writes a completed submission plus an
immutable blocked trace. Candidate import then emits a model-blind human review sheet; review and
score create content-addressed first-pass artifacts that remain permanently blocked from promotion.
No live run or quality score is claimed until an operator captures, executes, and reviews the task.
The exact candidate identity, provisioning/run commands, rubric, and failure rules are in
[`evals/grounded-answers/README.md`](evals/grounded-answers/README.md).

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
ATLAS_DATABASE_URL=postgresql+asyncpg://atlas:atlas@localhost:5432/atlas \
  uv run atlas-pulse-index
```

In a fourth terminal:

```bash
ATLAS_VALKEY_URL=valkey://localhost:6379/0 \
ATLAS_DATABASE_URL=postgresql+asyncpg://atlas:atlas@localhost:5432/atlas \
  uv run uvicorn atlas_pulse.asgi:app --reload --port 8000
```

Run the dashboard with Vite in a fifth terminal. Its development proxy forwards `/api` to the
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
| `GET` | `/v1/incidents?limit=50&radius_km=50` | Measured evidence components plus versioned source-claim annotations |
| `GET` | `/v1/search?q=dangerous+storm&limit=10` | Hybrid retrieval over current evidence with transparent ranks |
| `GET` | `/v1/evidence-packs?q=dangerous+storm` | Bounded, citation-safe, content-addressed retrieval handoff |
| `GET` | `/v1/agent-runs/preflight?q=dangerous+storm` | Pack-bound default-deny manifest with zero execution |

`/v1/signals` accepts `source=usgs|nws|firms|gdelt`, `min_severity=0..4`, aware
`occurred_after`/`occurred_before` timestamps, `active_only`, and a non-wrapping WGS84
`bbox=west,south,east,north`. `min_severity` applies to NWS severity ranks and the documented
AtlasPulse GDELT priority rank. Spatial requests return intersecting point or polygon evidence.
Set `include_area_only=true` only when a viewport consumer explicitly wants valid NWS alerts
that have area codes but no source geometry.

`/v1/incidents` performs a query-time join over current signals from different sources. Its
versioned `spatiotemporal-v1` rule links a pair only when PostGIS measures it within both the
requested geography radius and occurrence-time window. Defaults are a 50 km radius, six-hour
pair window, 24-hour lookback, active signals only, at most 2,000 candidate edges, and at most 50
components. The API caps radius at 500 km, time window at 24 hours, lookback at seven days, edges
at 5,000, and returned components at 100. It reports `candidate_edges_truncated` and
`incidents_truncated` rather than silently implying completeness.

Nodes preserve the full normalized source event and public evidence URL. Edges expose exact
distance, time delta, geometry basis, relation, and rule version. Content-addressed node, edge,
and component IDs make identical current evidence reproducible independent of database row order.
Polygon evidence is preferred over a focus point for backend distance; UI lines connect focus
points only as a visual guide. A connected component is deliberately named an incident
*candidate*: spatial and temporal proximity alone does not prove causation, corroboration, or a
shared real-world incident.

`/v1/search` runs the query through local `BAAI/bge-small-en-v1.5` embeddings and PostgreSQL
English full-text search. It accepts `source`, aware `occurred_after`/`occurred_before`,
`active_only`, `bbox`, a `near=longitude,latitude` plus `radius_km` filter, and
`ranking_mode=lexical|dense|rrf|hybrid` (default `hybrid`). Both channels use the same predicates.
Each channel retrieves at most `candidate_limit` rows (default 50, maximum 200), Reciprocal Rank
Fusion combines their ranks with `k=60`, and exact-phrase/token-coverage evidence breaks only
exact fused-score ties. The lexical channel uses a bounded any-term English query so one missing
paraphrased term cannot collapse the whole candidate set. Raw FTS/cosine scores, channel ranks,
RRF score, final score, measured distance, model ID, mode/rule ID, and exact query parameters are
returned.

Citation status `traceable` means only that AtlasPulse found a public HTTP(S) evidence URL without
embedded credentials or credential-like query parameters and attached it to the exact source and
event identity. It does not mean AtlasPulse fetched, endorsed, or independently verified the
claim. `missing` and `rejected` fail closed. Search returns source events, never generated prose.
See the [evaluation boundary](docs/retrieval-evaluation.md).

`/v1/evidence-packs` preserves deployed retrieval order while admitting only traceable source-text
prefixes under explicit item and character budgets. `/v1/agent-runs/preflight` returns a fresh pack
beside a second content-addressed manifest. That manifest binds the pack, requested read-only
capabilities, default-deny policy, all eight authorization checks, and an explicit `not_started`
execution state. Under `agent-authorization-v1`, the result is always `blocked`; it is not an
approval token or a hidden agent invocation.

The separate grounded-answer evaluator captures `/v1/evidence-packs` responses but does not add an
API answer surface. Its pinned local runner revalidates exact model/runtime identities and isolates
candidate execution from human review. Two independent reviews can be compared per rubric field,
and only disputed fields can be finalized by a distinct, blinded adjudicator; every trace and
report stays non-promoting. See the
[grounded-answer workflow](evals/grounded-answers/README.md).

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
GDELT Event rows become `geopolitical.gdelt_event` point observations keyed by the provider's
`GlobalEventID`. AtlasPulse selects geolocated root events in CAMEO QuadClass 4 (material
conflict), retains the original CAMEO codes, Goldstein scale, actors, coverage counts, tone,
report date, and first source-report URL, and gives the live view a documented 24-hour operational
window. The 15-minute `DATEADDED` value is labeled as detection time; the underlying event date
has only daily precision. GDELT is machine-coded from media and can contain reporting, NLP, or
geocoding errors, so the UI never presents these observations as independently verified incidents.
Replay requests read one extra entry to compute `has_more`, return at most 500 items, and expose
the final visible stream ID as `next_cursor`. Stream retention is bounded, so replay is
deterministic for retained entries rather than an indefinite event archive.

## Failure behavior

- HTTP transport errors, `429`, and `5xx` responses retry with bounded exponential backoff.
- Permanent non-success responses fail immediately; `429` and `5xx` are the retryable exceptions.
- USGS, NWS, enabled FIRMS, and GDELT poll on separate async tasks, so one failure does not block
  others.
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
- GDELT resolves the one Event export in the official latest-update manifest, upgrades it to
  HTTPS, verifies advertised size and MD5, bounds both compressed and expanded bytes, rejects
  unsafe ZIP members, and requires exactly 61 tab-delimited Event columns. It selects at most
  5,000 qualifying records per cycle by default.
- The GDELT latest-update pointer exposes only the newest 15-minute export. A prolonged outage can
  therefore create gaps; immutable snapshots and replay prove what AtlasPulse saw, not complete
  historical GDELT coverage.
- Revision insert, newer-only current pointer, and projection checkpoint commit together. A
  failed batch is retried from the unchanged cursor and duplicate revision inserts are harmless.
- Readiness fails closed when the stream or durable query store is unavailable; liveness remains
  available.
- Correlation reads only projected current state inside explicit time, distance, viewport, edge,
  and component bounds. Truncation is part of the response contract; widening a dense query can
  change component membership when the candidate-edge cap is reached.
- Claim analysis runs only after correlation, retains the parent edge and exact source fields,
  drops internally ambiguous operational claims, and abstains when normalized claims cannot be
  compared. It never changes graph identity or blocks ingestion/projection.
- The retrieval worker owns a separate checkpoint. Model download/load, embedding, or vector
  transaction failure cannot block the canonical projector; the failed batch restarts from its
  unchanged cursor.
- Current retrieval rows update for a newer stream position, or for the same revision when its
  derived document/model identity changes during an intentional rebuild. Generated text search,
  vector data, and the retrieval checkpoint commit atomically.
- Dense and lexical scores are never added directly. Deterministic RRF combines ranks, and the
  response exposes evidence tie-breakers and hard candidate caps. A reviewed baseline must justify
  any future learned reranker.
- Relationship candidate tasks require independently adjudicated gold but expose only exact
  reviewer-visible evidence. Prediction imports bind every row to that task and an immutable
  model/artifact/template/runtime identity; comparisons remain non-promoting and never change the
  production relationship rule.
- Grounded-answer capture revalidates the deployed evidence-pack identity and request echo. Imports
  require task-local citations, complete token/latency measurements, and declared context limits;
  protected review fields cannot change. Dual-review agreement and disagreement-only adjudication
  remain candidate-blind, content-addressed, and unable to promote a candidate.
- The pinned grounded-answer runner fails closed on model/runtime byte drift, prompt drift, context
  overflow, foreign citations, reasoning output, malformed JSON, or server accounting mismatch. It
  starts only its owned authenticated loopback child in llama.cpp offline mode and never receives a
  review or report artifact.
- Agent preflight evaluates every policy gate even when evidence is unavailable, binds the exact
  pack identity into its manifest, and can only return `blocked` under the v1 policy. It never
  starts the proposed agent, invokes a generative agent model, grants agent network/tool access,
  generates an answer, or performs an agent side effect.

See [ADR 0001](docs/adr/0001-use-valkey-streams.md) for the event-bus decision,
[ADR 0002](docs/adr/0002-snapshot-before-validation.md) for the evidence boundary, and
[ADR 0003](docs/adr/0003-cursor-based-replay.md) for replay semantics, and
[ADR 0004](docs/adr/0004-multi-source-weather-geometry.md) for source isolation and NWS geometry,
and [ADR 0005](docs/adr/0005-transactional-postgis-projection.md) for durable projection and
checkpoint semantics, and [ADR 0006](docs/adr/0006-firms-thermal-anomaly-ingestion.md) for FIRMS
identity, expiry, and credential boundaries, and
[ADR 0007](docs/adr/0007-gdelt-material-conflict-ingestion.md) for GDELT integrity, selection,
time, and uncertainty boundaries, and
[ADR 0008](docs/adr/0008-deterministic-evidence-graph-correlation.md) for correlation rules,
identity, query bounds, and non-causal semantics, and
[ADR 0009](docs/adr/0009-independent-hybrid-retrieval.md) for model, failure-isolation, fusion,
filter, and citation decisions, and
[ADR 0010](docs/adr/0010-pooled-human-retrieval-evaluation.md) for pooled judgments, blinding,
metrics, and gate semantics, and
[ADR 0011](docs/adr/0011-baseline-driven-retrieval-hardening.md) for the measured lexical-recall,
RRF-monotonic hybrid, and candidate-coverage decisions, and
[ADR 0012](docs/adr/0012-shared-pool-longitudinal-evaluation.md) for exact judgment reuse and
shared-pool before/after measurement, and
[ADR 0013](docs/adr/0013-versioned-source-claim-relationships.md) for claim provenance,
comparison scope, abstention, and the model boundary, and
[ADR 0014](docs/adr/0014-human-reviewed-claim-pair-benchmark.md) for live edge sampling,
prediction blinding, strict human labels, and semantic promotion metrics, and
[ADR 0015](docs/adr/0015-independent-review-adjudication.md) for exact independent reviews,
agreement measurement, disagreement-only adjudication, and final gold provenance, and
[ADR 0016](docs/adr/0016-content-addressed-agent-evidence-packs.md) for deterministic bounded
agent context, and
[ADR 0017](docs/adr/0017-default-deny-agent-run-preflight.md) for pack-bound authorization,
immutable manifests, and the zero-execution gate, and
[ADR 0018](docs/adr/0018-content-addressed-retrieval-campaigns.md) for chronological reviewed
campaigns, global-union scoring, and baseline-relative trajectories, and
[ADR 0019](docs/adr/0019-gold-blind-relationship-candidate-sandbox.md) for gold-blind candidate
tasks, immutable model identities, paired comparisons, and the closed promotion boundary, and
[ADR 0020](docs/adr/0020-pinned-local-relationship-nli-runner.md) for the separate CPU-only NLI
runner, balanced source budgets, fixed abstention policy, and content-addressed inference trace,
and
[ADR 0021](docs/adr/0021-gold-free-grounded-answer-evaluation.md) for exact live task capture,
atomic cited answers, model-blind review, tokenizer budgets, and non-promoting first-pass metrics,
and
[ADR 0022](docs/adr/0022-pinned-local-grounded-answer-runner.md) for the revision-pinned Qwen GGUF,
owned offline llama.cpp boundary, token preflight, case-local schemas, and blocked inference trace,
and
[ADR 0023](docs/adr/0023-grounded-answer-independent-review-adjudication.md) for per-field
independent-review agreement, reviewer-blind disagreement sheets, third-person adjudication, and
the still-closed release boundary.
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
| Live source | [GDELT 2.0 Event exports](https://www.gdeltproject.org/data.html) | Public, keyless, updated every 15 minutes |
| API/contracts | Python, FastAPI, Pydantic | Open source |
| Event stream | Valkey + `valkey-py` | Open source |
| Durable geospatial state | PostgreSQL + PostGIS + SQLAlchemy/Alembic | Open source |
| Dense retrieval | [FastEmbed](https://github.com/qdrant/fastembed) + [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5) | Apache-2.0 tooling + MIT model; local CPU inference |
| Sparse/vector retrieval | PostgreSQL full-text search + [pgvector](https://github.com/pgvector/pgvector) | Open source; self-hosted |
| Retrieval evaluation | Pydantic, Python CSV, pytest, human judgments | Open source/local; no judge API |
| Claim relationships | Versioned Python rules over source-backed fields | Open source/local; no model or API |
| Relationship evaluation | Pydantic, human gold, ONNX Runtime, tokenizers, DeBERTa-v3-small NLI | Apache-2.0/local CPU; no judge or inference API |
| Grounded-answer evaluation | Pydantic, Qwen3 1.7B Q8 GGUF, llama.cpp, dual protected CSV review, per-field agreement, human adjudication | Apache-2.0/open source/local; no inference or judge API |
| Agent governance | Versioned Python policy checks + canonical JSON identities | Open source/local; no model or agent framework |
| Web command center | React, TypeScript, TanStack Query, Zod | Open source |
| Geospatial UI | MapLibre GL + OpenFreeMap/OpenStreetMap | Open source/public, no key |
| Static serving | Caddy | Open source |
| Observability | OpenTelemetry | Open source; console export by default |
| Toolchain | uv, Ruff, mypy, pytest, Vite, Vitest, Biome | Open source |
| Runtime | Docker Engine/Compose or Podman | Free/open-source options |
| CI | GitHub Actions on this public repository | Free hosted runners for public repos |

## Next milestones

1. Populate the first multi-capture campaign after deployment and use its globally pooled slice
   trajectories to decide whether a free local cross-encoder earns its added latency and
   complexity.
2. Run the live claim-pair benchmark through two independent reviews and adjudication, execute the
   checked-in revision-pinned local NLI candidate, and populate the gold-blind paired report before
   proposing any annotation version or release threshold.
3. Execute the checked-in Qwen grounded-answer candidate over a fresh live gold-free task, complete
   two independent model-blind reviews, resolve disputed fields through the checked-in adjudication
   workflow, and populate the final descriptive report before proposing any threshold or preflight
   policy change.
4. Add explicit approval identity, expiry/revocation, and an append-only signed run ledger before
   enabling evidence triage, impact assessment, or forecasting agents.
5. Add agent trajectory scoring, drift monitoring, and a fully free deployment path.

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

Material-conflict observations come from the [GDELT Project](https://www.gdeltproject.org/), whose
GDELT 2.0 Event stream publishes machine-coded global news metadata every 15 minutes. AtlasPulse
uses the official [latest-update manifest](https://data.gdeltproject.org/gdeltv2/lastupdate.txt)
and follows the [GDELT 2.0 Event codebook](https://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf).
CAMEO category and Goldstein values describe coded event classes rather than verified ground
truth or a measured impact. The repository contains only synthetic GDELT-shaped fixtures.

Licensed under the [MIT License](LICENSE).

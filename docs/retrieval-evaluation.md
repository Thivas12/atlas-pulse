# Human-reviewed retrieval evaluation

AtlasPulse measures retrieval before adding generation. Day 10 adds a reproducible, pooled human
judgment workflow over the changing public-event corpus while retaining the deterministic and
real-service proofs from Day 9.

## What is compared

The production `/v1/search` endpoint exposes four versioned ranking modes over the same bounded
dual-channel candidate contract:

| Mode | Ranking rule | Purpose |
| --- | --- | --- |
| `lexical` | `postgres-english-fts-any-v2` | Bounded any-term PostgreSQL English full-text rank |
| `dense` | `bge-cosine-hnsw-v1` | Local BGE cosine rank only |
| `rrf` | `rrf60-v1` | Reciprocal Rank Fusion with `k=60` |
| `hybrid` | `rrf60-evidence-tiebreak-v2` | RRF with exact-phrase/token coverage only for ties |

All modes preserve identical source, time, expiry, bounding-box, radius, candidate-cap, model,
and citation boundaries. Lexical and dense ablations select only candidates returned by their
channel. RRF and hybrid use their union. Raw FTS and cosine scores are never treated as if they
shared a calibrated scale. The reviewed v1 baseline showed that the original all-term lexical
query returned no candidates and that additive hand-selected reranking weights reduced quality.
[ADR 0011](adr/0011-baseline-driven-retrieval-hardening.md) records the measured decision to
recover lexical candidates before considering a cross-encoder.

## Evidence workflow

The checked-in `evals/retrieval/live-disruptions-v1.json` query set spans all four live sources,
cross-source operator intents, exact and paraphrased language, safety/impact categories, and
geospatial filters. Each capture:

1. validates every case through the production `SearchQuery` contract;
2. queries each configured ranking mode and records rule/model identity and observed latency;
3. takes the depth-limited union and rejects event revisions, model IDs, or per-mode rule IDs that
   change during capture;
4. orders candidates by a query-set-derived hash, independent of every system rank;
5. exports a review CSV with no mode, rank, or score columns;
6. verifies the completed CSV has exactly one grade per candidate and unchanged evidence fields;
7. content-addresses the reviewed pool and emits machine-readable JSON plus Markdown.

The capture JSON keeps system runs for audit and scoring. Reviewers work only from the separate
rank-blind CSV. Generated artifacts are ignored by Git unless a reviewed baseline is intentionally
promoted with reviewer and capture provenance intact.

The relevance scale is graded `0..3`: irrelevant, weakly related, useful, and direct/actionable.
Citation status is excluded from relevance judgment and scored independently. See the exact
rubric and commands in [`evals/retrieval/README.md`](../evals/retrieval/README.md).

## Metrics and gates

Metrics are macro-averaged across queries for every mode and slice:

| Metric | Definition |
| --- | --- |
| Precision@k | Relevant (`grade > 0`) retrieved documents divided by `k` |
| Pooled Recall@k | Judged relevant retrieved documents divided by all relevant documents in the pool |
| MRR@k | Reciprocal rank of the first relevant result, averaged across queries |
| nDCG@k | Graded gain `2^grade - 1` with logarithmic rank discount, normalized by the judged ideal |
| Hit rate@k | Fraction of queries with at least one relevant result |
| Judged rate@k | Fraction of the first `k` positions carrying a judgment |
| Citation traceability@k | Fraction of the first `k` positions with a structurally traceable source URL |
| Candidate coverage | Fraction of queries for which a mode returned at least one candidate |
| Latency | Observed client-side p50 and interpolated p95 for the captured requests |

Reports retain per-query rows for error analysis and aggregate the declared slices, so an overall
gain cannot silently hide a regression on one source, geography, or intent. A `GatePolicy` may set
explicit minimum retrieval metrics or maximum p95 latency. Missing modes/cutoffs and malformed or
incomplete judgments fail closed. Rules can target the overall mode or a declared slice, and their
machine-readable outcomes are embedded in the report. Gate violations exit `1`;
evidence/workflow errors exit `2`. No threshold is checked in before a human-reviewed baseline
exists.

Reports list the exact empty query IDs for every mode. The capture command also warns when all
modes are empty for a query. This distinguishes “nothing was retrieved” from “retrieved evidence
was judged irrelevant”; it does not by itself prove whether ingestion, indexing, expiry, filters,
or ranking caused the gap. `candidate_coverage` can be gated overall or within a declared source
slice and, unlike cutoff metrics, does not take a cutoff.

Reports containing candidate coverage use evaluation report schema `1.1.0`; query sets,
candidate pools, and gate policies remain at schema `1.0.0`.

## Boundaries that remain explicit

- Recall is pooled recall. Relevant evidence outside the union of captured results is unknown.
- Live capture is a short sequence of requests, not a database-wide time-travel snapshot. A
  changed event revision aborts capture, but arrivals between modes can still alter rank context.
- The lexical and dense ablations retain shared dual-channel candidate generation in the current
  service implementation; their latency is end-to-end request latency, not isolated operator cost.
- Unjudged documents receive zero gain and reduce judged rate; they are not proven irrelevant.
- Citation traceability validates safe URL structure and event attachment, not factual truth.
- One reviewer supports development decisions. Public comparative claims should use independent
  duplicate judgments and adjudication.
- Candidate coverage proves only that a mode returned something; it does not prove an eligible
  corpus existed or that an empty result had no relevant evidence upstream.
- Metrics evaluate retrieval, not answer faithfulness, claim entailment, agent decisions, or
  real-world impact. Those require separate versioned datasets before generation is adopted.

## Existing deterministic proof

The real-service integration suite still seeds a synthetic source-shaped corpus into PostgreSQL
and exercises GIN FTS, pgvector HNSW, PostGIS, expiry, temporal filtering, fusion, and idempotent
indexing. Unit and browser suites cover deterministic rendering, revision identity, citation
safety, strict API contracts, and Zod rejection. Synthetic fixtures prove mechanics; only the
reviewed live workflow can support a relevance claim.

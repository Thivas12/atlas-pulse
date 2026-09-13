# ADR 0011: Harden retrieval from the reviewed live baseline

## Status

Accepted

## Context

The first rank-blind review of `live-disruptions.v1` produced report
`live-disruptions.v1-20260913t042021z-a5aacafc9189`. Its reviewed pool is content-addressed by
`a5aacafc9189fc14db58da2d165731f46dd90466eaff9e7c934353679d43c576`.

At cutoff 10, dense retrieval and RRF both achieved `0.5617` nDCG and `0.6000` pooled recall.
The weighted hybrid rule was worse at `0.5486` nDCG and `0.5917` pooled recall. Lexical retrieval
returned no candidates for any of the 15 queries. FIRMS and GDELT query slices also had no
candidates in any mode.

Inspection found two distinct problems:

1. PostgreSQL `websearch_to_tsquery` treats ordinary unquoted terms as a conjunction. Operator
   queries such as “high confidence intense satellite thermal anomaly” therefore required every
   content word to occur, producing an empty lexical channel when one paraphrased term differed.
2. The hybrid rule added uncalibrated token-coverage and exact-phrase weights to RRF. Those values
   could reorder candidates with different RRF evidence and degraded the reviewed result.

A cross-encoder cannot repair a candidate that neither retrieval channel returns. It would also
add latency and model complexity before the cheaper retrieval defect was corrected.

## Decision

1. Compile user text into a syntax-neutral, de-duplicated any-term web-search query. Cap it at 32
   terms, retain the existing database and API candidate limits, and continue using the GIN-backed
   English `tsvector` index.
2. Version the lexical rule as `postgres-english-fts-any-v2`.
3. Make hybrid ranking monotonic with RRF. Exact-phrase and token-coverage evidence may break only
   exact RRF ties; they may not overturn a stronger fused score. Version this rule as
   `rrf60-evidence-tiebreak-v2`.
4. Add candidate coverage and the exact empty query IDs to machine-readable and Markdown reports.
   Allow overall and slice-specific coverage gates without conflating an empty result with an
   irrelevant result.
5. Warn immediately after capture when every mode is empty for a query.
6. Defer a local cross-encoder until a new reviewed capture shows that the repaired candidate pool
   has adequate coverage but poor ordering.

## Consequences

- Lexical recall can recover when only a subset of operator terms appears in an event document.
- Broad OR retrieval may introduce weak candidates. PostgreSQL rank, bounded candidate depth,
  RRF, source/time/geospatial filters, and reviewed evaluation constrain that cost.
- Hybrid no longer claims that hand-selected weights improve relevance. It remains deterministic
  and auditable while preserving evidence features for a future evaluated reranker.
- Empty FIRMS or GDELT queries are visible as coverage gaps. Coverage does not by itself identify
  whether ingestion, expiry, indexing, filtering, or retrieval caused the gap.
- Ranking-rule identity changes deliberately make mixed old/new captures fail provenance checks.
- The implementation remains local and open source; it introduces no paid model or judge API.

## Validation required

After deployment, repeat the same capture and human-review workflow. Compare the new lexical,
RRF, and hybrid results against the content-addressed baseline. Investigate source ingestion and
index freshness separately when FIRMS or GDELT coverage remains zero. Promote a learned reranker
only if it improves reviewed nDCG/MRR without violating latency and source-slice gates.

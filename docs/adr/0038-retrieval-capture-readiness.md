# ADR 0038: Diagnose source-to-index readiness before retrieval capture

## Status

Accepted

## Context

Candidate coverage is an output of retrieval, not a diagnosis. The frozen v1 development capture
returned no FIRMS candidates for either source-specific query, but that observation alone could
not distinguish a disabled credentialed feed, a failed or stale poll, an empty signal projection,
retrieval-index lag, event expiry, a structured-filter mismatch, or ranking behavior. Starting a
new v2 judgment task before resolving those layers would spend review effort on an ambiguous
corpus and could incorrectly attribute a data-pipeline failure to retrieval quality.

The existing API already exposes the evidence needed for a bounded black-box trace: exact build
identity through health/readiness, worker-written source freshness, current and retained signal
visibility, controlled lexical source-marker probes over indexed documents, and dense search over
the shared structured predicates. The source marker is emitted by the deterministic document
renderer, so its lexical probe establishes bounded indexed-row existence without depending on an
approximate nearest-neighbor search vector.

## Decision

1. Add `atlas-pulse-evaluate diagnose` as a required recorded step immediately before a fresh
   live retrieval capture.
2. Require the operator to supply the expected 40-character deployment commit. Abort before corpus
   probes unless `/healthz` and `/readyz` agree on that exact healthy revision and application
   version.
3. Derive required sources from the frozen query set's explicit source filters. For each source,
   record its complete freshness item plus bounded current and retained existence probes against
   `/v1/signals` and lexical `/v1/search`. The search uses the guaranteed rendered `Source` marker
   together with the exact source predicate; it is an index-existence probe, not a relevance test.
4. Block capture when a required source has no configured freshness record, fails freshness, has
   no retained signal, has no retained indexed document, or has a current projected signal that is
   absent from the current retrieval index.
5. Probe every frozen query once in dense mode with its exact structured predicates. Report empty
   query IDs and a bounded reason, but do not block an otherwise healthy capture: a changing live
   corpus may legitimately contain no matching tsunami, tornado, or other rare event. This avoids
   selecting capture time based on a desired benchmark outcome.
6. Request at most one result per visibility probe, retain only document identity and occurrence
   time, and bind the exact response bytes by SHA-256. Do not copy source text into the readiness
   artifact or claim corpus cardinality from the existence check.
7. Preserve the deployed request-budget contract for every retrieval probe, including bounded
   pacing and retry behavior. Record the lexical source-inventory rule separately from the dense
   exact-query rule, and reject model, rule, query-echo, source-identity, or response-contract drift.
8. Emit content-addressed JSON and Markdown under ignored `artifacts/evaluation/`. The report is
   point-in-time pipeline evidence; it is not a relevance judgment, completeness proof, quality
   gate, or availability SLA.

## Consequences

- FIRMS zero coverage can be attributed to a specific observed layer before reviewers see a CSV.
- A healthy retained source corpus is separated from current-event availability and exact
  structured-filter eligibility.
- Captures bind to the revision that was actually probed rather than the operator's intended
  deployment.
- The command adds bounded search traffic before capture, so it honors the same rate-limit pacing
  and may delay the subsequent capture until the next request-budget window.
- Natural empty-query observations remain visible without introducing benchmark-timing selection
  bias.
- Filtered approximate-nearest-neighbor behavior cannot turn an unrelated inventory vector into a
  false source-pipeline blocker; dense mode remains the exact-query eligibility test.

## Rejected alternatives

- **Treat capture coverage as the diagnostic.** It conflates ingestion, projection, expiry,
  indexing, filtering, and ranking after review work has already begun.
- **Expose database row counts through a new public endpoint.** The existing public contracts can
  prove bounded existence without widening the production API or presenting a transient count as
  completeness evidence.
- **Block on every empty exact query.** Rare live conditions can legitimately be absent; waiting
  for all cases to become non-empty would select the corpus based on the benchmark.
- **Use an arbitrary lexical inventory query.** Vocabulary mismatch can make an arbitrary lexical
  result empty. The accepted source probe instead queries a controlled marker that every indexed
  document is guaranteed to contain and still keeps all frozen-query eligibility probes dense.

## Amendment: controlled source marker

The original rule used the unrelated dense query `operational source inventory` for source-index
existence. A live v2 diagnostic returned empty GDELT inventory probes while two later GDELT-filtered
dense queries returned candidates from the same deployment. Filtered HNSW search is query-vector
dependent and therefore cannot serve as a deterministic row-existence test. Readiness rule v2 and
report schema 1.1.0 replace only the source inventory probes with the controlled lexical marker;
the frozen evaluation-query probes remain dense.

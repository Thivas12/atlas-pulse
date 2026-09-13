# ADR 0018: Score retrieval campaigns against one global reviewed union

## Status

Accepted

## Context

The two-capture comparison in ADR 0012 makes one before/after decision trustworthy by rescoring
both systems against their shared judged union. AtlasPulse now needs to observe more than one
transition. Chaining pairwise comparisons would give each transition a different evidence
denominator, so an apparent trajectory could be caused by pool composition rather than ranking
quality. Scoring each capture only against its own candidates has the same problem.

A campaign must also preserve the project's human-evidence boundary. Public events can change
under stable source IDs, reviewer labels can conflict, and live captures can arrive out of order.
Silently accepting any of those states would create a polished trend from incompatible evidence.

## Decision

1. A campaign contains at least two fully reviewed candidate pools supplied in strict
   chronological order.
2. Every pool must retain the same query-set ID and SHA-256, exact query definitions, query IDs,
   and ranking-mode shape. Ranking-rule and embedding-model identities may change and remain
   visible as measured system changes.
3. Form one per-query union of every candidate surfaced anywhere in the campaign. Reuse a
   judgment only when the complete reviewer-visible evidence identity is unchanged. Reject a
   changed document under the same ID or conflicting relevance grades.
4. Rescore every capture against that global judged union at the same cutoffs. Emit absolute
   values plus capture-minus-first-capture deltas for coverage, latency, standard retrieval
   metrics, and every declared source/intent slice.
5. Record each capture's candidate count and its added, dropped, and overlapping candidates
   relative to the immediately preceding capture.
6. Content-address the global union and the complete deterministic campaign report, excluding
   only its generation timestamp and own ID. Preserve every original pool hash, endpoint,
   capture/review time, reviewer, embedding model, and ranking rule.
7. Treat the campaign as evidence for a human model-selection decision. It does not create
   relevance labels, choose thresholds, or authorize a cross-encoder, generator, or agent.

## Consequences

- All points in a reported trajectory share the same pooled-recall and ideal-nDCG denominator.
- An old capture can be rescored when a later capture expands the judged universe without
  altering the original pool artifact.
- Exact hashes make the campaign reproducible from its referenced reviewed pools.
- Live corpus arrivals, revisions, expiry, ingestion state, endpoint changes, and cache effects
  remain confounders. A campaign isolates a ranking change only when the captures read the same
  underlying snapshot.
- A candidate that only existed during a later live capture still enters the campaign-wide
  denominator. The report states this limitation instead of presenting live time-series data as
  a controlled A/B experiment.
- A single reviewer is acceptable for development evidence. Public comparative claims still
  require independently reviewed and adjudicated labels.

## Rejected alternatives

- **Chain pairwise reports.** Each pair has a different candidate union, so the deltas do not form
  one comparable trajectory.
- **Average metrics from independent pool reports.** Their pooled-recall and ideal-ranking
  denominators differ.
- **Fill missing grades with an LLM judge.** This would cross the human-evidence boundary and add
  an uncalibrated model before a baseline is established.
- **Automatically approve a cross-encoder from one positive aggregate delta.** Model selection
  must consider representative slices, latency, repeated captures, and an explicit human
  decision.

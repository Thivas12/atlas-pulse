# ADR 0012: Compare retrieval changes on a shared judged candidate pool

## Status

Accepted

## Context

The first reviewed `live-disruptions.v1` capture identified zero lexical coverage and a weighted
hybrid regression. ADR 0011 changed candidate generation and ranking, but a code-level regression
test cannot establish that live relevance improved. A second capture will contain a changing set
of public events, and scoring each capture against only its own result pool would give the two
systems different pooled-recall denominators and different ideal rankings for nDCG.

Requiring every unchanged event to be graded again adds review effort without adding evidence.
Blindly copying a grade by event ID is unsafe because public events can be revised while retaining
their source identifier. Comparing query sets, evidence revisions, or labels that silently changed
would manufacture a before/after claim.

## Decision

1. A new capture may seed its rank-blind CSV from one previously reviewed pool only when the
   query-set ID and SHA-256 match and the captured query definitions are identical.
2. Reuse a grade and rationale only when every reviewer-visible candidate field is identical,
   including document hash, title, occurrence time, citation metadata, and rendered evidence.
   New or revised evidence remains blank for review.
3. Compare two fully reviewed pools only when their query-set identity, query definitions, and
   ranking modes match.
4. Form the per-query union of candidates from both captures and rescore both systems against that
   same judged universe. This gives pooled recall and ideal nDCG a common denominator.
5. Fail closed if the same query/document ID contains different evidence or if identical evidence
   carries conflicting relevance grades.
6. Emit a versioned JSON comparison plus Markdown tables for candidate-minus-baseline quality,
   latency, source/intent slices, resolved empty queries, and new coverage gaps.
7. Preserve capture endpoints, timestamps, reviewers, model IDs, ranking-rule IDs, and the hashes
   of both original reviewed pools.

## Consequences

- Exact judgment reuse reduces repeated work without allowing an event revision to inherit a stale
  label.
- Both rankings are evaluated against all evidence either system surfaced, eliminating a major
  pooled-evaluation asymmetry.
- A live longitudinal comparison still includes corpus arrivals, expiry, ingestion state, and
  endpoint differences. It isolates a ranking change only when both endpoints use the same
  underlying data snapshot.
- A reused grade remains a human judgment from the earlier review; the output identifies both
  reviewers and review times instead of presenting reuse as a new independent assessment.
- Document identity currently uses `source:event_id`. A changed revision under that identity stops
  comparison rather than being silently coerced into one document.

## Rejected alternatives

- **Compare two independent aggregate reports.** Their judged pools and relevance denominators can
  differ, so a delta can reflect pooling rather than ranking.
- **Copy grades by event ID.** Source records can change without changing the ID.
- **Use an LLM judge for new candidates.** That would add a second uncalibrated model before the
  human baseline is closed and would weaken the explicit evidence boundary.
- **Treat a later live capture as a controlled A/B test.** Without a shared snapshot, time and
  ingestion are confounders and must remain visible caveats.

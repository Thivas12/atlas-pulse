# ADR 0010: Gate retrieval with pooled human judgments

- Status: Accepted
- Date: 2026-09-13

## Context

Deterministic unit and database tests prove that hybrid retrieval executes as designed, but they
do not prove that its results satisfy an operator's information need. The indexed corpus changes
with four public near-real-time feeds, so checked-in labels tied only to rank positions would drift
and an LLM judge would introduce an unmeasured source of bias. Adopting a cross-encoder, generation,
or agents without a relevance baseline would make later improvements impossible to distinguish
from regressions.

## Decision

Use versioned operator-shaped queries and depth-limited pooling across lexical, dense, RRF, and
hybrid ranking modes.

1. Capture exact query filters, model/rule identity, ordered document identities, latency, source
   evidence, and content hashes from the production API.
2. De-duplicate by `source:event_id`; abort if the same identity, embedding model, or per-mode
   ranking rule changes during the capture.
3. Export a separate candidate sheet ordered by a deterministic blind hash. Exclude rank, mode,
   and score so assessors cannot favor the current system.
4. Require a human to assign every candidate an integer relevance grade from 0 through 3. Protect
   evidence columns against edits and import reviewer identity plus review time into a strict pool.
5. Report Precision@k, pooled Recall@k, MRR@k, graded nDCG@k, hit rate, judgment coverage, citation
   traceability, observed latency, source/intent slices, and per-query details.
6. Hash the complete reviewed pool. Refuse incomplete artifacts, implicit overwrites, missing
   modes/cutoffs, and ambiguous gate rules.
7. Establish quality floors only from a reviewed baseline. Do not check in invented thresholds or
   present pooled recall as exhaustive corpus recall.

The workflow uses Python's standard CSV support, Pydantic contracts, and the existing local
retrieval service. It adds no paid model, API, database, or evaluation platform.

## Consequences

- Retrieval changes can be compared to explicit ablations and blocked by inspectable policies.
- Randomized, rank-blind presentation reduces assessor bias while the original runs remain
  auditable in a separate artifact.
- Per-query and slice retention makes failure analysis possible instead of collapsing evidence
  into one leaderboard number.
- A live pool is reproducible as a captured artifact, not as a promise that the upstream corpus
  will remain unchanged.
- Depth-limited pooling cannot discover relevant documents missed by every participating system;
  recall is therefore named and documented as pooled recall.
- Sequential live requests can observe arrivals between modes. Revision changes fail capture,
  while broader snapshot isolation is deferred until evidence shows it is needed.
- Reviewer agreement and adjudication are required before public comparative claims, but not for
  a developer's first internal baseline.

## Rejected alternatives

- LLM-as-judge labels are not a human gold standard and add model/prompt drift before generation
  itself is evaluated.
- Synthetic-only relevance scores are stable but do not establish usefulness on current public
  data.
- Click logs do not exist yet and would mix relevance with UI position and user-selection bias.
- A single aggregate score can hide source and intent regressions.
- Fixed gates before collecting a reviewed baseline manufacture precision without evidence.

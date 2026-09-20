# ADR 0037: Enforce explicit source-native retrieval constraints

## Status

Accepted

## Context

The adjudicated development report for the frozen `live-disruptions.v1` capture showed hybrid as
the strongest overall mode at cutoff 10 (`0.7733` precision, `0.4763` pooled recall, `0.8333` MRR,
and `0.7236` nDCG). Changing fusion again is therefore not the first corrective action.
The two first-pass reviews agreed on `55.51%` of judgments (`0.1580` Cohen's kappa), and the
disagreements were resolved with AI assistance. The report is therefore used for development
triage, not as an independently adjudicated public quality claim.

Failure inspection instead found candidates that matched a topic or location while violating a
condition stated by the information need: earthquakes below a requested magnitude, events without
the requested tsunami flag, and NWS records with the wrong alert type. FIRMS remained empty for
both source-specific queries in every mode. The report also showed weak impact and severity slices,
but aggregate slice metrics cannot distinguish ranking error from an absent eligible corpus.

Embedding similarity and full-text rank are ordering mechanisms. Neither should be treated as a
numeric, boolean, or categorical predicate. Inferring filters from arbitrary query prose would add
an unmeasured parser whose errors were hidden inside every ranking-mode comparison.

## Decision

1. Extend `SearchQuery` with six optional typed constraints: `min_magnitude`, `max_depth_km`,
   `tsunami`, `alert_type`, `min_confidence_rank`, and `observation_period`.
2. Validate finite numeric values, non-negative depth, FIRMS confidence ranks `1..3`, day/night
   values, booleans, and bounded normalized alert labels at the production contract boundary.
3. Map the constraints directly to normalized `event_json.payload` fields. Missing or incorrectly
   typed fields do not match. NWS alert labels match exactly after case normalization.
4. Add the predicates to the one shared PostgreSQL filter set used by lexical and dense candidate
   queries. RRF and hybrid therefore fuse candidates from the same constrained corpus.
5. Expose and echo the same parameters on search, evidence-pack, and agent-preflight endpoints.
   Carry them through retrieval and grounded-answer capture contracts.
6. Include a constraint in the evidence-pack identity only when it is applied. Unconstrained pack
   identities remain stable; constrained packs bind the exact eligibility rules.
7. Keep `live-disruptions.v1` immutable. Add `live-disruptions.v2`, changing only the benchmark
   identity and the five query definitions whose intent names a structured condition.
8. Treat v2 as a new baseline requiring fresh blinded judgments. Do not use the longitudinal
   comparison or campaign commands across v1 and v2 because their query definitions differ.
9. Diagnose empty FIRMS results through ingestion, credential, poll-freshness, and indexing
   evidence. Typed filtering deliberately cannot manufacture an absent corpus.

## Consequences

- Explicit eligibility conditions are enforced before ranking and cannot be outweighed by semantic
  similarity.
- Ranking ablations remain honest because all modes share exactly the same filters.
- API and evidence-pack consumers can reproduce both the query and its structured constraints.
- The feature is source-field based rather than query-specific, so later operator queries can use
  the same contract without hard-coded document IDs or benchmark labels.
- A new reviewed v2 capture is required before claiming a quality gain. The adjudicated v1 report
  identifies the failure mode but is not reused as v2 gold.
- JSON payload predicates add database work. Existing lexical indexes, HNSW iterative scanning,
  candidate bounds, and real-Postgres tests constrain that risk; live latency must still be
  measured in the next capture.

## Rejected alternatives

- **Change fusion weights.** The reviewed failure is candidate eligibility, and a rank weight
  cannot make an ineligible record satisfy a numeric or categorical condition.
- **Parse constraints from free text.** That adds a new semantic component without its own labeled
  parser benchmark and makes mode comparisons harder to interpret.
- **Hard-code evaluation query IDs.** Production retrieval must not know benchmark identities.
- **Rewrite v1 in place.** That would erase the exact definition attached to its reviewed artifacts.
- **Reuse v1 judgments for v2.** A changed eligible corpus and changed query identity require a new
  blinded pool.

# ADR 0014: Gate semantic relationship changes with blinded live claim pairs

## Status

Accepted

## Context

The synthetic `structured-claims-v1` corpus proves exact rule behavior, but it cannot establish
precision or recall on heterogeneous live source text. Evaluating only relationships emitted by
the rule would hide missed claims and make recall unknowable. Asking an LLM to judge its own
proposals would introduce model and prompt drift before a human baseline exists.

Live evidence also changes. A useful artifact must preserve the exact source documents, measured
parent edge, deployed rule output, review rubric, capture bounds, truncation state, and reviewer
identity without revealing the prediction during labeling.

## Decision

Add a separate `claim-pair-rubric-v1` evaluation workflow.

1. Freeze the expected correlation and relationship rule versions plus exact `/v1/incidents`
   parameters in a checked-in benchmark definition.
2. Validate response counts, echoed parameters, graph references, canonical edge orientation,
   and one rule version throughout the capture. Fail rather than compare incompatible data.
3. Group measured edges by unordered source pair and take a stable-hash-capped sample from each
   group. Record available and sampled counts plus both API truncation flags.
4. Expand every sampled edge across every supported predicate. This includes cases where the
   deployed extractor abstains, allowing false abstentions and per-label recall to be measured.
5. Store exact deterministic event documents and hashes with the system claims and prediction in
   the JSON pool. Export a separate CSV that omits all system decisions and uses blind hash order.
6. Protect every reviewer-visible field during import and require one of `corroborates`,
   `contradicts`, or `insufficient_evidence` for every case.
7. Reuse an earlier judgment only when benchmark, rubric, and exact reviewer-visible evidence
   match. Do not invalidate gold labels merely because a later system prediction changes.
8. Report per-label precision/recall/F1, a full confusion matrix, accuracy, macro F1, decisive
   coverage, abstention rate, selective accuracy, predicate/source-pair slices, and error IDs.
9. Keep undefined metrics explicit. Establish no promotion threshold until a representative pool
   is reviewed; require independent review and adjudication before public comparative claims.

## Consequences

- The deterministic abstaining baseline can now be measured rather than described only through
  golden cases.
- Extraction misses appear as false abstentions because evaluation units exist independently of
  emitted relationships.
- Source-pair caps make review feasible and preserve minority pair visibility, but overall scores
  are benchmark scores rather than live-prevalence estimates.
- Captures with API truncation remain useful for bounded error analysis, while explicit flags
  prevent them from being presented as a complete census.
- Future symbolic, NLI, or LLM proposal versions can reuse unchanged human labels without
  exposing prior predictions to reviewers.
- The workflow adds no paid judge, model, API, or data service.

## Rejected alternatives

- **Review emitted relationships only.** This cannot measure missed claims or recall.
- **Show the current label in the sheet.** It anchors reviewers to the system under evaluation.
- **Randomly sample without a recorded seed.** The pool cannot be reproduced or audited.
- **Let the highest-volume source pair dominate.** Aggregate accuracy would hide coverage gaps in
  lower-volume source combinations.
- **Treat `insufficient_evidence` as a negative class only.** It is an operational abstention, so
  coverage and selective accuracy are required alongside ordinary classification metrics.
- **Set quality gates now.** Thresholds without a reviewed live baseline manufacture confidence.

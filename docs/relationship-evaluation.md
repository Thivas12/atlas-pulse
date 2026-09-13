# Human-reviewed claim-pair evaluation

AtlasPulse separates three questions that are easy to collapse accidentally:

1. Did two source records occur near each other in space and time?
2. Do those records make comparable claims?
3. Are either of those claims true?

`spatiotemporal-v1` answers only the first. `structured-claims-v1` makes conservative,
source-backed annotations for the second. The relationship benchmark measures that annotation
layer; it never answers the third.

## Evaluation unit

The benchmark unit is `(measured edge, predicate)`, not an emitted relationship. Each sampled
edge is reviewed for every supported predicate, including predicates where the deployed rule
emitted nothing. This is necessary to measure false abstentions and recall. Evaluating only
emitted relationships would measure precision while hiding claims the extractor missed.

The current predicate set is:

- `hazard_domain`
- `evacuation_state`
- `road_access_state`

Each endpoint is rendered with the same deterministic, bounded event-document function used by
hybrid retrieval. The case identity hashes both rendered documents, the predicate, the immutable
parent edge, measured distance/time, and geometry bases. It intentionally excludes the system
prediction, so the same human label can evaluate a later rule over unchanged evidence.

## Sampling and blinding

The checked-in definition fixes the API rule versions, query bounds, rubric, predicates, and
per-source-pair cap. Capture validates the API's echoed parameters and refuses rule drift,
inconsistent graph counts, cross-incident duplicate edges, unknown node/edge references, or
mixed rule versions.

Edges are grouped by unordered source pair and selected with
`source-pair-stable-hash-v1`. This prevents high-volume pairs from consuming the complete review
budget while avoiding a hand-picked positive set. It also means aggregate metrics describe the
capped benchmark and are not weighted estimates of live prevalence.

The review sheet is prediction-blind. It excludes:

- deployed labels;
- extracted claim values and identifiers;
- rule bases and rationales;
- API order.

Protected evidence columns are checked byte-for-byte on import. Spreadsheet formula prefixes in
public text are neutralized. Every row must receive exactly one rubric label before reviewer name
and UTC review time can be attached to the immutable pool.

## Measurements

The scorer reports:

- per-label support, predicted count, TP, FP, FN, precision, recall, and F1;
- a complete human-label-by-system-label confusion matrix;
- accuracy and macro F1;
- decisive coverage and abstention rate;
- selective accuracy over only non-abstaining predictions;
- the same measurements for every predicate and source pair;
- exact case IDs for all disagreements.

Undefined precision, recall, F1, or selective accuracy is serialized as `null` and rendered as
`N/A`. A missing contradiction in one small live window is not reported as perfect or zero
contradiction performance.

## Promotion boundary

No quality floor is checked in before the first representative human-reviewed pool exists. A
future local NLI or LLM proposer must run over the same evidence cases and preserve model,
prompt, threshold, and runtime identities. It may earn a new annotation version only if the
reviewed comparison shows useful recall gains without unacceptable false decisiveness, latency,
or source/predicate regressions. It may never rewrite the measured graph.

See [ADR 0014](adr/0014-human-reviewed-claim-pair-benchmark.md) for the decision and
[`evals/relationships/README.md`](../evals/relationships/README.md) for commands and the rubric.

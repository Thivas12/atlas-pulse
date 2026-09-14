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

## Independent review and adjudication

A single reviewed pool is useful for internal error discovery, but it is not treated as public
gold. `independent-review-adjudication-v1` requires two complete first-pass reviews from different
people over the exact same capture. The capture identity excludes only human judgments, reviewer
metadata, and its artifact-envelope version. It retains event documents, graph measurements,
request parameters, rule versions, and deployed predictions, so reviews of different systems or
snapshots cannot be mixed.

Review order is canonical by reviewer identity. The agreement report is therefore stable when CLI
arguments are reversed and contains:

- observed and expected marginal agreement;
- Cohen's kappa, or `null` when expected agreement is exactly one;
- a complete first-reviewer-by-second-reviewer confusion matrix;
- predicate and source-pair agreement slices;
- both reviewed-pool hashes and every disagreement case ID.

Only disagreements are written to the adjudication CSV. The sheet exposes both human labels and
rationales but remains blind to system labels, claims, bases, and rationales. Every evidence and
review column is protected during import. A named third adjudicator must resolve each row with an
allowed label and non-empty rationale. Matching reviewer labels pass through without an override.

The resulting schema `1.1.0` gold pool embeds the independent reviewers, both reviewed-pool hashes,
agreement report identity, observed agreement, kappa, adjudicator, UTC timestamp, and decision
count. The ordinary scorer consumes this pool and surfaces the provenance in both JSON and
Markdown. Software can verify artifact identity and distinct names; it cannot prove that the
humans worked independently.

## Gold-blind candidate sandbox

Candidate evaluation is a separate, non-promoting stage. It accepts only a final schema `1.1.0`
pool with independent adjudication provenance. `candidate-task` then emits a content-addressed
JSON task containing the exact reviewer-visible evidence while excluding:

- every human gold label and rationale;
- reviewer and adjudicator identities;
- the captured deployed prediction, claims, bases, and rationales.

The companion CSV contains only the task/case identity, predicate, source pair, and blank
`predicted_label` and `latency_ms` cells. An external offline runner reads the JSON, writes exactly
one of the three rubric labels and one finite non-negative observed latency for every case, and
never needs access to the gold pool.

Import requires a strict candidate definition. A reproducible identity includes the model's exact
40- or 64-hex source revision, the local model artifact SHA-256, adapter version, input-template
SHA-256, runtime and version, and all scalar inference parameters. A repository name plus a
floating `main` or `latest` revision is intentionally invalid.

`candidate-score` verifies the task against the complete capture, protects every CSV identity
field, rejects missing/duplicate/unknown cases, content-addresses the prediction batch, and then
scores the captured deployed rule and candidate over the same adjudicated gold. Its JSON and
Markdown retain:

- paired baseline/candidate metrics and candidate-minus-baseline deltas;
- per-label results plus predicate and source-pair slices;
- both gold-label confusion matrices and a baseline-to-candidate transition matrix;
- exact improvements, regressions, unchanged errors, and changed-but-still-wrong cases;
- descriptive candidate mean, p50, p95, maximum, and total latency.

Every report is `blocked` from promotion. No thresholds are checked in yet, deployed-rule
per-case latency is not measured on the same basis, and an accountable human still must approve a
new relationship rule version after regression verification. The sandbox never executes a model,
rewrites the gold pool, or changes production annotations.

## Pinned local NLI runner

The model executor remains separate from the evaluator. The checked-in
`deberta-v3-small-predicate-nli-v1.json` configuration selects
`cross-encoder/nli-deberta-v3-small` at exact revision
`fa2804872c3b4bd748f38c0185cc85775361e735` and the quantized AVX2 ONNX artifact. Provisioning
downloads only the declared inference files. Runtime exposes no network/model-discovery path,
requires the exact blank task sheet, validates the model's three labels, pad token, and context
window, and
hashes the portable paths plus exact bytes of every model/tokenizer input.

For each predicate case, the adapter creates one symmetric premise containing both different-
source records and two explicit hypotheses: the records corroborate, or they make mutually
exclusive claims. Each record receives the same 640-character head/tail budget before the
tokenizer applies a 384-token `only_first` limit, preserving the short hypothesis. The run trace
records both three-way probability distributions, character/token truncation, total two-
hypothesis latency, the complete system identity, and the predeclared decision policy.

The candidate decides only when the larger hypothesis-entailment score is at least `0.70` and its
lead is at least `0.10`; otherwise it emits `insufficient_evidence`. These are candidate settings,
not validated production thresholds. They are fixed before scoring against adjudicated gold and
may be changed only as a newly identified candidate, never silently tuned on the same benchmark.

The runner receives no gold pool, review file, human rationale, deployed prediction, or production
write capability. Its output still enters `candidate-score`, and both artifacts remain blocked
from promotion. See
[ADR 0020](adr/0020-pinned-local-relationship-nli-runner.md) for the execution decision.

## Promotion boundary

No quality floor is checked in before a representative pool completes independent review and
adjudication. A local NLI or LLM proposer must run through the gold-blind sandbox with immutable
model, artifact, template, adapter, parameter, and runtime identities. It may earn a new
annotation version only after an explicit policy shows useful recall gains without unacceptable
false decisiveness, latency, or source/predicate regressions and a human approves the change. It
may never rewrite the measured graph.

See [ADR 0014](adr/0014-human-reviewed-claim-pair-benchmark.md) for the benchmark decision,
[ADR 0015](adr/0015-independent-review-adjudication.md) for gold-label finalization,
[ADR 0019](adr/0019-gold-blind-relationship-candidate-sandbox.md) for candidate isolation and the
non-promotion boundary,
[ADR 0020](adr/0020-pinned-local-relationship-nli-runner.md) for local NLI execution, and
[`evals/relationships/README.md`](../evals/relationships/README.md) for commands and the rubric.

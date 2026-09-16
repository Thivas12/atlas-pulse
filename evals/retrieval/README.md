# Retrieval judgment assets

`live-disruptions-v1.json` is a versioned set of evergreen, operator-shaped information needs.
It covers all four public sources, exact and paraphrased language, cross-source intents, impact
types, and spatial filters. It contains queries—not relevance answers—so a changing live corpus
cannot masquerade as a frozen quality claim.

Generated pools, review sheets, reports, and policies belong under `artifacts/evaluation/`, which
Git ignores. Promote an artifact into this directory only after review, with its reviewer,
capture time, endpoint, model/rule identities, and content hash intact.

## Relevance rubric

Grade the event against the query and its filters, using only the evidence shown in the blind CSV:

| Grade | Meaning |
| ---: | --- |
| `3` | Directly answers the operational information need with specific, actionable evidence |
| `2` | Clearly relevant and useful, but incomplete or less direct |
| `1` | Topically related; weak operational value for this exact need |
| `0` | Not relevant to the information need |

Do not infer facts absent from the source event. A valid citation URL does not increase relevance;
it is measured separately. Add a short rationale for ambiguous grades. One review is an internal
development baseline only. Public comparative claims require two different people to review the
same untouched capture independently, followed by third-person adjudication of grade
disagreements.

## Live workflow

Run the deployed API locally, then capture all four ablations into one pool and a rank-blind CSV:

```bash
mkdir -p artifacts/evaluation
uv run atlas-pulse-evaluate capture \
  --queries evals/retrieval/live-disruptions-v1.json \
  --base-url http://localhost:8000 \
  --output artifacts/evaluation/pool.json \
  --judgments-output artifacts/evaluation/judgments.csv
```

The complete v1 query set makes 60 bounded search requests. When the deployed expensive-route
budget is exhausted, capture honors the API's integer `Retry-After` and reset headers and prints
each planned wait. A single wait is capped at five minutes, cumulative waiting is capped at
15 minutes, and repeated `429` responses stop after four total attempts. Intentional quota waits
are excluded from the recorded request latency. Do not raise the server budget merely to make an
evaluation finish faster.

For a later capture of the same frozen query set, seed only byte-identical prior evidence from the
reviewed baseline:

```bash
uv run atlas-pulse-evaluate capture \
  --queries evals/retrieval/live-disruptions-v1.json \
  --base-url http://localhost:8000 \
  --output artifacts/evaluation/candidate-pool.json \
  --judgments-output artifacts/evaluation/candidate-judgments.csv \
  --seed-reviewed-pool artifacts/evaluation/reviewed-baseline-pool.json
```

The command reports how many grades were reused and how many blank cells remain. Reuse requires the
same query-set hash and identical captured query definitions. A candidate is prefilled only when
all reviewer-visible evidence is unchanged; a revised event with the same source ID remains blank.

Grade the rank-blind sheet through the resumable terminal workflow:

```bash
uv run atlas-pulse-evaluate judge \
  --pool artifacts/evaluation/pool.json \
  --judgments artifacts/evaluation/judgments.csv
```

The command displays one candidate at a time without mode, score, or rank. Enter `0` through `3`,
optionally add a rationale, `s` to leave one pending, or `q` to stop safely. Every accepted answer
is atomically saved, and rerunning the same command resumes at the first blank grade. It validates
all protected evidence fields before prompting and removes terminal control characters from public
text. Avoid opening and resaving the CSV in spreadsheet software, which may silently rewrite
timestamps or identifiers. Import the completed sheet:

```bash
uv run atlas-pulse-evaluate review \
  --pool artifacts/evaluation/pool.json \
  --judgments artifacts/evaluation/judgments.csv \
  --reviewer "Your Name" \
  --output artifacts/evaluation/reviewed-pool.json
```

Score the reviewed artifact:

```bash
uv run atlas-pulse-evaluate score \
  --pool artifacts/evaluation/reviewed-pool.json \
  --output-json artifacts/evaluation/report.json \
  --output-markdown artifacts/evaluation/report.md
```

## Independent review and adjudication

Keep the original unjudged `pool.json`. Generate a fresh sheet for the second reviewer without
copying, exposing, or seeding the first review:

```bash
uv run atlas-pulse-evaluate review-sheet \
  --pool artifacts/evaluation/pool.json \
  --output artifacts/evaluation/second-judgments.csv

uv run atlas-pulse-evaluate judge \
  --pool artifacts/evaluation/pool.json \
  --judgments artifacts/evaluation/second-judgments.csv

uv run atlas-pulse-evaluate review \
  --pool artifacts/evaluation/pool.json \
  --judgments artifacts/evaluation/second-judgments.csv \
  --reviewer "Second Reviewer's Name" \
  --output artifacts/evaluation/second-reviewed-pool.json
```

The second reviewer must be a different person and must not see the first review, retrieval mode,
rank, or score. Compare the two complete first-pass reviews and export only their disagreements:

```bash
uv run atlas-pulse-evaluate agreement \
  --first-pool artifacts/evaluation/first-reviewed-pool.json \
  --second-pool artifacts/evaluation/second-reviewed-pool.json \
  --output-json artifacts/evaluation/agreement.json \
  --output-markdown artifacts/evaluation/agreement.md \
  --adjudication-output artifacts/evaluation/adjudication.csv
```

The report retains observed agreement, expected marginal agreement, unweighted Cohen's kappa, a
complete `0..3` confusion matrix, exact disagreement identities, and query/source/slice results.
It fails unless both artifacts contain the exact same captured queries, candidates, evidence,
runs, model identity, and ranking-rule identity. Reversing the two command arguments cannot change
the report identity or confusion-matrix orientation.

A third person, different from both first-pass reviewers, resolves only the disagreements. The
terminal hides reviewer identities, swaps review A/B order deterministically per row, validates
all protected evidence, and saves each accepted decision atomically:

```bash
uv run atlas-pulse-evaluate judge-adjudication \
  --first-pool artifacts/evaluation/first-reviewed-pool.json \
  --second-pool artifacts/evaluation/second-reviewed-pool.json \
  --adjudication-sheet artifacts/evaluation/adjudication.csv

uv run atlas-pulse-evaluate adjudicate \
  --first-pool artifacts/evaluation/first-reviewed-pool.json \
  --second-pool artifacts/evaluation/second-reviewed-pool.json \
  --adjudication-sheet artifacts/evaluation/adjudication.csv \
  --adjudicator "Third Person's Name" \
  --output-pool artifacts/evaluation/gold-pool.json \
  --output-json artifacts/evaluation/adjudication.json \
  --output-markdown artifacts/evaluation/adjudication.md
```

Every disagreement requires a final `0..3` grade and a rationale of 10–1000 characters. Agreed
grades pass through unchanged. A header-only adjudication sheet is valid when the reviewers agree
on every candidate, but a distinct adjudicator must still finalize the process provenance. The
schema `1.1.0` gold pool retains both first-pass review hashes, agreement metrics, the agreement
report identity, the adjudicator, and every disagreement decision. Scoring that pool emits report
schema `1.2.0` with the same provenance. All outputs remain under ignored `artifacts/`; do not
commit the live evidence or human judgments as part of the tooling change.

After importing the candidate judgments, compare both reviewed captures against the union of
evidence surfaced by either system:

```bash
uv run atlas-pulse-evaluate compare \
  --baseline-pool artifacts/evaluation/reviewed-baseline-pool.json \
  --candidate-pool artifacts/evaluation/reviewed-candidate-pool.json \
  --output-json artifacts/evaluation/comparison.json \
  --output-markdown artifacts/evaluation/comparison.md
```

The comparison records baseline/candidate/overlap/union query-document candidate counts,
candidate-minus-baseline deltas at every cutoff, resolved and newly empty queries, slice changes,
model/rule identities, latency, and both original pool hashes. It rejects different query sets,
changed evidence under one document ID, and conflicting grades. The JSON is the complete audit
artifact; Markdown is the decision view.

After at least two reviewed captures exist, build a chronological campaign. Repeat `--pool` in
oldest-to-newest order; every capture is rescored against one global judged union:

```bash
uv run atlas-pulse-evaluate campaign \
  --pool artifacts/evaluation/reviewed-baseline-pool.json \
  --pool artifacts/evaluation/reviewed-candidate-pool.json \
  --pool artifacts/evaluation/reviewed-followup-pool.json \
  --output-json artifacts/evaluation/campaign.json \
  --output-markdown artifacts/evaluation/campaign.md
```

The campaign exposes metric and slice trajectories from capture 1, plus candidates added or
dropped between adjacent captures. It rejects unreviewed, duplicate, out-of-order, incompatible,
or conflicting pools. It is a human decision artifact and does not automatically approve a
cross-encoder or any other model.

Only after a reviewed baseline exists, create a `GatePolicy` JSON with explicit floors or latency
ceilings and pass it with `--policy`. A failed rule exits `1`; malformed or incomplete evidence
exits `2`. Outputs are never overwritten unless `--force` is supplied deliberately.

Every report now records `candidate_coverage` and the exact query IDs for which a mode returned
nothing. Capture also prints an immediate warning when all modes are empty for a query. Coverage
can reveal a missing source or retrieval failure, but it cannot distinguish ingestion, indexing,
expiry, filtering, and ranking by itself.

A policy can gate the overall mode or one declared slice. Choose values from an accepted reviewed
baseline; the numbers below only demonstrate the schema:

```json
{
  "schema_version": "1.0.0",
  "policy_id": "reviewed-baseline.v1",
  "rules": [
    {
      "rule_id": "hybrid-ndcg-at-10",
      "mode": "hybrid",
      "metric": "ndcg",
      "cutoff": 10,
      "minimum": 0.7
    },
    {
      "rule_id": "nws-recall-at-10",
      "mode": "hybrid",
      "slice_name": "source-nws",
      "metric": "pooled_recall",
      "cutoff": 10,
      "minimum": 0.8
    },
    {
      "rule_id": "hybrid-p95-latency",
      "mode": "hybrid",
      "metric": "latency_p95_ms",
      "maximum": 1000
    },
    {
      "rule_id": "firms-candidate-coverage",
      "mode": "hybrid",
      "slice_name": "source-firms",
      "metric": "candidate_coverage",
      "minimum": 1.0
    }
  ]
}
```

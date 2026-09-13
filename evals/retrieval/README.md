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
it is measured separately. Add a short rationale for ambiguous grades. For stronger published
claims, use two independent reviewers and adjudicate disagreements before importing the final
sheet.

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

Open `judgments.csv` in VS Code. Fill every `relevance_0_to_3` cell and optional `rationale`; do not
change protected evidence columns. The sheet omits retrieval mode, score, and rank. Public text
that begins like a spreadsheet formula is prefixed with a single quote in the review sheet; the
original event text remains unchanged in the pool. Import it:

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

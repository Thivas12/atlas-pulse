# Claim relationship evaluation

`structured-claims-v1.json` freezes synthetic, source-shaped contract cases for every decisive
and abstaining behavior in `structured-claims-v1`. CI passes them through the production graph
and relationship builders, then compares exact `(label, predicate, normalized_value)` sets.

```bash
uv run pytest tests/test_relationships.py
```

Those cases prove deterministic behavior, not live accuracy. `live-claim-pairs-v1.json` defines
the separate human-reviewed benchmark. It requests one bounded `/v1/incidents` snapshot, groups
measured edges by unordered source pair, and selects at most ten edges per source pair using a
content-stable hash. Every selected edge becomes one case for each supported predicate.

## 1. Capture and blind

Run the full stack until current signals have been projected, then capture the deployed API:

```bash
mkdir -p artifacts/relationship-evaluation
uv run atlas-pulse-evaluate-relationships capture \
  --definition evals/relationships/live-claim-pairs-v1.json \
  --base-url http://localhost:8000 \
  --output artifacts/relationship-evaluation/pool.json \
  --judgments-output artifacts/relationship-evaluation/judgments.csv
```

The JSON pool retains the deployed label, extracted claims, provenance, rule versions, graph
measurements, exact request parameters, source-pair population counts, and API truncation flags.
The CSV deliberately omits all deployed labels, claim values, relationship IDs, bases, and
rationales. Its row order is a deterministic blind hash rather than API or prediction order.

If a prior capture has been fully reviewed, exact evidence can be reused without exposing old
system predictions:

```bash
uv run atlas-pulse-evaluate-relationships capture \
  --definition evals/relationships/live-claim-pairs-v1.json \
  --base-url http://localhost:8000 \
  --output artifacts/relationship-evaluation/candidate-pool.json \
  --judgments-output artifacts/relationship-evaluation/candidate-judgments.csv \
  --seed-reviewed-pool artifacts/relationship-evaluation/reviewed-pool.json
```

Reuse requires the same benchmark definition and rubric plus byte-identical reviewer-visible
evidence and graph measurements. A changed system prediction does not invalidate a human label;
a changed source document does.

## 2. Apply `claim-pair-rubric-v1`

Review only the predicate and the two source documents in each row:

| Label | Use only when |
| --- | --- |
| `corroborates` | Both records explicitly make the same claim for the named predicate at the same applicable scope. |
| `contradicts` | Both records explicitly make mutually exclusive claims for the named predicate at the same applicable scope. |
| `insufficient_evidence` | Either side lacks the named claim, scope is not comparable, text is ambiguous, or records describe different claims. |

Different hazard domains are not contradictions. A spatially nearby fire and conflict can both
exist. For `evacuation_state` and `road_access_state`, require the same named place or subject;
nearby coordinates alone do not create comparable operational claims. Labels describe agreement
between source records, not real-world truth. Add a short rationale for difficult cases.

Do not edit protected evidence columns. Complete every `gold_label`, then import the sheet:

```bash
uv run atlas-pulse-evaluate-relationships review \
  --pool artifacts/relationship-evaluation/pool.json \
  --judgments artifacts/relationship-evaluation/judgments.csv \
  --reviewer "Reviewer name" \
  --output artifacts/relationship-evaluation/reviewed-pool.json
```

## 3. Score

```bash
uv run atlas-pulse-evaluate-relationships score \
  --pool artifacts/relationship-evaluation/reviewed-pool.json \
  --output-json artifacts/relationship-evaluation/report.json \
  --output-markdown artifacts/relationship-evaluation/report.md
```

The report contains the full confusion matrix; precision, recall, and F1 for corroboration,
contradiction, and abstention; overall accuracy and macro F1; decisive coverage; abstention rate;
selective accuracy; predicate/source-pair slices; and every error case. `N/A` is used when a
metric has no denominator rather than silently reporting zero.

The overall score describes this capped benchmark, not live source prevalence. A response that
reports incident or candidate-edge truncation remains usable for bounded error analysis, but the
flags stay visible and must accompany any result. Do not set promotion thresholds until a named
human has reviewed a representative pool. A second independent reviewer and adjudication are
required before making public comparative claims or promoting an NLI/LLM proposal rule.

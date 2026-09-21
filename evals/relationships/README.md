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

Do not edit protected evidence columns. Give two reviewers separate copies of the same blank CSV.
They must complete every `gold_label` independently without seeing the deployed prediction or the
other review. Import each sheet separately:

```bash
cp artifacts/relationship-evaluation/judgments.csv \
  artifacts/relationship-evaluation/reviewer-a.csv
cp artifacts/relationship-evaluation/judgments.csv \
  artifacts/relationship-evaluation/reviewer-b.csv

uv run atlas-pulse-evaluate-relationships review \
  --pool artifacts/relationship-evaluation/pool.json \
  --judgments artifacts/relationship-evaluation/reviewer-a.csv \
  --reviewer "Reviewer A" \
  --output artifacts/relationship-evaluation/reviewed-a.json

uv run atlas-pulse-evaluate-relationships review \
  --pool artifacts/relationship-evaluation/pool.json \
  --judgments artifacts/relationship-evaluation/reviewer-b.csv \
  --reviewer "Reviewer B" \
  --output artifacts/relationship-evaluation/reviewed-b.json
```

One reviewed pool can support internal error analysis. Do not use it for a public comparison or
model-promotion decision.

## 3. Measure agreement and adjudicate disagreements

Compare the two exact reviews and create a system-blind sheet containing only disagreements:

```bash
uv run atlas-pulse-evaluate-relationships agreement \
  --first-pool artifacts/relationship-evaluation/reviewed-a.json \
  --second-pool artifacts/relationship-evaluation/reviewed-b.json \
  --output-json artifacts/relationship-evaluation/agreement.json \
  --output-markdown artifacts/relationship-evaluation/agreement.md \
  --adjudication-output artifacts/relationship-evaluation/adjudication.csv
```

The agreement report contains observed agreement, expected marginal agreement, Cohen's kappa, a
complete reviewer confusion matrix, predicate/source-pair slices, and every disagreement ID.
`N/A` is used when kappa is undefined because expected agreement is exactly one. High agreement
does not prove that either reviewer is correct.

The adjudication CSV contains both human labels and rationales, but still excludes every deployed
system decision. A third named person must choose an allowed `adjudicated_label` and provide a
short `adjudication_rationale` for every row. If the reviewers agreed everywhere, leave the
header-only sheet unchanged. Finalize one content-addressed gold pool:

```bash
uv run atlas-pulse-evaluate-relationships adjudicate \
  --first-pool artifacts/relationship-evaluation/reviewed-a.json \
  --second-pool artifacts/relationship-evaluation/reviewed-b.json \
  --adjudication-sheet artifacts/relationship-evaluation/adjudication.csv \
  --adjudicator "Adjudicator name" \
  --output-pool artifacts/relationship-evaluation/gold-pool.json \
  --output-json artifacts/relationship-evaluation/adjudication.json \
  --output-markdown artifacts/relationship-evaluation/adjudication.md
```

The final pool embeds both independent reviewed-pool hashes, the agreement report ID, observed
agreement, kappa, adjudicator identity, UTC timestamp, and decision count. Protected-field edits,
different captures or system versions, duplicate reviewer identities, missing decisions, blank
rationales, and an adjudicator who matches either reviewer all fail closed.

## 4. Score

```bash
uv run atlas-pulse-evaluate-relationships score \
  --pool artifacts/relationship-evaluation/gold-pool.json \
  --output-json artifacts/relationship-evaluation/report.json \
  --output-markdown artifacts/relationship-evaluation/report.md
```

The report contains the full confusion matrix; precision, recall, and F1 for corroboration,
contradiction, and abstention; overall accuracy and macro F1; decisive coverage; abstention rate;
selective accuracy; predicate/source-pair slices; and every error case. `N/A` is used when a
metric has no denominator rather than silently reporting zero.

The overall score describes this capped benchmark, not live source prevalence. A response that
reports incident or candidate-edge truncation remains usable for bounded error analysis, but the
flags stay visible and must accompany any result. Do not set promotion thresholds until a
representative pool has completed the independent-review and adjudication workflow. See
[`docs/adr/0015-independent-review-adjudication.md`](../../docs/adr/0015-independent-review-adjudication.md)
for the provenance and blinding decision.

## 5. Run a bounded single-review development comparison

Independent duplicate review is necessary only when the result will support a public comparative
or promotion claim. An independently operated development loop can use one complete reviewed pool
without inventing extra reviewer identities. This path is explicit, separately typed, and always
blocked from promotion.

After importing one completed review with the ordinary `review` command, export a label-blind task:

```bash
uv run atlas-pulse-evaluate-relationships development-candidate-task \
  --pool artifacts/relationship-evaluation/reviewed-pool.json \
  --output artifacts/relationship-evaluation/development-task.json \
  --predictions-output artifacts/relationship-evaluation/development-predictions.csv
```

Run the pinned local NLI candidate exactly as shown below, using the development task and prediction
template paths. Then create a development-only comparison and declare whether the single review
used AI assistance:

```bash
uv run atlas-pulse-evaluate-relationships development-candidate-score \
  --pool artifacts/relationship-evaluation/reviewed-pool.json \
  --task artifacts/relationship-evaluation/development-task.json \
  --predictions artifacts/relationship-evaluation/development-predictions.completed.csv \
  --candidate-definition artifacts/relationship-evaluation/candidate-system.json \
  --review-assistance ai_assisted \
  --output-batch artifacts/relationship-evaluation/development-batch.json \
  --output-json artifacts/relationship-evaluation/development-report.json \
  --output-markdown artifacts/relationship-evaluation/development-report.md
```

Use `--review-assistance unassisted` only when no AI system contributed to the labels. The report
records `single-review-development-v1`, the reviewer and pool hash, the assistance declaration, and
an additional single-review promotion blocker. It is not accepted as the independently adjudicated
relationship report required by the agent release workflow. Keep the stricter path below for any
future public quality or promotion claim.

## 6. Evaluate an external local candidate without exposing adjudicated gold

Create a model-facing task only after adjudication:

```bash
uv run atlas-pulse-evaluate-relationships candidate-task \
  --pool artifacts/relationship-evaluation/gold-pool.json \
  --output artifacts/relationship-evaluation/candidate-task.json \
  --predictions-output artifacts/relationship-evaluation/candidate-predictions.csv
```

`candidate-task.json` contains the predicate, exact two source documents, content hashes, and
measured edge context required for inference. It contains no gold label, human rationale,
reviewer identity, deployed label, extracted claim, rule basis, or system rationale. The CSV is a
protected output template. An external offline runner must fill `predicted_label` and
`latency_ms` for every row without changing the task, case, predicate, or source-pair columns.

The repository includes one predeclared free local candidate. First provision only its pinned
model/tokenizer files while online:

```bash
uv run atlas-pulse-cache-relationship-nli \
  --candidate-config \
    evals/relationships/candidates/deberta-v3-small-predicate-nli-v1.json \
  --output artifacts/models/nli-deberta-v3-small
```

Then run inference locally. This command performs no model discovery or download and accepts only
the exact blank sheet paired with the task:

```bash
uv run atlas-pulse-run-relationship-nli \
  --task artifacts/relationship-evaluation/candidate-task.json \
  --predictions-template artifacts/relationship-evaluation/candidate-predictions.csv \
  --candidate-config \
    evals/relationships/candidates/deberta-v3-small-predicate-nli-v1.json \
  --model-dir artifacts/models/nli-deberta-v3-small \
  --output-predictions artifacts/relationship-evaluation/candidate-predictions.completed.csv \
  --output-definition artifacts/relationship-evaluation/candidate-system.json \
  --output-run artifacts/relationship-evaluation/candidate-run.json
```

`candidate-run.json` is a content-addressed gold-blind trace containing the two hypothesis
probability distributions, source/token truncation flags, two-hypothesis latency, exact model
artifact hash, template hash, runtime versions, and every fixed inference parameter. Its
`promotion_status` is always `blocked`. General SNLI/MultiNLI performance from the upstream model
card is not evidence of accuracy on AtlasPulse claims.

The included runner writes the exact candidate system file automatically. Any different external
runner must provide the same strict identity contract:

```jsonc
{
  "schema_version": "1.0.0",
  "candidate_id": "your-local-candidate-v1",
  "model_id": "publisher/model-name",
  "model_revision": "<exact 40-or-64-character lowercase hexadecimal revision>",
  "model_artifact_sha256": "<sha256 of the exact local model artifact>",
  "adapter_version": "relationship-candidate-adapter-v1",
  "input_template_sha256": "<sha256 of the exact input template>",
  "runtime": "onnxruntime",
  "runtime_version": "<exact installed version>",
  "parameters": {
    "max_length": 512,
    "abstention_threshold": 0.7,
    "offline": true
  }
}
```

The revision cannot be a floating branch such as `main` or `latest`. Hash the bytes actually run,
including any quantized or converted model, and version the adapter/template that maps each
predicate and evidence pair into model input.

Import and compare the predictions:

```bash
uv run atlas-pulse-evaluate-relationships candidate-score \
  --pool artifacts/relationship-evaluation/gold-pool.json \
  --task artifacts/relationship-evaluation/candidate-task.json \
  --predictions artifacts/relationship-evaluation/candidate-predictions.completed.csv \
  --candidate-definition artifacts/relationship-evaluation/candidate-system.json \
  --output-batch artifacts/relationship-evaluation/candidate-batch.json \
  --output-json artifacts/relationship-evaluation/candidate-report.json \
  --output-markdown artifacts/relationship-evaluation/candidate-report.md
```

The comparison reports baseline and candidate classification/abstention metrics, all predicate and
source-pair slices, both confusion matrices, baseline-to-candidate transitions, exact paired gains
and regressions, and descriptive candidate latency. It always reports promotion as `blocked`:
there is no checked-in quality policy, the rule has no equivalent isolated per-case timing, and a
new version still needs regression verification plus explicit human approval. Running this command
does not execute a model or modify the gold pool or production annotations. See
[`docs/adr/0019-gold-blind-relationship-candidate-sandbox.md`](../../docs/adr/0019-gold-blind-relationship-candidate-sandbox.md)
and
[`docs/adr/0020-pinned-local-relationship-nli-runner.md`](../../docs/adr/0020-pinned-local-relationship-nli-runner.md).
The separately constrained one-review path is documented in
[`docs/adr/0041-single-review-relationship-development-evaluation.md`](../../docs/adr/0041-single-review-relationship-development-evaluation.md).

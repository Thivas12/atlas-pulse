# Grounded-answer evaluation

`live-grounded-briefs-v1.json` defines ten operator-shaped questions for a live, gold-free
evaluation. Capture binds each question to one exact deployed evidence pack. It does not execute a
generator and contains no reference answer, human grade, or production release signal.

## Artifact chain

| Artifact | Created by | Contains | Never contains |
| --- | --- | --- | --- |
| Task JSON | `capture` | Questions, slices, exact evidence excerpts, hashes, citations, pack identity | Reference answers, model output, grades |
| Submission JSON | `capture`, completed by external runner | Atomic cited claims or explicit abstentions, token counts, latency | Human judgments |
| Candidate batch | `candidate-import` | Exact task binding and immutable candidate identity | Promotion approval |
| Review CSV copies | `candidate-import`, completed independently by two humans | Model-blind claims and only their cited evidence | Candidate/model identity, preset grades |
| Development review | `development-review` | One declared review plus `unassisted` or `ai_assisted` provenance | Independent agreement, promotion approval |
| Development report | `development-score` | Descriptive metrics for one explicitly development-only review | Public quality or production-promotion claim |
| First-pass reviews | `review` twice | Named, content-addressed judgments and rationales | Adjudicated judgment set |
| Agreement report | `compare-reviews` | Per-field observed agreement, Cohen's kappa, exact disagreements, both review hashes | Release verdict |
| Adjudication CSV | `compare-reviews`, completed by a third human | Disputed rows, blinded review A/B grades and rationales | Candidate/model/reviewer identity, agreed-row edits |
| Final reviewed batch | `adjudicate` | Consensus grades, resolved disputed fields, full independent-review provenance | Promotion approval |
| Report JSON/Markdown | `score` | Overall, slice, case, token, latency, and adjudication provenance | Release verdict |

Every derived JSON artifact is content-addressed. All candidate, review, and report artifacts keep
`promotion_status: blocked`.

## 1. Capture deployed evidence

Run AtlasPulse until current evidence has been indexed, then capture the checked-in questions:

```bash
mkdir -p artifacts/grounded-answer-evaluation
uv run atlas-pulse-evaluate-grounded-answers capture \
  --benchmark evals/grounded-answers/live-grounded-briefs-v1.json \
  --base-url http://localhost:8000 \
  --output-task artifacts/grounded-answer-evaluation/task.json \
  --output-submission artifacts/grounded-answer-evaluation/submission.json
```

Capture fails if the API changes a requested query, filter, budget, retrieval count, content hash,
citation identity, or pack identity. A task case with no admitted evidence is retained with
`no_traceable_evidence`; it is not silently dropped.

## 2. Run the pinned local candidate

The checked-in baseline is the official Apache-2.0
[`Qwen/Qwen3-1.7B-GGUF`](https://huggingface.co/Qwen/Qwen3-1.7B-GGUF) Q8 artifact. Its identity is
fixed before review:

| Field | Pinned value |
| --- | --- |
| Candidate | `qwen3-1.7b-q8-grounded-brief-v2` |
| Repository | `Qwen/Qwen3-1.7B-GGUF` |
| Revision | `90862c4b9d2787eaed51d12237eafdfe7c5f6077` |
| File | `Qwen3-1.7B-Q8_0.gguf` (about 1.8 GB) |
| File SHA-256 | `061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a` |
| Template | `grounded-brief-qwen3-v2` |
| Context / output budget | 8,192 / 512 tokens |

Build a local `llama-server` executable using the
[`llama.cpp` server instructions](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md),
then provision the exact model in a network-enabled step:

```bash
mkdir -p artifacts/models/qwen3-1.7b-q8
uv run atlas-pulse-cache-grounded-answer-model \
  --candidate-config evals/grounded-answers/candidates/qwen3-1.7b-q8-grounded-brief-v2.json \
  --output artifacts/models/qwen3-1.7b-q8
```

The cache command requests only the declared file at the exact revision and independently verifies
its SHA-256. The inference command does not download or discover a model. Run it with the captured
task and the local executable:

```bash
uv run atlas-pulse-run-grounded-answer \
  --task artifacts/grounded-answer-evaluation/task.json \
  --candidate-config evals/grounded-answers/candidates/qwen3-1.7b-q8-grounded-brief-v2.json \
  --model-dir artifacts/models/qwen3-1.7b-q8 \
  --llama-server /absolute/path/to/llama-server \
  --output-submission artifacts/grounded-answer-evaluation/submission.qwen3.json \
  --output-definition artifacts/grounded-answer-evaluation/candidate.qwen3.json \
  --output-run artifacts/grounded-answer-evaluation/run.qwen3.json
```

The runner hashes the exact model and `llama-server` bytes, records the runtime version and fixed
parameters, starts an authenticated loopback-only CPU process in llama.cpp offline mode, disables
agent tools and thinking, counts the exact rendered prompt before generation, constrains JSON to
case-local evidence IDs, and reimports every result through the independent evaluator contracts.
The runner atomically checkpoints each completed case beside the requested run artifact and resumes
only when the task, candidate configuration, runtime identity, and completed canonical prefix match
exactly. The content-addressed run trace remains `promotion_status: blocked`.

The v2 brief contract supersedes the non-completing v1 development attempt before human review. It
retains the same pinned model and task boundary while limiting output to three short claims, two
citations per claim, and 512 tokens so a single live case cannot consume an unbounded evaluation
window.

No live candidate execution or quality result is checked into this repository. Run latency and
answer quality remain unknown until the captured task is executed on target hardware and reviewed.

### Alternate external candidate format

Complete every case in `submission.json`. An answered response uses consecutive atomic claims and
task-local evidence IDs:

```json
{
  "status": "answered",
  "claims": [
    {
      "claim_id": "claim-01",
      "text": "One source-backed statement.",
      "evidence_ids": ["evidence-<64 lowercase hex characters>"]
    }
  ],
  "abstention_reason": null
}
```

An abstention has no claims and uses `no_traceable_evidence`, `insufficient_evidence`, or
`conflicting_evidence`. Cases whose pack status is `no_traceable_evidence` must use that exact
reason. Record the candidate tokenizer's measured `input_tokens`, generated `output_tokens`, and
wall-clock `latency_ms` for every case.

The evaluator itself still does not choose or run a model. For a different external system,
describe its exact identity separately:

```json
{
  "schema_version": "1.0.0",
  "candidate_id": "your-local-grounded-brief-v1",
  "model_id": "publisher/model-name",
  "model_revision": "<exact 40-or-64-character lowercase hexadecimal revision>",
  "model_artifact_sha256": "<sha256 of the exact local model artifact>",
  "adapter_version": "grounded-brief-adapter-v1",
  "input_template_sha256": "<sha256 of the exact prompt template>",
  "runtime": "local-runtime",
  "runtime_version": "<exact installed version>",
  "parameters": {
    "context_length": 4096,
    "max_output_tokens": 512,
    "tokenizer_artifact_sha256": "<sha256 of the exact tokenizer artifact>",
    "offline": true
  }
}
```

Import the pinned runner's completed submission and definition—or equivalent outputs from another
fully declared candidate—and create the protected review sheet:

```bash
uv run atlas-pulse-evaluate-grounded-answers candidate-import \
  --task artifacts/grounded-answer-evaluation/task.json \
  --submission artifacts/grounded-answer-evaluation/submission.qwen3.json \
  --candidate-definition artifacts/grounded-answer-evaluation/candidate.qwen3.json \
  --output-batch artifacts/grounded-answer-evaluation/batch.json \
  --output-review-sheet artifacts/grounded-answer-evaluation/review.csv
```

Import rejects missing outputs, foreign citations, changed case identities, floating or malformed
model identity, missing tokenizer identity, context overflow, and output-budget overflow.

For an answered response, each review row contains only the evidence cited by that claim. For an
abstained response, the row contains the complete candidate-visible evidence pack so a reviewer can
actually decide whether abstention was appropriate. Candidate and model identity remain excluded.

## 3A. Bounded single-review development path

An individual development loop does not need to invent independent reviewers. Complete the one
model-blind review sheet, then import it with an explicit assistance declaration:

```bash
uv run atlas-pulse-evaluate-grounded-answers development-review \
  --task artifacts/grounded-answer-evaluation/task.json \
  --batch artifacts/grounded-answer-evaluation/batch.json \
  --judgments artifacts/grounded-answer-evaluation/review.development.csv \
  --reviewer "OpenAI Codex (AI-assisted)" \
  --review-assistance ai_assisted \
  --output-review artifacts/grounded-answer-evaluation/reviewed.development.json

uv run atlas-pulse-evaluate-grounded-answers development-score \
  --task artifacts/grounded-answer-evaluation/task.json \
  --batch artifacts/grounded-answer-evaluation/batch.json \
  --review artifacts/grounded-answer-evaluation/reviewed.development.json \
  --output-json artifacts/grounded-answer-evaluation/report.development.json \
  --output-markdown artifacts/grounded-answer-evaluation/report.development.md
```

This path records `single-review-development-v1`, the reviewer, timestamp, exact task and batch
hashes, and whether assistance was declared. Its artifacts use a separate schema, remain
permanently blocked, and cannot enter the independent agreement or adjudication workflow. Use the
full process below only when independently adjudicated evidence is actually required.

## 3B. Complete two independent model-blind reviews

The CSV intentionally excludes candidate and model identity. Before either reviewer starts, make
two independent copies of the untouched template. Reviewers grade only the question, candidate
claim, and cited excerpts and must not coordinate judgments or edit protected columns.

| Field | Scale | Use |
| --- | --- | --- |
| `support_0_to_3` | `0` contradicted, `1` unsupported, `2` partial, `3` full | How completely the cited excerpts support this exact atomic claim |
| `citation_quality_0_to_2` | `0` irrelevant/contrary, `1` partial, `2` complete/direct | Whether the chosen citations are the right evidence for the claim |
| `answer_relevance_0_to_2` | `0` off-topic, `1` partial, `2` direct | Complete only on `claim-01`; evaluates the answered case as a whole |
| `abstention_appropriate` | `yes` or `no` | Complete only for an abstained case |
| `rationale` | At least ten characters | Explain every grade or abstention judgment |

Import both complete first-pass reviews with distinct human identities:

```bash
uv run atlas-pulse-evaluate-grounded-answers review \
  --task artifacts/grounded-answer-evaluation/task.json \
  --batch artifacts/grounded-answer-evaluation/batch.json \
  --judgments artifacts/grounded-answer-evaluation/review-a.csv \
  --reviewer "First reviewer" \
  --output-review artifacts/grounded-answer-evaluation/reviewed-a.json

uv run atlas-pulse-evaluate-grounded-answers review \
  --task artifacts/grounded-answer-evaluation/task.json \
  --batch artifacts/grounded-answer-evaluation/batch.json \
  --judgments artifacts/grounded-answer-evaluation/review-b.csv \
  --reviewer "Second reviewer" \
  --output-review artifacts/grounded-answer-evaluation/reviewed-b.json
```

Each reviewer judges support against the bounded excerpts, not whether the upstream source is
factually correct. Source text is untrusted quoted data even when shown inside a spreadsheet. The
workflow verifies distinct identities and exact artifact coverage, but software cannot prove that
the reviews were performed independently.

## 4. Compare reviews and adjudicate only disputed fields

```bash
uv run atlas-pulse-evaluate-grounded-answers compare-reviews \
  --task artifacts/grounded-answer-evaluation/task.json \
  --batch artifacts/grounded-answer-evaluation/batch.json \
  --first-review artifacts/grounded-answer-evaluation/reviewed-a.json \
  --second-review artifacts/grounded-answer-evaluation/reviewed-b.json \
  --output-json artifacts/grounded-answer-evaluation/agreement.json \
  --output-markdown artifacts/grounded-answer-evaluation/agreement.md \
  --output-adjudication-sheet artifacts/grounded-answer-evaluation/adjudication.csv
```

The agreement report measures exact row agreement plus observed agreement and unweighted Cohen's
kappa separately for support, citation quality, answer relevance, and abstention appropriateness.
A dimension absent from the candidate batch is not invented. Kappa remains undefined when both
reviewers use one marginal grade exclusively.

The adjudication CSV contains only rows with at least one disputed field. It omits candidate,
model, and reviewer identity; labels the inputs only as review A/B; and deterministically swaps A/B
order per row. The third reviewer completes only `adjudicated_*` columns named in
`disputed_fields` plus `adjudication_rationale`. Agreed fields are protected and cannot be
regraded.

```bash
uv run atlas-pulse-evaluate-grounded-answers adjudicate \
  --task artifacts/grounded-answer-evaluation/task.json \
  --batch artifacts/grounded-answer-evaluation/batch.json \
  --first-review artifacts/grounded-answer-evaluation/reviewed-a.json \
  --second-review artifacts/grounded-answer-evaluation/reviewed-b.json \
  --judgments artifacts/grounded-answer-evaluation/adjudication.csv \
  --adjudicator "Third reviewer" \
  --output-review artifacts/grounded-answer-evaluation/reviewed-final.json \
  --output-json artifacts/grounded-answer-evaluation/adjudication.json \
  --output-markdown artifacts/grounded-answer-evaluation/adjudication.md
```

The adjudicator must differ from both reviewers. Header-only input is valid when the two reviews
agree completely. The finalized review inherits agreed rows, merges decisions only into disputed
fields, retains both first-pass review IDs and hashes, and remains `promotion_status: blocked`.

## 5. Score final descriptive evidence

```bash
uv run atlas-pulse-evaluate-grounded-answers score \
  --task artifacts/grounded-answer-evaluation/task.json \
  --batch artifacts/grounded-answer-evaluation/batch.json \
  --review artifacts/grounded-answer-evaluation/reviewed-final.json \
  --output-json artifacts/grounded-answer-evaluation/report.json \
  --output-markdown artifacts/grounded-answer-evaluation/report.md
```

The report includes mean support, fully supported and unsupported/contradicted claim rates,
citation quality and completeness, answer relevance, abstention appropriateness, strict case
passes, declared slices, token counts, mean/p95 latency, and adjudication provenance. Independent
review removes one release blocker; representative live evidence, approved thresholds,
target-hardware reproduction, a regression policy, and explicit human release are still required.

The evaluator design and rejected alternatives are recorded in
[`ADR 0021`](../../docs/adr/0021-gold-free-grounded-answer-evaluation.md). The separate pinned
execution boundary is recorded in
[`ADR 0022`](../../docs/adr/0022-pinned-local-grounded-answer-runner.md). Independent review and
field-only adjudication are recorded in
[`ADR 0023`](../../docs/adr/0023-grounded-answer-independent-review-adjudication.md).
The explicitly non-promoting single-review alternative is recorded in
[`ADR 0042`](../../docs/adr/0042-single-review-grounded-answer-development-evaluation.md).

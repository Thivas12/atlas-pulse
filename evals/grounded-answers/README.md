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
| Review CSV | `candidate-import`, completed by human | Model-blind claims and only their cited evidence | Candidate/model identity, preset grades |
| Reviewed batch | `review` | Named first-pass judgments and rationales | Adjudicated gold |
| Report JSON/Markdown | `score` | Overall, slice, case, token, and latency measurements | Release verdict |

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
| Candidate | `qwen3-1.7b-q8-grounded-brief-v1` |
| Repository | `Qwen/Qwen3-1.7B-GGUF` |
| Revision | `90862c4b9d2787eaed51d12237eafdfe7c5f6077` |
| File | `Qwen3-1.7B-Q8_0.gguf` (about 1.8 GB) |
| File SHA-256 | `061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a` |
| Template | `grounded-brief-qwen3-v1` |
| Context / output budget | 8,192 / 768 tokens |

Build a local `llama-server` executable using the
[`llama.cpp` server instructions](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md),
then provision the exact model in a network-enabled step:

```bash
mkdir -p artifacts/models/qwen3-1.7b-q8
uv run atlas-pulse-cache-grounded-answer-model \
  --candidate-config evals/grounded-answers/candidates/qwen3-1.7b-q8-grounded-brief-v1.json \
  --output artifacts/models/qwen3-1.7b-q8
```

The cache command requests only the declared file at the exact revision and independently verifies
its SHA-256. The inference command does not download or discover a model. Run it with the captured
task and the local executable:

```bash
uv run atlas-pulse-run-grounded-answer \
  --task artifacts/grounded-answer-evaluation/task.json \
  --candidate-config evals/grounded-answers/candidates/qwen3-1.7b-q8-grounded-brief-v1.json \
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
The content-addressed run trace remains `promotion_status: blocked`.

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

## 3. Review without model identity

The CSV intentionally excludes the candidate and model name. Grade only the question, candidate
claim, and cited excerpts. Do not edit the protected columns.

| Field | Scale | Use |
| --- | --- | --- |
| `support_0_to_3` | `0` contradicted, `1` unsupported, `2` partial, `3` full | How completely the cited excerpts support this exact atomic claim |
| `citation_quality_0_to_2` | `0` irrelevant/contrary, `1` partial, `2` complete/direct | Whether the chosen citations are the right evidence for the claim |
| `answer_relevance_0_to_2` | `0` off-topic, `1` partial, `2` direct | Complete only on `claim-01`; evaluates the answered case as a whole |
| `abstention_appropriate` | `yes` or `no` | Complete only for an abstained case |
| `rationale` | At least ten characters | Explain every grade or abstention judgment |

Import one complete first-pass review:

```bash
uv run atlas-pulse-evaluate-grounded-answers review \
  --task artifacts/grounded-answer-evaluation/task.json \
  --batch artifacts/grounded-answer-evaluation/batch.json \
  --judgments artifacts/grounded-answer-evaluation/review.csv \
  --reviewer "Reviewer name" \
  --output-review artifacts/grounded-answer-evaluation/reviewed.json
```

The reviewer judges support against the bounded excerpts, not whether the upstream source is
factually correct. Source text is untrusted quoted data even when shown inside a spreadsheet.

## 4. Score descriptive evidence

```bash
uv run atlas-pulse-evaluate-grounded-answers score \
  --task artifacts/grounded-answer-evaluation/task.json \
  --batch artifacts/grounded-answer-evaluation/batch.json \
  --review artifacts/grounded-answer-evaluation/reviewed.json \
  --output-json artifacts/grounded-answer-evaluation/report.json \
  --output-markdown artifacts/grounded-answer-evaluation/report.md
```

The report includes mean support, fully supported and unsupported/contradicted claim rates,
citation quality and completeness, answer relevance, abstention appropriateness, strict case
passes, declared slices, token counts, and mean/p95 latency. These are first-pass descriptive
measurements. A second independent review, disagreement adjudication, representative evidence,
approved thresholds, target-hardware reproduction, and explicit human release are still required.

The evaluator design and rejected alternatives are recorded in
[`ADR 0021`](../../docs/adr/0021-gold-free-grounded-answer-evaluation.md). The separate pinned
execution boundary is recorded in
[`ADR 0022`](../../docs/adr/0022-pinned-local-grounded-answer-runner.md).

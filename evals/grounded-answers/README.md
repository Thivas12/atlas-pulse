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

## 2. Run one external candidate

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

The evaluator does not choose or run a model. Describe the exact external system separately:

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

Import the completed submission and create the protected review sheet:

```bash
uv run atlas-pulse-evaluate-grounded-answers candidate-import \
  --task artifacts/grounded-answer-evaluation/task.json \
  --submission artifacts/grounded-answer-evaluation/submission.json \
  --candidate-definition artifacts/grounded-answer-evaluation/candidate.json \
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

The design and rejected alternatives are recorded in
[`ADR 0021`](../../docs/adr/0021-gold-free-grounded-answer-evaluation.md).

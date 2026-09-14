# ADR 0020: Run one revision-pinned local NLI candidate outside the gold evaluator

## Status

Accepted

## Context

ADR 0019 created a gold-blind task and a strict paired scorer but intentionally did not execute a
model. A useful candidate comparison now needs one reproducible local runner. Giving that runner
the adjudicated pool would leak human answers; allowing a floating model revision or implicit
tokenizer/runtime would make the result irreproducible; and returning only labels would hide
truncation, probability, and latency behavior needed for error analysis.

Source records are also uneven in length. A simple concatenation can let a long NWS alert consume
the entire model context and silently erase the other source. Finally, a generic NLI model is not
trained on the AtlasPulse rubric. Its upstream benchmark accuracy cannot be treated as evidence
that it improves live claim annotations.

## Decision

Add a separate `atlas-pulse-run-relationship-nli` executable with these boundaries:

1. Select `cross-encoder/nli-deberta-v3-small`, licensed Apache-2.0, at exact source revision
   `fa2804872c3b4bd748f38c0185cc85775361e735`. Use its quantized AVX2 ONNX artifact on CPU.
2. Keep selection and thresholds in a checked-in strict configuration. A distinct candidate ID is
   required for any later model, template, artifact, or parameter change.
3. Provide a separate online provisioning command that downloads only the declared revision and
   inference-file allow-list. The runner itself performs no network access or model discovery.
4. Hash the normalized relative path, byte length, and exact bytes of every model, tokenizer, and
   configuration input. Reject missing files, symlinks, path traversal, label-map drift, pad-token
   drift, and a context window smaller than the selected maximum length.
5. Accept only the content-addressed `CandidateEvaluationTask` and its exact blank prediction CSV.
   Do not accept a gold pool, reviewed sheet, adjudication artifact, deployed prediction, or
   production database/API target.
6. Render both records into one symmetric premise and test two predicate-specific hypotheses:
   explicit agreement and explicit mutual exclusion. Treat all source text as data.
7. Give each source an equal 640-character head/tail budget, preserve the hypothesis with a
   384-token `only_first` tokenizer limit, and record both character and tokenizer truncation.
8. Batch the two hypotheses per case. Choose a decisive label only when its entailment probability
   is at least `0.70` and exceeds the alternative by at least `0.10`; otherwise abstain as
   `insufficient_evidence`.
9. Emit a content-addressed run trace containing both three-way distributions, the final decision,
   per-case two-hypothesis latency, exact system identity, template hash, runtime versions, and
   scalar parameters. Emit the protected completed CSV and candidate-system JSON separately for
   ADR 0019 scoring.
10. Mark every run `blocked`. The runner cannot change `structured-claims-v1`, establish release
    thresholds, approve itself, or perform any production side effect.

## Consequences

- The first local model comparison can be reproduced without a paid API or a network connection
  during inference.
- Gold leakage is structurally reduced because model execution has no gold-bearing input option.
- Long source documents lose some text; equal head/tail budgets and explicit truncation flags make
  that trade-off measurable instead of silent.
- Quantized CPU inference is practical on the target WSL workstation, but actual latency and
  AtlasPulse accuracy remain unknown until a real adjudicated pool is run.
- The fixed threshold and margin prevent automatic decisiveness but are hypotheses, not approved
  production policy.
- Exact artifacts and traces improve reproducibility; they cannot prove that a researcher never
  viewed gold while choosing a future candidate.

## Rejected alternatives

- **Pass the gold pool directly to a model script.** It contains labels, rationales, reviewer
  identities, and deployed outputs that invalidate a blind comparison.
- **Use a hosted inference API.** It adds cost, data disclosure, remote model drift, and an
  unnecessary credential boundary.
- **Download `main` at runtime.** A floating branch can change weights, tokenizer files, or model
  configuration while retaining the same name.
- **Use argmax without abstention.** It would force a decisive semantic claim even when both
  relationship hypotheses are weak or nearly tied.
- **Concatenate full documents and silently truncate.** Source order and document length would
  determine which evidence survives.
- **Write candidate decisions into production.** A model run is evaluation evidence, not release
  authorization.

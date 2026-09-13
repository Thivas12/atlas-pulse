# ADR 0019: Evaluate relationship candidates in a gold-blind, non-promoting sandbox

## Status

Accepted

## Context

An independently reviewed and adjudicated claim-pair pool can measure the deployed
`structured-claims-v1` rule. Evaluating a local NLI or LLM candidate over that same pool creates
three additional risks:

- exporting gold labels or current predictions to the candidate runner would leak answers;
- accepting partial, reordered, or differently sourced predictions would invalidate the paired
  comparison;
- a favorable score could be mistaken for authorization to change production annotations even
  though no promotion thresholds, comparable baseline latency, or human approval exists.

Model names and floating registry branches are also insufficient provenance. Quantization,
conversion, tokenizer/input templates, adapter logic, runtime versions, and inference parameters
can change results while retaining the same marketing name.

## Decision

Add a separate `candidate-task` and `candidate-score` workflow after final adjudication.

1. Refuse to create a candidate task from an unreviewed or single-review pool. A schema `1.1.0`
   pool with independent adjudication provenance is mandatory.
2. Export only the evidence fields shown to human reviewers: exact source documents and hashes,
   predicate, source pair, measured parent edge, distance/time, and geometry bases. Exclude human
   labels, rationales, reviewer/adjudicator identities, extracted claims, and deployed predictions.
3. Content-address the task and bind it to the complete capture hash. The opaque capture hash
   includes the deployed rule state but no human judgment.
4. Produce a protected CSV containing task identity, case identity, predicate, and source pair.
   Require exactly one allowed label and one finite non-negative observed latency for every case.
5. Require an immutable candidate definition: candidate and adapter versions, a 40- or 64-hex
   model revision, model artifact SHA-256, input-template SHA-256, runtime/version, and explicit
   scalar inference parameters.
6. Content-address the imported prediction batch. Reject missing, duplicate, unknown, tampered,
   or task-mismatched rows.
7. Compare the candidate and the captured deployed rule against the same adjudicated labels.
   Report baseline/candidate accuracy, macro F1, decisive coverage, abstention, selective accuracy,
   per-label metrics, predicate/source-pair slices, both confusion matrices, prediction
   transitions, and exact case-level improvements and regressions.
8. Report candidate p50/p95 latency descriptively. Do not compare it with the pool's HTTP capture
   latency, which measures a different operation.
9. Set every candidate comparison's promotion status to `blocked`. The report always names the
   missing thresholds, comparable baseline timing, explicit human approval, regression
   verification, and new versioned rule as release blockers.
10. Keep the workflow offline and model-agnostic. It imports external predictions; it does not
    download, execute, select, or promote a model and never mutates production annotations.

## Consequences

- Multiple free local candidates can be compared over identical evidence without exposing the
  gold labels in their input artifact.
- Paired improvements and regressions remain visible even when aggregate accuracy is unchanged.
- Exact model, template, adapter, runtime, and parameter identities make results reproducible and
  invalidate floating `main` or `latest` model references.
- Candidate latency remains useful for sizing but cannot be presented as a speedup or regression
  until the deployed rule has an equivalent isolated measurement.
- Software can prove content identity and complete prediction coverage. It cannot prove that a
  researcher avoided consulting gold labels while choosing or tuning the model.
- A comparison artifact supplies evidence for a future policy decision but deliberately cannot
  make that decision.

## Rejected alternatives

- **Give the model the adjudicated pool JSON.** It contains gold labels, human rationales, and
  deployed outputs, making a blind comparison impossible.
- **Identify a model by repository name and branch.** Floating revisions and converted artifacts
  are not reproducible identities.
- **Accept only labels without task hashes.** Predictions could silently target a different
  capture or evidence rendering.
- **Compare aggregate accuracy only.** It hides abstention behavior, minority slices, and paired
  regressions offset by unrelated gains.
- **Use API capture latency as the baseline.** It measures graph retrieval and serialization, not
  isolated relationship inference.
- **Auto-promote the winner.** No representative live evidence, policy thresholds, regression
  suite decision, or accountable human authorization is encoded yet.

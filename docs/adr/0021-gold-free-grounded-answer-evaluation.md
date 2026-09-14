# ADR 0021: Evaluate grounded answers through gold-free capture and model-blind review

## Status

Accepted

## Context

ADR 0016 made retrieved evidence bounded, traceable, content-addressed, and explicitly untrusted.
ADR 0017 kept every generative and agent capability blocked. The next release question is not
whether a model can produce fluent prose; it is whether each atomic statement is supported by the
exact excerpts it saw, whether its citations are useful, whether it answers the operator's
question, and whether it abstains when evidence is absent or insufficient.

Putting reference answers into a candidate task would leak the evaluation target. Asking a model
to grade its own output would make the result circular. Free-form prose without atomic claim and
citation structure would also make support errors hard to locate. Finally, tokenizer overflow and
latency can be hidden unless the external runner records them against an immutable candidate
identity.

## Decision

Add a separate `atlas-pulse-evaluate-grounded-answers` workflow with these boundaries:

1. Capture the checked-in live questions by requesting one deployed `/v1/evidence-packs` result
   per query. Reconstruct and independently validate every pack identity, retrieval echo, budget,
   citation, text hash, count, and status before accepting it.
2. Produce a content-addressed task containing the operator question, declared slices, exact
   admitted excerpts, citation URLs, and source hashes. Do not include a reference answer, human
   judgment, model output, or promotion label.
3. Require an external runner to return either consecutive atomic claims with explicit task-local
   evidence IDs or an explicit abstention. A case with no traceable evidence must use the matching
   abstention and cannot borrow citations from another case.
4. Bind every imported batch to the exact task plus an immutable model revision, model artifact,
   input template, adapter, runtime, and scalar inference parameters. Require the external runner
   to report input tokens, output tokens, and latency for every case.
5. Require `context_length`, `max_output_tokens`, and `tokenizer_artifact_sha256`. Reject a prompt
   whose measured input plus declared output budget exceeds the context window or whose observed
   output exceeds the declared maximum.
6. Render a model-blind CSV that includes the question, answer, atomic claim, and only its cited
   excerpts. Exclude candidate and model identity. JSON-encode prose fields so source text cannot
   become an active spreadsheet formula.
7. Protect every task, answer, claim, and evidence field during review import. A named human grades
   support (`0..3`), citation quality (`0..2`), answer relevance (`0..2`, once per answered case),
   or abstention appropriateness, with a rationale for every judgment.
8. Emit a content-addressed descriptive report with overall, declared-slice, and per-case metrics,
   plus token and latency observations. A strict case pass means every claim is fully supported,
   every citation is complete, and relevance is direct, or the abstention is appropriate.
9. Keep candidate batches, first-pass reviews, and reports permanently marked `blocked`. They do
   not update `agent-authorization-v1`, select a model, expose an answer API, or execute an agent.
10. Implement any local generator as a separate runner that accepts only the gold-free task. Add a
    second independent review and disagreement adjudication before defining a release threshold.

## Consequences

- A local generator can be compared on exact live evidence without receiving human answers or
  reviewer rationales.
- Support and citation failures are inspectable at claim level instead of being hidden inside one
  holistic score.
- Empty retrieval is a measured abstention case, not permission to invent an answer.
- Context fit is checked against the candidate's own pinned tokenizer measurements, while the
  evidence pack retains its independent character budgets.
- Human grades remain bounded judgments over retrieved excerpts. They do not verify that a public
  source is true, current, complete, or representative.
- One review is useful for error analysis but is not independent adjudicated gold. Promotion and
  the default-deny preflight therefore remain unchanged.
- The evaluator trusts the external runner's token and latency observations until they are
  reproduced on the target hardware.

## Rejected alternatives

- **Ship a generated-answer endpoint first.** There is no evaluated adapter, reviewed live corpus,
  approved threshold, or authorization path for that behavior.
- **Include ideal answers in the task.** This leaks the target and encourages answer imitation
  rather than evidence-grounded generation.
- **Use an LLM judge.** It adds another unvalidated model, cost or artifact boundary, and circular
  reasoning where human review is still required.
- **Grade only the final paragraph.** Holistic scores cannot reliably identify unsupported atomic
  claims or the excerpt responsible for a citation failure.
- **Let citations reference URLs or free text.** Stable task-local evidence IDs provide exact
  membership checks and preserve the source hash and excerpt seen by the model.
- **Treat one reviewer or a high mean score as release approval.** Neither establishes agreement,
  representativeness, a regression policy, or explicit human authorization.

# ADR 0025: Gate agent review with observable trajectories and longitudinal evidence

## Status

Accepted

## Context

ADR 0024 made human intent proposal-scoped, signed, expiring, and revocable, but human approval is
not evidence that a candidate behaves consistently. AtlasPulse also has separate relationship and
grounded-answer evaluation artifacts, yet no contract joins them to observable agent behavior,
detects regression across captures, or states the exact thresholds required before human review.

Capturing free-form reasoning would create a sensitive, unstable interface and would not prove that
the candidate respected its runtime capabilities. Enabling the agent merely because one report or
one operator looks favorable would collapse evaluation, approval, and execution into one unsafe
decision.

## Decision

Introduce `observable-agent-trajectory-v1`, longitudinal drift reports, a content-addressed
`agent-release-thresholds-v1` policy, `agent-release-assessment-v1`, and the default-deny
`agent-authorization-v3` preflight.

1. Record only observable action metadata, protected evidence/claim IDs, duration, and boolean
   capability observations. Do not accept prompts, rationales, scratchpads, hidden reasoning, or
   chain-of-thought.
2. Bind every trajectory submission to one exact grounded task and candidate batch. Reject missing
   cases, changed query identities, foreign evidence or claims, ambiguous action shapes, incomplete
   capability observations, and invalid termination.
3. Keep capture and import offline. The trajectory CLI cannot invoke a model, network, tool, API
   answer surface, or side effect; it only validates supplied observations and writes artifacts.
4. Score each trajectory only against the independently adjudicated grounded-answer report for the
   exact same task, response batch, and immutable candidate-system identity.
5. Measure policy compliance, required evidence inspection, exact claim-to-citation tracing,
   termination consistency, grounded strict pass, joined end-to-end pass, steps, tokens, and
   latency. Mark every score report blocked from promotion.
6. Compare only chronological reports with the same benchmark and candidate-system identity.
   Threshold quality-rate drops and relative p95 latency/step increases, and report every failed
   metric without asserting why it changed.
7. Require a complete consecutive drift chain across the selected capture series. A chain with
   missing, duplicate, reversed, unrelated, or extra links fails closed.
8. Pin conservative v1 thresholds in a checked-in content-addressed policy. Require representative
   relationship evidence, three grounded/trajectory captures, minimum sample sizes, strict
   grounding and trajectory rates, bounded runtime observations, and stable drift.
9. Emit only `blocked` or `eligible_for_human_review`. The latter means all supplied evidence met
   the exact policy; it cannot authorize execution and still requires a separate human approval.
10. Let API startup optionally load one explicit assessment from a regular non-symlink file no
    larger than 5 MB. Validate the full Pydantic contract and every content identity before using
    it. Absence or invalidity never degrades into a pass.
11. Bind the reduced assessment identity, upstream report IDs, gate-family outcomes, and blocking
    reasons into the proposal. Selecting new release evidence therefore invalidates old approval
    scope.
12. Add three preflight checks: observable trajectory evaluation, trajectory drift monitoring, and
    release-threshold policy. Quality eligibility, human approval, and execution release remain
    three distinct gates. Execution stays hard-disabled under v3.

## Consequences

- Reviewers can inspect exactly what behavior was measured without collecting hidden reasoning.
- A favorable grounded answer is insufficient when the trajectory skipped evidence, mislinked a
  claim, used a forbidden capability, or regressed across captures.
- Release evidence is reproducible and tamper-evident across relationship, grounded, trajectory,
  drift, policy, proposal, and manifest identities.
- Adding assessment evidence changes proposal scope, so operational sequencing matters: assess
  first, then grant approval to that exact proposal.
- Instrumentation observations do not prove sandbox isolation. Target-host reproduction,
  monitoring, rollback, and explicit execution design remain future work.
- Strict 100% trajectory component thresholds may block early candidates. That is intentional;
  threshold relaxation requires a new policy identity and reviewable rationale.
- The repository gains no production answer endpoint, model daemon, tool integration, automatic
  approval, or autonomous agent claim. No live quality result is fabricated.

## Rejected alternatives

- **Capture chain-of-thought for scoring.** Hidden reasoning is not a stable public contract, can
  expose sensitive data, and does not prove which observable capability was used.
- **Use only final-answer quality.** A plausible answer can conceal skipped evidence, incorrect
  claim linkage, forbidden access, or brittle behavior.
- **Evaluate one hand-picked run.** A single capture cannot establish longitudinal stability or
  expose regressions across changing live evidence.
- **Compare any two reports.** Drift is meaningful only for the same immutable candidate and
  benchmark in chronological order.
- **Let tests satisfy release.** Synthetic fixtures validate code paths, not live candidate quality
  or target-runtime behavior.
- **Let a passing assessment activate approval.** Quality evidence is not human intent, actor
  identity, or permission.
- **Let approval activate execution.** Human intent is not a substitute for an explicit execution
  architecture, operational controls, rollback, and production validation.

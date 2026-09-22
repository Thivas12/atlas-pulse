# ADR 0042: Separate single-review grounded-answer development evidence

## Status

Accepted.

## Context

The independently adjudicated grounded-answer workflow is appropriate for promotion-quality
evidence, but an individual development loop may have only one reviewer or may use declared AI
assistance. Inventing reviewer identities or presenting one assisted review as independent human
agreement would create false provenance. The existing first-pass artifact also described every
review as human-only, so it could not honestly represent that workflow.

Abstention review had a second gap: the model-blind CSV exposed no evidence on an abstention row.
Without the evidence pack that the candidate saw, a reviewer could not determine whether refusing
to answer was appropriate.

## Decision

Add a separate, permanently non-promoting development path.

1. `candidate-import` continues to bind the exact task, candidate, submission, and review template.
2. Abstention rows contain the complete candidate-visible evidence pack. Answered claim rows still
   contain only their cited evidence.
3. `development-review` imports one complete protected review sheet and requires an explicit
   `unassisted` or `ai_assisted` declaration.
4. The review records `single-review-development-v1`, reviewer, timestamp, assistance declaration,
   and exact task and candidate-batch hashes under artifact schema `1.2.0`.
5. `development-score` emits descriptive quality, abstention, token, and latency measurements under
   the same development-only boundary.
6. Regular `score` rejects development reviews, while `development-score` rejects first-pass or
   adjudicated reviews. Independent comparison and adjudication continue to accept only their
   existing review types.
7. Every development artifact remains `promotion_status: blocked` and states that it cannot support
   a public quality claim, production promotion, answer endpoint, or agent execution.

## Consequences

- Individual development work can be recorded honestly without manufacturing organizational
  process.
- Assistance provenance is explicit but remains operator-declared rather than independently
  verified.
- Abstention appropriateness becomes reviewable against the same bounded evidence visible to the
  candidate.
- Independent adjudication remains the required path if promotion-quality evidence is later needed.

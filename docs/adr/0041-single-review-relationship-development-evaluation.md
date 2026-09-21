# ADR 0041: Separate single-review relationship development evidence

## Status

Accepted

## Context

The relationship candidate sandbox originally accepted only a schema `1.1.0` pool produced by two
independent reviewers and a distinct adjudicator. That boundary is appropriate for public quality
comparisons and promotion decisions, but it makes ordinary error analysis impractical for an
independently operated system. Relaxing the existing commands would make a one-review result easy
to mistake for independently adjudicated gold. Inventing reviewer identities would make the
provenance false.

## Decision

Add a separate, permanently non-promoting development workflow.

1. `development-candidate-task` accepts exactly one complete schema `1.0.0` reviewed pool. It
   rejects unjudged pools and independently adjudicated pools.
2. The exported task remains label-blind and contains the same evidence-only payload used by the
   strict candidate runner. Reviewer identity, labels, rationales, and deployed predictions remain
   absent.
3. `development-candidate-score` requires the operator to declare review assistance as
   `unassisted` or `ai_assisted`.
4. The report records `single-review-development-v1` provenance, the reviewed-pool hash, reviewer,
   review timestamp, and assistance declaration.
5. The report uses a separate strict artifact type and the explicit
   `single_review_development` scope. It always reports promotion as blocked and names missing
   independent review and adjudication as an additional blocker.
6. The existing `candidate-task`, `candidate-score`, and agent release contracts remain unchanged.
   They continue to require independently adjudicated gold, so a development report cannot enter
   the release path accidentally.

## Consequences

- One operator can run a bounded local NLI experiment without performing review theater.
- AI-assisted labels are disclosed rather than presented as independent human review.
- Candidate inputs remain isolated from labels, and paired errors remain inspectable.
- Development metrics cannot support public quality, production promotion, or release claims.
- A future public comparison still requires the independent-review and adjudication workflow.

## Rejected alternatives

- **Relax the existing candidate commands.** This would blur the promotion boundary and weaken
  existing artifact guarantees.
- **Reuse one person or an AI assistant under multiple reviewer names.** Distinct strings are not
  independent judgments and would create misleading provenance.
- **Skip provenance and run an ad hoc script.** That would lose exact pool, task, candidate, and
  prediction identities and make results harder to reproduce.
- **Require independent review for every iteration.** That cost is disproportionate for bounded
  development decisions and encourages either stale evidence or false reviewer metadata.

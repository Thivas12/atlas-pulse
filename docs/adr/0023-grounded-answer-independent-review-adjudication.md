# ADR 0023: Finalize grounded-answer judgments through independent review and field adjudication

## Status

Accepted

## Context

ADR 0021 introduced claim-level human grading for grounded answers, while ADR 0022 added a pinned
local candidate runner that cannot see review artifacts. One first-pass review is useful for error
analysis, but it does not measure reviewer consistency. Treating it as final would hide ambiguity
in support, citation quality, answer relevance, and abstention judgments.

Grounded-answer rows differ from the single-label relationship benchmark. An answered claim has
support and citation grades, the first claim also carries case-level relevance, and an abstention
has only an appropriateness decision. Two reviewers may agree on some fields in a row and disagree
on another. Sending the entire corpus or every field back to an adjudicator would allow consensus
grades to drift and expose more candidate output than necessary. Showing reviewer identities could
also create authority anchoring, while showing candidate identity would weaken the established
model-blind boundary.

## Decision

Add an `independent-review-adjudication-v1` workflow after two ordinary first-pass review imports:

1. Require two content-addressed `ReviewedGroundedAnswerBatch` artifacts from different normalized
   reviewer identities. Both must be unadjudicated first passes over the exact same task hash,
   candidate-batch hash, rubric version, and complete judgment-row identity set.
2. Canonically order the source reviews by normalized reviewer identity before computing report
   identity, confusion matrices, or final provenance. Reversing CLI input order must not change the
   agreement artifact.
3. Measure exact agreement separately for `support_grade`, `citation_quality`,
   `answer_relevance`, and `abstention_appropriate`. For each observed dimension, retain its full
   confusion matrix, observed agreement, marginal expected agreement, and unweighted Cohen's
   kappa. Leave kappa undefined when expected agreement is one; do not fabricate metrics for a
   dimension with no observations.
4. Also report full-row exact agreement and count every disputed field. Rationale text alone does
   not create a grade disagreement, but both original rationales remain in the machine-readable
   agreement artifact.
5. Export only judgment rows containing at least one disputed field. Protect the exact task,
   answer, claim, citation, evidence, report, disputed-field, and input-review values during import.
6. Keep candidate, model, and reviewer identity out of the editable adjudication CSV. Present the
   two inputs only as review A and review B, and deterministically swap A/B order independently for
   each row to reduce positional anchoring while keeping the export reproducible.
7. Permit edits only to final columns named in that row's `disputed_fields`. A third named human,
   distinct from both reviewers after normalization and case folding, must provide one valid rubric
   value for every disputed field and a rationale of at least ten characters. The adjudicator may
   choose any valid rubric value, including a justified third value.
8. Inherit a fully agreed row without exposing it to adjudication. On a partially disputed row,
   preserve every agreed field exactly and merge only the adjudicated fields. A header-only sheet
   is valid when the two reviews agree on every row.
9. Emit a schema `1.1.0` reviewed batch containing final judgments and provenance with both
   reviewer identities, review IDs, review hashes, per-dimension agreement, agreement-report ID,
   adjudicator, timestamp, disputed-row count, and disputed-field count. Emit a separate decision
   report containing the before/after judgments for every disputed row.
10. Keep agreement, adjudication, final review, and score artifacts permanently marked `blocked`.
    Independent adjudication removes only the first-pass-review blocker. It does not establish a
    representative sample, target-hardware latency, approved threshold, regression policy, model
    release, answer endpoint, preflight authorization, or agent execution.

## Consequences

- Review consistency becomes inspectable for each rubric dimension instead of being collapsed
  across incompatible scales.
- Partial disagreements are localized. Consensus fields cannot be accidentally regraded during
  adjudication.
- Adjudicators can inspect both judgments and rationales without knowing which human or candidate
  produced them.
- Both first-pass reviews and every third-person decision remain traceable from the final review.
- Identical review inputs produce the same agreement ID and blinded row ordering, although report
  timestamps and final adjudication timestamps remain explicit metadata.
- Human agreement does not make public-source statements true. It measures how consistently the
  bounded excerpts support the candidate output under the declared rubric.
- The checked-in workflow can now process a live Qwen run, but the repository still contains no
  live candidate quality result or human review artifact.

## Rejected alternatives

- **Average the two grades.** Ordinal grade means can create values no human selected, conceal
  severe disagreements, and cannot resolve a boolean abstention decision.
- **Use one holistic agreement score.** Mixing four scales makes the result hard to interpret and
  can hide a weak but safety-relevant dimension behind stronger ones.
- **Adjudicate every field on every row.** That wastes review effort and allows agreed judgments to
  drift after agreement has already been established.
- **Show reviewer names in the adjudication sheet.** Identity can anchor a third reviewer toward a
  more senior or familiar assessor and is unnecessary for the rubric decision.
- **Keep one fixed A/B orientation.** A consistent left/right source can create positional
  anchoring across the batch. Per-row deterministic swapping reduces that cue without sacrificing
  reproducibility.
- **Require the final value to equal one submitted value.** A third reviewer may identify that both
  grades are inconsistent with the rubric. The decision remains bounded to the valid scale and
  requires a rationale.
- **Use a model judge or majority vote.** A two-reviewer disagreement has no majority, and an
  unvalidated model judge would add another artifact, prompt, and bias boundary while weakening
  human accountability.
- **Let adjudication update preflight.** Review completion is not a representative evaluation,
  approved release threshold, or explicit authorization.

# ADR 0036: Finalize retrieval relevance through independent review and adjudication

## Status

Accepted

## Context

ADR 0010 introduced rank-blind pooled relevance judgments, and ADR 0018 added exact judgment reuse
for longitudinal campaigns. One complete review is useful for internal failure discovery, but it
does not measure whether another person applies the `0..3` relevance rubric consistently. Treating
one review as public gold would hide ambiguous operational intent, individual mistakes, and grade
boundary drift.

The adjudication stage must preserve the existing retrieval blind. A third person may inspect the
captured query, evidence, and both human grades, but retrieval mode, rank, score, and reviewer
identity can anchor the final decision. Reviews from different captures, event revisions, model
identities, or ranking rules cannot be combined into one interpretable gold pool.

## Decision

Add an `independent-review-adjudication-v1` workflow after two ordinary `review` imports.

1. Regenerate the second rank-blind sheet from the original unjudged pool. Do not seed it from or
   expose the first review.
2. Require two complete schema `1.0.0` first-pass pools from different normalized reviewer
   identities. Reject an already adjudicated pool as an input review.
3. Hash the complete capture after removing only the artifact-envelope version, relevance grades,
   rationales, reviewer metadata, and adjudication metadata. Refuse the pair unless the hashes are
   identical. Retrieval runs, ordering, rule/model identity, filters, and reviewer-visible evidence
   remain inside the hash.
4. Canonically order review artifacts by normalized reviewer identity. Reversing command arguments
   cannot alter report identity, confusion-matrix orientation, or final provenance.
5. Measure exact observed agreement, expected marginal agreement, and unweighted Cohen's kappa
   overall and by query, source, and declared query slice. Retain the full `0..3` confusion matrix
   and every exact disagreement identity. Kappa is `null` when expected agreement is one.
6. Export only grade disagreements. Keep retrieval mode, rank, score, and reviewer identity out of
   the editable CSV. Present inputs only as review A and review B, swapping A/B deterministically
   per row to reduce positional anchoring.
7. Protect all evidence and input-review fields byte-for-byte. The terminal workflow permits only
   a final `0..3` grade and a rationale of 10–1000 characters, saves atomically, and resumes blanks.
8. Require a named adjudicator different from both first-pass reviewers. Matching grades inherit
   consensus and cannot be overwritten; only disagreements receive third-person decisions. A
   header-only sheet is valid when every grade agrees.
9. Emit a schema `1.1.0` final gold pool with both reviewed-pool hashes, agreement report identity,
   observed agreement, kappa, adjudicator, UTC timestamp, and decision count. Emit a separate
   content-addressed decision report retaining every disagreement and final rationale.
10. Score the final pool through the existing retrieval metrics while emitting report schema
    `1.2.0` with the adjudication provenance. Keep agreement and adjudication artifacts marked
    `blocked`; they do not establish a quality threshold or authorize production promotion.

## Consequences

- Reviewer consistency becomes measurable overall and within source and intent slices.
- Exact capture hashing prevents a superficially similar live snapshot from entering one review
  process.
- Consensus grades remain immutable, while adjudication effort is limited to real disagreements.
- Reviewer-blind, per-row A/B swapping reduces identity and position anchoring without sacrificing
  reproducibility.
- A unanimous single-grade review correctly records kappa as undefined rather than fabricating a
  chance-corrected score.
- The process remains deterministic, local, resumable, and free of model judges or paid services.
- Software can verify distinct names and exact artifacts, but cannot prove that people worked
  independently; that remains a procedural responsibility.
- Agreement does not make public-source evidence true or the 15-query live capture representative.

## Rejected alternatives

- **Promote one review as public gold.** It provides no evidence about rubric consistency.
- **Average two ordinal grades.** A mean can create a grade no person selected and conceal a severe
  disagreement.
- **Adjudicate every candidate.** It wastes effort and lets a third person alter genuine consensus.
- **Expose retrieval output during adjudication.** Mode, rank, or score would anchor the gold grade
  toward the system being evaluated.
- **Expose reviewer names.** Identity is unnecessary for the rubric decision and may introduce
  authority or familiarity bias.
- **Keep one fixed review A/B order.** Persistent left/right placement can create position bias.
- **Accept a first-pass reviewer as adjudicator.** That does not provide a distinct resolution of
  the disagreement.
- **Use a model judge.** An unvalidated judge would add model and prompt drift before the human
  relevance baseline itself is stable.

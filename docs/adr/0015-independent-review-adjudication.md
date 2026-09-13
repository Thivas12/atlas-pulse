# ADR 0015: Finalize relationship gold labels through independent review and adjudication

## Status

Accepted

## Context

The live claim-pair benchmark can import one complete prediction-blind review, which is enough for
internal error discovery. It is not enough for a public comparison or for promoting an NLI/LLM
proposal rule. One reviewer can apply the rubric inconsistently, and ordinary percent agreement
does not account for agreement expected from each reviewer's label distribution.

Adjudication must also preserve the existing grounding boundary. An adjudicator may inspect both
human decisions and their rationales, but seeing the deployed system label would anchor the final
gold decision to the system under evaluation. Combining reviews captured from different evidence,
rules, or API snapshots would make the resulting score uninterpretable.

## Decision

Add an `independent-review-adjudication-v1` workflow after two ordinary `review` imports.

1. Require both inputs to be complete, first-pass reviews from different normalized reviewer
   identities.
2. Content-hash the complete capture after removing only human labels, rationales, reviewer
   metadata, and the artifact-envelope version. Refuse reviews unless these hashes match exactly.
   Deployed predictions remain in the hash, so two reviews cannot silently target different rule
   outputs.
3. Canonically order the two review artifacts by reviewer identity. Command argument order cannot
   change report IDs, confusion-matrix orientation, or downstream adjudication columns.
4. Report observed agreement, expected marginal agreement, and Cohen's kappa overall and for each
   predicate and source pair. Serialize kappa as `null` when expected agreement is exactly one.
5. Preserve a complete reviewer-by-reviewer confusion matrix and exact disagreement case IDs.
   Agreement measures consistency, not correctness.
6. Export only disagreement rows for adjudication. Protect the original evidence, both reviewer
   identities, labels, rationales, and the agreement-report identity byte-for-byte. Keep all
   deployed labels, extracted claims, bases, and system rationales out of the CSV.
7. Require a named adjudicator who differs from both reviewers, one allowed final label, and a
   non-empty rationale for every disagreement. Matching independent labels pass through without
   adjudicator override.
8. Emit a schema `1.1.0` final gold pool containing the two reviewed-pool hashes, agreement report
   ID, observed agreement, kappa, adjudicator, UTC timestamp, and decision count. Also emit a
   separate content-addressed adjudication report containing every changed decision.
9. Allow the existing scorer to consume the final pool unchanged, while surfacing its independent
   review provenance and replacing the single-review caveat.

## Consequences

- Public or promotion-oriented evaluation can be tied to two exact independent reviews and an
  inspectable final decision record.
- Stable capture and review hashes prevent accidental mixing of live snapshots or system versions.
- Predicate and source-pair agreement slices expose rubric ambiguity hidden by an aggregate score.
- A unanimous but single-label review correctly reports Cohen's kappa as undefined rather than
  manufacturing perfect chance-corrected agreement.
- The workflow remains local, deterministic, and free of model judges or paid services.
- The software can validate artifact identity and distinct names, but it cannot prove that humans
  worked independently; that remains a documented procedural responsibility.

## Rejected alternatives

- **Treat one review as public gold.** This provides no estimate of rubric consistency and leaves
  individual mistakes unchallenged.
- **Adjudicate every row.** This wastes reviewer effort and lets a third person overwrite genuine
  independent consensus without evidence of a dispute.
- **Show the deployed prediction during adjudication.** It would anchor the final label to the
  system being evaluated.
- **Compare only percent agreement.** It hides agreement expected from heavily imbalanced label
  marginals; observed agreement and kappa are both required.
- **Accept the same reviewer twice.** Duplicate identity does not create independent evidence.
- **Merge reviews from similar captures.** Even one changed document, graph measurement, or system
  output creates a different evaluation artifact and fails closed.

# ADR 0043: Narrow grounded-answer abstention policy

## Status

Accepted.

## Context

The pinned `qwen3-1.7b-q8-grounded-brief-v2` candidate completed a ten-case live development task,
but selected the abstention branch for all ten cases. In the declared AI-assisted single review,
only two abstentions were judged appropriate. The resulting development report recorded 20%
appropriate abstention and a 20% strict case pass rate. It remains single-review development
evidence and cannot support a public quality claim or production promotion.

The v2 instruction grouped absent, insufficient, and materially conflicting evidence together and
did not distinguish genuinely unanswerable questions from questions that could receive a cautious,
partial, or bounded negative answer. The model, schema, and short-answer budget made the smaller
abstention branch particularly easy to select.

## Decision

Add `grounded-brief-qwen3-v3` as a new, immutable instruction-template version while preserving v2
for exact reproduction.

1. Keep the same pinned model bytes, llama.cpp boundary, context and output budgets, decoding
   parameters, evidence rendering, response schema, citation validation, and default-deny release
   policy.
2. Prefer an answered response whenever at least one excerpt directly supports a responsive,
   bounded claim.
3. Treat partial coverage, uncertainty, machine-coded or unverified evidence, missing impact
   details, and unrelated additional records as limitations to state rather than automatic reasons
   to abstain.
4. Allow a cited bounded finding that the retrieved records do not show a requested conflict,
   corroboration, or impact.
5. Reserve abstention for cases with no supported responsive claim or a material conflict that
   prevents every bounded answer.
6. Bind prompt, system, run, and checkpoint identities to the selected template version. Preserve
   the historical v2 prompt hash algorithm so existing evidence remains reproducible.
7. Keep every v3 run and review development-only and promotion-blocked. The v2 review is tuning
   evidence, so a v3 result on the same task is not an independent generalization estimate.

## Consequences

- The next run can measure whether the same small local model produces useful cited claims instead
  of defaulting to the shortest abstention response.
- v2 artifacts and checkpoints remain attributable to their original prompt contract.
- Any v3 improvement is descriptive evidence on a tuned development task, not a production quality
  claim.
- If v3 still over-abstains or begins producing unsupported claims, a new version and fresh
  development review are required; v3 is never mutated in place.

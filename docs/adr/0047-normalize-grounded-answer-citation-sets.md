# ADR 0047: Normalize grounded-answer citation sets

## Status

Accepted.

## Context

The v6 candidate completed and checkpointed the first three live cases. Its fourth structured
response then repeated the same valid task-local evidence ID within one claim, and the independent
response model correctly rejected the citation list because evidence IDs must be unique. The JSON
Schema already declares `uniqueItems: true`, but the pinned llama.cpp grammar does not enforce that
keyword during generation.

A repeated reference does not add evidence or alter which records support the claim. Retrying a
fixed-seed candidate can reproduce the same duplication, while weakening the shared response model
would permit ambiguous noncanonical artifacts from every producer.

## Decision

Add `grounded-brief-qwen3-v7` with the exact v6 messages, response schema, model, runtime, decoding
parameters, token limits, and claim-identifier normalization plus a version-bound citation-set
normalization.

1. Treat each claim's `evidence_ids` array as the set defined by the evaluation contract: remove
   repeated string values and sort the remaining values lexicographically before validation.
2. Preserve claim list order, claim text, and every distinct model-selected evidence ID.
3. Do not add, substitute, or repair an evidence ID. Empty, malformed, or foreign citation sets
   continue to fail the independent response and task-local import validators.
4. Retain v6's position-based normalization of already well-formed claim identifiers.
5. Record `canonicalize-claim-ids-and-evidence-sets-v1` in both the input-template identity and
   immutable candidate parameters.
6. Preserve v2 through v6 parsing and candidate identities without retroactive changes.
7. Use a fresh v7 checkpoint and output namespace. V6's three completed cases and later failure
   remain development evidence and are not rewritten or relabeled as v7 output.
8. Keep v7 development-only and promotion-blocked. Its behavior is informed by earlier task output
   and therefore cannot be presented as independent quality evidence.

## Consequences

- Repeated citations collapse to one canonical reference without changing support membership.
- The adapter compensates for a documented structured-generation limitation without weakening the
  evaluator's producer-independent uniqueness rule.
- The candidate definition makes claim-ID and citation-set normalization visible and reproducible.
- Successful completion still requires model-blind review for support, citation quality, relevance,
  and abstention appropriateness.

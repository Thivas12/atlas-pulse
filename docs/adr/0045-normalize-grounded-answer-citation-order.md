# ADR 0045: Normalize grounded-answer citation order

## Status

Accepted.

## Context

The completion-safe v4 candidate passed the failure point that blocked v3 and returned a structured
answer for the first live case. Validation then rejected the response because one claim contained
two task-local evidence IDs in noncanonical order. The selected citation set was valid and unique;
only its array order violated the content-addressed artifact contract.

Repeated inference is not an appropriate remedy because the pinned seed makes the ordering
deterministic. Dropping to one citation per claim would discard useful cross-source support, while
removing canonical ordering from the shared evidence model would weaken every producer and
historical artifact.

## Decision

Add `grounded-brief-qwen3-v5` with the exact v4 messages, response schema, model, runtime, decoding
parameters, and token limits plus one version-bound adapter normalization.

1. After valid JSON decoding and before response-model validation, sort each answered claim's
   `evidence_ids` array lexicographically.
2. Do not add, remove, deduplicate, substitute, or otherwise change an evidence ID. Duplicate,
   malformed, foreign, or structurally invalid citations continue to fail validation.
3. Record `sort-claim-evidence-ids-v1` in both the input-template identity and immutable candidate
   parameters.
4. Preserve v2 through v4 parsing and candidate identities without normalization.
5. Use a fresh v5 checkpoint and output namespace. V4 evidence remains a failed development
   attempt and is not rewritten.
6. Keep v5 development-only and promotion-blocked. Its behavior is informed by earlier task output
   and therefore cannot be presented as independent quality evidence.

## Consequences

- Semantically equivalent citation sets produce one canonical content-addressed representation.
- Cross-source claims may retain two citations without relying on a small model to perform lexical
  identifier sorting.
- The candidate definition makes the normalization visible and reproducible.
- Successful completion still requires model-blind review for support, citation quality, relevance,
  and abstention appropriateness.

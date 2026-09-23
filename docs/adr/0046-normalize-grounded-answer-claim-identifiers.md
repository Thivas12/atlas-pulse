# ADR 0046: Normalize grounded-answer claim identifiers

## Status

Accepted.

## Context

The v5 candidate passed the v4 citation-order failure and returned a structured answer for the
first live case. Validation then rejected the response because its syntactically valid `claim_id`
values were not consecutive and canonically ordered from `claim-01`. Claim IDs are local
presentation identifiers; the ordered claim array, claim text, and evidence references carry the
response semantics.

Repeating the pinned inference is not an appropriate remedy because the fixed configuration can
reproduce the same formatting choice. Reordering claims by their generated identifiers could alter
the model's intended answer order, while weakening the shared response model would permit
noncanonical artifacts from every producer.

## Decision

Add `grounded-brief-qwen3-v6` with the exact v5 messages, response schema, model, runtime, decoding
parameters, token limits, and citation-set normalization plus one version-bound adapter step.

1. Preserve the generated claim list order, text, and evidence-ID membership.
2. After valid JSON decoding and citation sorting, assign `claim-01`, `claim-02`, and so on by claim
   list position before response-model validation.
3. Apply identifier normalization only when every supplied claim is an object whose `claim_id`
   already matches the required `claim-NN` syntax. Missing, mistyped, or malformed identifiers
   continue to fail validation.
4. Record `canonicalize-claim-ids-and-evidence-order-v1` in both the input-template identity and
   immutable candidate parameters.
5. Preserve v2 through v5 parsing and candidate identities without retroactive changes.
6. Use a fresh v6 checkpoint and output namespace. V5 remains a failed development attempt and is
   not rewritten.
7. Keep v6 development-only and promotion-blocked. Its behavior is informed by earlier task output
   and therefore cannot be presented as independent quality evidence.

## Consequences

- Semantically identical ordered claim lists produce consecutive canonical identifiers.
- Canonicalization cannot change claim order, prose, citation membership, or abstention decisions.
- The candidate definition makes both citation-order and claim-identifier normalization visible
  and reproducible.
- Successful completion still requires model-blind review for support, citation quality, relevance,
  and abstention appropriateness.

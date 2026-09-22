# ADR 0044: Bound grounded-answer completion shape

## Status

Accepted.

## Context

The versioned v3 prompt correctly stopped treating partial evidence as an automatic reason to
abstain, but the pinned Qwen3 1.7B candidate did not complete the first live case. Six independent
process attempts with the same immutable configuration each reached `finish_reason='length'` after
exactly 512 output tokens. No case completed, so the checkpoint remained empty.

This is deterministic candidate behavior rather than a transient server failure. The v3 schema
allowed three 180-character claims with two long evidence identifiers per claim. That valid maximum
shape left too little completion headroom for this model and tokenizer. Raising the output ceiling
would repeat the earlier unbounded v1 failure mode without first reducing the requested response.

## Decision

Add `grounded-brief-qwen3-v4` as a new immutable prompt and response-contract version.

1. Preserve v2 and v3 prompts, schemas, hashes, configurations, and failed-run provenance.
2. Keep the same model bytes, llama.cpp runtime boundary, decoding parameters, context length,
   evidence rendering, citation limit, and 512-token output ceiling.
3. Reduce the maximum answer from three claims to two and each claim from 180 characters to 120.
4. Direct the candidate to prefer one claim and use the second only when another part of the
   question requires it.
5. Retain the narrow v3 abstention policy and require the JSON object to end immediately after the
   final claim.
6. Bind prompt hashes, response schemas, checkpoints, candidate definitions, and run traces to the
   selected template version. Existing v2 and v3 identities remain unchanged.
7. Keep v4 development-only and promotion-blocked. Its design uses observed v2 review labels and v3
   execution behavior, so results on the same task are tuning evidence rather than an independent
   quality estimate.

## Consequences

- A maximum v4 response is materially smaller while still permitting two-source citations and two
  distinct operator-relevant findings.
- A fresh v4 output and checkpoint namespace is required; v3 retries cannot be reused.
- If v4 still reaches the output ceiling, the failure is retained and the next version must change
  the runtime or schema explicitly rather than mutating v4.
- Completion only enables review. It does not establish support, relevance, production readiness,
  or permission to enable model or agent execution.

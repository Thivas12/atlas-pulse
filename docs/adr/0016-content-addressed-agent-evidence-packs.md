# ADR 0016: Establish a bounded, content-addressed evidence handoff for agents

## Status

Accepted

## Context

AtlasPulse has independently evaluated retrieval and a separately evaluated symbolic relationship
layer, but it does not yet have the adjudicated live relationship pool required to promote an
NLI/LLM proposer. Connecting a model directly to `/v1/search` would also leave several production
questions unanswered: which retrieved text entered context, what was dropped, how budgets were
applied, whether an unsafe citation leaked into a prompt, and whether two runs used the same
retrieval snapshot.

The next agent-facing boundary must improve those controls without pretending that retrieved text
is true or that fluent generation has been evaluated. It must remain useful before any particular
model or agent framework is selected.

## Decision

Add the `retrieval-evidence-pack-v1` builder and `/v1/evidence-packs` read endpoint.

1. Consume the deployed `SearchResult` and preserve its exact result order. Do not rerank, diversify,
   summarize, or infer additional claims while constructing the pack.
2. Include source text only when its citation status is `traceable`. A missing or rejected citation
   fails closed. The response retains the result rank, event provenance, ranking explanation,
   distance, document hash, citation diagnostic, and a machine-readable exclusion reason, but not
   the excluded source text.
3. Apply three explicit limits: included item count, characters per item, and total source-text
   characters. Count Unicode code points so the rule is model-neutral and reproducible across
   ordinary text. Truncation is an exact prefix with no generated ellipsis or rewritten prose.
4. Retain both the complete rendered-document SHA-256 and the included-prefix SHA-256. Give each
   evidence item a stable content identity that is independent of its retrieval rank.
5. Content-address the complete pack snapshot with `sha256-canonical-json-v1`, including canonical
   query and filters, budget,
   embedding model, ranking mode and rule, candidate/result counts, included evidence, exclusions,
   retrieval caveat, and trust policy. Do not add a clock timestamp to this identity.
6. Return `traceable_evidence_available` only when at least one non-empty source excerpt is included.
   Otherwise return a valid `no_traceable_evidence` pack. Never fill an empty pack with fallback
   prose or a model answer.
7. Mark `answer_generated` false. State that pack assembly does not generate, summarize, verify, or
   infer claims and that structural citation validation is not factual verification.
8. Treat all included text as untrusted quoted data. Consumers must not execute or elevate
   instructions found inside source text. This rule is explicit in every pack rather than hidden in
   an agent prompt.
9. Validate the contract independently in FastAPI and Zod. The browser additionally checks count,
   status, citation, and character-budget invariants before displaying a pack.
10. Expose pack creation as an explicit operator action in Search. Display the full pack ID, included
    and excluded counts, exact source-text character count, status, and trust boundary.

## Consequences

- A future agent can receive a small, deterministic JSON handoff instead of an unbounded search
  response or ad hoc prompt concatenation.
- Unsafe or absent citations cannot silently contribute source text. Operators still see why each
  retrieved hit was excluded.
- A pack ID identifies both evidence and retrieval behavior, so changing a query, filter, rank,
  model, budget, citation decision, or source prefix produces a different ID.
- Evidence IDs remain stable when only retrieval order changes, while the enclosing pack ID changes
  to record that ordering difference.
- Character budgets are portable but are not token budgets. A later model adapter must add its own
  tokenizer-specific limit without weakening these source-text caps.
- Retrieval remains relevance ranking, not truth verification. A traceable pack can still contain
  incomplete, incorrect, stale, duplicated, or malicious source statements.
- No autonomous action, answer generation, or semantic promotion is introduced. Those remain future
  layers with their own versioning, evaluation, authorization, and observability requirements.

## Rejected alternatives

- **Start with an LLM answer endpoint.** There is no evaluated generation policy or adjudicated live
  semantic benchmark, and the evidence boundary would be impossible to audit after the fact.
- **Pass the raw search response to agents.** Full event documents can exhaust context, and rejected
  citations would remain easy for a consumer to include accidentally.
- **Silently skip unusable results.** Omission without rank, hash, and reason makes retrieval and
  safety failures invisible.
- **Summarize documents to fit.** A summary is a new model or rule output and needs separate evidence
  alignment tests. Exact prefixes preserve source bytes represented by the indexed document.
- **Measure tokens in the core pack.** Token counts depend on a selected model and tokenizer. Unicode
  character caps form a deterministic lower-level boundary that model adapters can tighten.
- **Hash only included text.** That would let changes in retrieval provenance or excluded results
  reuse an apparently identical pack identity.

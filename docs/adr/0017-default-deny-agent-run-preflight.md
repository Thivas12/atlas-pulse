# ADR 0017: Gate future agent runs with a default-deny content-addressed preflight

## Status

Accepted

## Context

The evidence-pack boundary makes retrieved context deterministic, bounded, citation-safe, and
explicitly untrusted. It does not answer the next operational question: whether a model or agent
may consume that pack now. AtlasPulse still lacks an adjudicated live relationship benchmark, a
grounded-answer evaluation, an evaluated model adapter, and an explicit human release decision.

Calling a generative agent model before those conditions exist would turn an inspectable future
milestone into an unmeasured production claim. It would also leave no immutable record of the
requested capability, the exact pack, the policy snapshot, the failed gates, or whether any tools
and side effects were possible.

## Decision

Add the `agent-run-manifest-v1` builder and `GET /v1/agent-runs/preflight` endpoint.

1. Build a fresh `retrieval-evidence-pack-v1` snapshot through the deployed search path and return
   it beside the manifest. The manifest binds the exact `pack_id`, rule version, availability
   status, counts, source-text character use, and ordered evidence IDs.
2. Limit the first run intent to an `evidence_triage` specialist requesting a
   `grounded_evidence_brief` in read-only mode. Its capability request explicitly disables network
   access, tools, and external side effects.
3. Evaluate eight ordered checks: pack integrity, traceable evidence, capability scope, evaluated
   model adapter, adjudicated live relationship benchmark, grounded-answer evaluation, explicit
   human release, and the execution release switch.
4. Use the versioned `agent-authorization-v1` policy with default decision `deny`. Execution stays
   disabled and human release remains mandatory.
5. Return a machine-readable reason for every blocked check. Missing traceable evidence adds a
   separate block; it never replaces or hides the other unmet release gates.
6. Keep the v1 decision `blocked`. The current repository does not contain evidence that could
   justify an authorized branch.
7. Record proposed-agent execution state explicitly: `agent_model_invoked`,
   `agent_network_accessed`, `agent_tools_invoked`, `answer_generated`, and
   `agent_side_effects_performed` are all false, while status is `not_started`. This does not
   relabel the local BGE embedding used during ordinary retrieval as an agent model.
8. Content-address the complete manifest except its own ID using `sha256-canonical-json-v1`. Do not
   include a request timestamp, random identifier, or mutable runtime field.
9. Validate count, reason, check, no-execution, and pack-binding invariants independently in
   FastAPI and Zod before displaying the preflight.
10. Present preflight as an explicit second operator action after evidence-pack preparation. Show
    the complete manifest ID, passed and blocked counts, all block reasons, and expandable checks.

## Consequences

- AtlasPulse can demonstrate governed agent architecture without pretending an agent is safe or
  accurate before the required evaluation work exists.
- A manifest changes when the bound pack, requested capability, policy, check observations, or
  decision changes.
- The returned pack and manifest form a verifiable identity chain: first verify `pack_id`, then
  verify the manifest that references it.
- An empty pack remains auditable and adds `no_traceable_evidence` while every unrelated release
  gate is still reported.
- Preflight is an immutable value object, not a durable execution ledger. Persistence, signatures,
  actor identity, approval expiry, revocation, and append-only run events require a later ADR.
- The endpoint repeats live retrieval to produce one internally consistent pack-manifest pair. Two
  calls can legitimately differ when current indexed evidence changes.
- No model package, paid service, agent framework, generated output, or external action is added.

## Rejected alternatives

- **Authorize a read-only LLM because tools are disabled.** Generated statements can still be
  unsupported or misleading; tool isolation does not replace semantic evaluation.
- **Return only a Boolean.** It hides which evidence, policy version, and gates produced the
  decision and cannot support meaningful audit or regression tests.
- **Accept a client-supplied `pack_id` without the pack.** The server could not prove that the ID
  exists or that the operator saw the same current evidence.
- **Persist a fake run record.** No run occurred. A durable ledger should record real actor,
  approval, execution, and revocation events once those concepts exist.
- **Add a configurable bypass flag.** A convenience override would make the evaluation and human
  gates advisory instead of enforceable.
- **Mark synthetic contract tests as a passed live benchmark.** Frozen cases protect rule behavior;
  they do not measure representative live accuracy.

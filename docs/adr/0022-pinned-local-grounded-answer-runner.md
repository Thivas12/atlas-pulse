# ADR 0022: Run one pinned local grounded-answer candidate outside human review

## Status

Accepted

## Context

ADR 0021 created a gold-free task, strict candidate import, and model-blind review workflow, but
deliberately left generation external. Reproducible evidence now needs one concrete local candidate
whose model bytes, prompt, runtime, decoding parameters, token use, and outputs are auditable.

A general chat model can follow instructions embedded in source text, cite evidence from another
case, continue past its context window, or emit hidden reasoning alongside an apparently valid
answer. A floating model revision or unrecorded runtime binary would also make later review
impossible to reproduce. Giving the runner a review sheet or scored report would break the
gold-free boundary.

## Decision

Add separate `atlas-pulse-cache-grounded-answer-model` and
`atlas-pulse-run-grounded-answer` executables with these boundaries:

1. Select the official Apache-2.0 `Qwen/Qwen3-1.7B-GGUF` repository at exact revision
   `90862c4b9d2787eaed51d12237eafdfe7c5f6077`. Run only
   `Qwen3-1.7B-Q8_0.gguf`, whose required SHA-256 is
   `061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a`.
2. Keep the model selection, artifact hash, prompt/template version, 8,192-token context,
   768-token output budget, decoding values, seed, and CPU thread count in one checked-in strict
   candidate configuration. Any changed choice requires a distinct candidate identity.
3. Separate online provisioning from inference. The cache command requests only the declared file
   at the declared revision and independently verifies the exact local bytes. The runner accepts a
   local model directory and performs no model discovery or download.
4. Accept only a content-addressed gold-free `GroundedAnswerTask`. Do not accept a review CSV,
   reviewed batch, rationale, grade, score report, production API, or database destination.
5. Treat each evidence excerpt as untrusted quoted JSON data. Bind each prompt hash to the exact
   task case and a case-local output schema whose citation enum contains only that case's evidence
   IDs. A no-evidence case can return only the matching abstention.
6. Start an owned `llama-server` child on a random `127.0.0.1` port with a random API key, offline
   mode, CPU-only execution, no agent, no tools, no Web UI, no prompt cache, localhost-only CORS,
   and thinking disabled in both server and request settings. Pass a minimal environment so ambient
   proxy and `LLAMA_*` options cannot alter the boundary.
7. Hash the exact `llama-server` executable and record both that digest and its normalized
   `--version` output. Reject an absent or non-executable runtime and a missing, symlinked,
   out-of-directory, or hash-mismatched model artifact.
8. Ask the server's chat input-token endpoint to render and count the exact request before
   generation. Refuse the case when input plus the full output budget exceeds context. Require the
   completion usage to agree with preflight and remain within the declared output maximum.
9. Require exactly one clean, non-streaming, schema-constrained JSON completion with no emitted
   reasoning. Parse it through the independent ADR 0021 contracts, which recheck complete case
   identity, task-local citations, no-evidence behavior, and token limits.
10. Emit a completed submission, immutable candidate definition, and content-addressed run trace.
    Keep the trace and the independently accepted candidate batch permanently `blocked`; this
    runner cannot expose a production answer, update preflight policy, or execute an agent.

## Consequences

- AtlasPulse has one inspectable, fully local baseline for grounded-answer review without a paid
  inference or judge API.
- The model file is about 1.8 GB and must be provisioned once; it is intentionally not committed to
  Git or downloaded during a candidate run.
- Exact prompt and runtime identities make a later reproduction comparable, but hardware,
  operating system, and llama.cpp build differences can still change latency or output.
- JSON grammar and citation membership prevent malformed structure and foreign citations. They do
  not prove that a claim is semantically supported, complete, current, or true.
- Qwen3's upstream capabilities and license do not establish AtlasPulse quality. The checked-in
  ten-case live task still needs execution, model-blind human review, independent second review,
  disagreement adjudication, and an approved release policy.
- No grounded-answer result satisfies `agent-authorization-v1`; the production and agent execution
  boundaries remain unchanged.

## Rejected alternatives

- **Use a hosted chat API.** It adds cost, source-text disclosure, credentials, remote model drift,
  and an unnecessary external execution dependency.
- **Download a floating model during inference.** A branch or tag can move, and network behavior
  would become part of every supposedly offline run.
- **Trust a filename or repository name.** Neither identifies the exact GGUF bytes actually
  executed.
- **Let llama.cpp inherit environment defaults.** Proxy variables and `LLAMA_*` options can silently
  change network or tool behavior.
- **Count tokens after generation.** An oversized case could already have been truncated or failed
  before the measurement was recorded.
- **Use unconstrained prose and repair it afterward.** Repair obscures what the model emitted and
  can introduce claims or citations that were never generated.
- **Pass review artifacts to one all-purpose command.** That weakens gold isolation and makes
  accidental tuning against human judgments easier.
- **Wire the candidate into `/v1/evidence-packs` or preflight.** Evaluation code is not an approved
  production adapter or release decision.

# Governed agent-run preflight

`GET /v1/agent-runs/preflight` prepares one internally consistent evidence-pack and run-manifest
pair. It answers “what would block this future run?” without starting the proposed generative
agent or executing any of its requested capabilities.

## Identity chain

| Artifact | Identity | Bound content |
| --- | --- | --- |
| Evidence pack | `pack-<sha256>` | Query, filters, ranking, budgets, admitted text, exclusions, and trust policy |
| Run manifest | `manifest-<sha256>` | Exact pack reference, run intent, capability request, policy snapshot, checks, decision, and execution state |

Both declare `sha256-canonical-json-v1`: sorted JSON keys, compact separators, UTF-8, explicit
nulls, and ISO-8601 datetimes. The manifest identity excludes only `manifest_id`. It references the
pack identity rather than duplicating its source text; verify the returned `pack_id` first, then
the manifest.

Preflight responses contain no timestamp. The same live retrieval snapshot and parameters produce
the same pair, while a changed indexed event, ranking, citation decision, budget, or policy result
produces a different identity.

## Locked v1 request

| Field | Value | Boundary |
| --- | --- | --- |
| Purpose | `evidence_triage` | First future specialist only |
| Mode | `read_only` | No external state mutation |
| Requested output | `grounded_evidence_brief` | Proposed output; never generated in v1 |
| Evidence read | Enabled | Restricted to the returned bounded pack |
| Text generation | Requested | Blocked until evaluation and release gates pass |
| Network, tools, side effects | Disabled | Not available to this profile |

## Authorization checks

The versioned `agent-authorization-v1` policy is default-deny and evaluates all checks every time.
One failure cannot hide another.

| Check | Current observation | Required state |
| --- | --- | --- |
| Evidence-pack integrity | Content-addressed and bounded | Content-addressed and bounded |
| Traceable evidence | Depends on live pack | At least one admitted item |
| Capability scope | Read-only; network/tools/side effects off | Same restricted scope |
| Model adapter | Not selected | Evaluated model adapter |
| Live relationship benchmark | Awaiting independent adjudication | Adjudicated pass |
| Grounded-answer evaluation | First-pass harness available; no reviewed representative pass | Evaluated pass |
| Human release | Not granted | Explicit human approval |
| Execution release | Disabled | Enabled |

The first three checks can pass today when evidence exists. The remaining five keep the proposed
run blocked. `agent-authorization-v1` still records the grounded-answer observation as unavailable;
the new harness is deliberately not wired into authorization. An empty pack also adds
`no_traceable_evidence`.

## Inspect a preflight

```bash
curl -fsS --get 'http://localhost:8000/v1/agent-runs/preflight' \
  --data-urlencode 'q=residents ordered to shelter from a dangerous storm' \
  --data-urlencode 'bbox=-125,24,-66,50' \
  --data-urlencode 'retrieval_limit=20' \
  --data-urlencode 'candidate_limit=100' \
  --data-urlencode 'max_items=8' \
  --data-urlencode 'max_characters_per_item=2000' \
  --data-urlencode 'max_total_characters=12000' \
  | jq '{pack: (.evidence_pack | {pack_id, status, item_count, exclusion_count}),
         manifest: (.manifest | {manifest_id, status, request, evidence, policy,
           authorization, execution, caveat})}'
```

The endpoint performs retrieval and deterministic policy evaluation only. Ordinary retrieval still
uses the local BGE embedding model; `agent_model_invoked: false` refers specifically to the proposed
generative agent run. The execution object must remain exactly `not_started` with every agent-action
flag false. A consumer must never interpret the presence of a manifest as authorization.

## What this milestone does not claim

- The manifest is not a model evaluation, safety certification, human approval, or signed token.
- `traceable_evidence_available` is structural grounding, not factual verification.
- Content addressing detects changed content; it does not prove who created or approved it.
- Preflight is returned to the caller but is not yet persisted in an append-only audit store.

The evidence contract is documented in [`evidence-packs.md`](evidence-packs.md). The decision and
rejected alternatives are recorded in
[`ADR 0017`](adr/0017-default-deny-agent-run-preflight.md). The separate non-promoting evaluator is
documented in the
[grounded-answer workflow](../evals/grounded-answers/README.md) and
[`ADR 0021`](adr/0021-gold-free-grounded-answer-evaluation.md).

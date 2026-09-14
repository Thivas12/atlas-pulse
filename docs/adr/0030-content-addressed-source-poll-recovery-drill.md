# ADR 0030: Bind source-poll recovery drills without injecting faults

- Status: accepted
- Date: 2026-09-14

## Context

ADR 0029 exposes a bounded source-poll transition history but deliberately does not infer recovery
from a later success. The history is worker-written state from the deployment under test, older
entries can expire, and a natural sequence of failure then success does not establish the scope or
cause of an incident.

The operational campaign can bind an operator-run service restart to before and after probes, but
it does not prove that one required source became observably degraded, that readiness stayed
available during that degradation, or that the next explicitly identified attempt returned the
source to a current state. A safe chaos/freshness drill needs those links without giving the
evidence CLI authority to change networking, containers, source configuration, or production data.

## Decision

AtlasPulse will add a standalone `source-poll-recovery-drill-v1` artifact and a
`record-source-recovery` command.

1. The operator performs and clears a separately reviewed fault, then supplies its UTC window,
   required source, expected bounded failure code, and exact failed and successful attempt IDs in a
   strict JSON submission. The submission asserts that a fault was injected and that no production
   data was deleted.
2. The recorder opens only bounded, stable, no-follow regular JSON files. It binds the exact
   deployment target, a passing pre-fault probe, a probe captured while the source was degraded, a
   passing post-clearance probe, and one strictly validated `/v1/source-polls` response.
3. Both selected attempts must have exactly one retained start and one matching terminal
   transition. Their stream IDs and timestamps must follow this order: pre-fault probe, declared
   fault start, failed attempt, degraded probe, declared fault clearance, successful attempt,
   passing probe, then history capture.
4. A passing artifact additionally requires the target source to move from healthy/current to an
   exact degraded failure and back to healthy/current. Every other required source must remain
   passing in the degraded probe. Health, readiness, events, and default-deny preflight must remain
   passing; only the source-freshness endpoint may fail, using its existing bounded semantic
   failure.
5. Full probe SHA-256 values, the canonical history SHA-256, four stream IDs, attempt IDs, fault
   code/stage, times, and derived durations become part of one content-addressed evidence identity.
6. The command never injects or clears a fault, restarts a service, reads logs or credentials,
   deletes data, grants approval, invokes an agent, or enables execution. These boundaries and the
   operator-asserted nature of fault causality remain embedded in every artifact.
7. The artifact is diagnostic drill evidence. It does not count toward the sampled 30-day campaign
   or replace that campaign's real restart-recovery requirement.

## Consequences

- Reviewers can verify an explicitly scoped failure-to-recovery sequence against exact public
  observations without treating the dashboard timeline as an inferred recovery claim.
- A failed expected-code or state check still produces a content-addressed failing artifact when
  all selected transitions are present. Missing or ambiguous transition pairs fail closed because
  there is no exact sequence to bind.
- The operator must retain all three probe files, the history response, and the submission beside
  the derived artifact if another reviewer needs to recompute it.
- A passing result remains one bounded observation, not continuous monitoring, a causal
  attestation, an SLA, or proof of upstream completeness.

## Rejected alternatives

- Automatically block source traffic or restart the ingestor from the evidence CLI. That would
  combine read-only evidence capture with production mutation and materially expand its authority.
- Mark any success newer than a failure as recovered. That would repeat the unsupported inference
  ADR 0029 explicitly rejects.
- Count the drill as a passing sampled campaign probe. The degraded probe must fail freshness by
  design, and the same deployment writes both the poll ledger and the public response.
- Store raw source responses, worker exceptions, URLs, or container logs in the drill. The bounded
  attempt contract already provides the necessary stage, code, timing, and counters without those
  disclosure risks.

# ADR 0026: Content-addressed sampled operational evidence

## Status

Accepted

## Context

AtlasPulse has a resource-capped HTTPS deployment profile, but a deployment runbook is not runtime
evidence. A single successful curl cannot support availability, recovery, durability, certificate,
or capacity claims. The roadmap requires at least 30 days of observed behavior before an
evidence-backed pilot statement.

The public API previously exposed only a semantic version. Two images built from different commits
could therefore produce indistinguishable health responses. Operations tooling also had no strict
artifact identity, so a restart or restore note could silently refer to a different target, probe,
or backup.

## Decision

1. Inject `ATLAS_BUILD_COMMIT_SHA` as Python-image build metadata and an OCI revision label, then
   expose it through both liveness and readiness. Public deployment tooling requires a full
   lowercase 40-character commit; local development may report `unknown`. Treat the value as an
   operator assertion, not independent build attestation.
2. Define one immutable deployment target from HTTPS origin, exact commit, version, environment,
   deployment time, required sources/services, and Compose files. Execution is always false.
3. Capture four fixed public endpoints with no redirects, a 1 MiB response bound, typed semantic
   checks, response-body hashes, verified TLS leaf metadata, and minimal source visibility.
4. Require the preflight response to remain blocked, release evidence to remain absent, all eleven
   checks to be present, execution to remain `not_started`, and every execution boolean to be false.
5. Capture Compose state/stats and host resources without reading logs, Docker inspection output,
   or container environment values.
6. Hash only stable, non-symlink, regular PostgreSQL custom-format backups. Record storage and
   encryption as explicit operator assertions.
7. Bind restart evidence to exact before/after probes. Bind restore evidence to an exact backup
   digest, reviewed commit, disposable environment, and five canonical checks. The CLI records
   drills but never performs them.
8. Recompute a sampled 30-day report from all exact evidence. Require a 30-consecutive-UTC-date
   streak with aligned passing probe, TLS, and resource observations, configured source visibility,
   a passing restart, an encrypted off-host backup, and a passing isolated restore of that class.
9. Call the strongest result `minimum_observation_set_complete`, label the success rate as sampled,
   and carry non-SLA, non-freshness, non-capacity, non-independent-audit, and no-execution caveats in
   every report.

## Consequences

- A monitor can detect the wrong deployed commit instead of accepting a matching version string.
- Edited, substituted, cross-target, missing-parent, chronologically impossible, symlinked, or
  oversized evidence fails closed.
- Content addresses detect mutation but do not authenticate the operator or collector. Host,
  clock, DNS, Docker, storage-scope, and encryption assertions remain explicit trust boundaries.
- Failure probes remain useful evidence and are written before the CLI returns status 1.
- Operators must still provision a public host, protect Docker authority, schedule collection,
  move encrypted backups off-host, and run real isolated restore drills.
- Event visibility is not poll-heartbeat evidence. A later milestone must add explicit ingestion
  attempt/outcome freshness before AtlasPulse can claim per-source polling reliability.
- This decision does not create an SLA, deploy an agent, enable execution, or weaken any release
  gate.

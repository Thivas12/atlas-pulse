# Sampled operational evidence campaigns

AtlasPulse v0.12 records deployment observations before making a public reliability statement. The
workflow is content-addressed, exact-commit-bound, and deliberately conservative. It does not run
a model or agent, change an approval, restart a service, create a backup, restore data, or claim an
SLA. Its source-poll drill recorder also does not inject or clear faults; it only validates
operator-supplied artifacts.

The command examples below use the direct-host `free-tier` overlay. A card-free workstation
deployment must use the exact substitutions in
[`workstation-funnel-deployment.md`](workstation-funnel-deployment.md): the
`deploy/workstation-funnel/compose.yaml` overlay, `workstation-funnel-public` environment, and its
Tailscale `*.ts.net` origin. The immutable target records the selected Compose files, so resource,
restart, backup, and restore evidence cannot silently switch profiles later.

## Claim boundary

The strongest status produced by this workflow is `minimum_observation_set_complete`. It means
only that the checked-in v1 minimums are represented:

1. evidence spans at least 30 calendar days;
2. 30 consecutive UTC dates contain a passing public probe;
3. 30 consecutive UTC dates contain verified TLS evidence;
4. those same 30 consecutive UTC dates contain a passing resource snapshot;
5. every required source passed both its worker-poll and upstream-data freshness checks on 30
   consecutive UTC dates;
6. one restart-recovery drill passed;
7. one encrypted off-host backup was observed; and
8. that same class of backup passed an isolated restore drill.

The probe success rate is a rate over collected samples, not continuous availability between the
daily observations. `/v1/source-freshness` reports worker poll state separately from upstream
source timestamp age. Public event ages still show only what was visible in the bounded
`/v1/events` response; unchanged source responses may be deduplicated without making a successful
poll disappear. `/v1/source-polls` provides bounded retained transition diagnostics, but it is not
an independent observation and does not count toward campaign completeness, probe success, or a
recovery-drill result. These limitations remain printed in every JSON and Markdown report.

## Threat model and trust boundaries

The workflow is designed to catch malformed inputs, accidental edits, wrong-target substitution,
missing parent evidence, impossible chronology, non-public origins, redirects, oversized HTTP
responses, symlinked JSON/backup inputs, and files that change while being read. Content identities
provide tamper detection, not authorship: they are not signatures, transparency-log entries, or
independent attestations.

The collector host, deployment operator, system clock, DNS resolver, Docker daemon, and statements
that a backup is encrypted or off-host remain trusted inputs. Docker access is effectively root
authority. Public HTTP uses fixed paths, verified hostname TLS, no redirects or proxy environment,
and a preflight global-address check, but this is not a third-party availability monitor. Evidence
retains response digests and bounded typed fields rather than source text, logs, container
environments, credentials, or agent output. Review artifacts before publishing the evidence
directory because origins, service names, timings, and backup file names are still operational
metadata.

## 1. Bind the deployed commit

The Python image stores the supplied revision in its OCI label and runtime metadata; the public API
exposes that `commit_sha` in both `/healthz` and `/readyz`. Before starting the reviewed deployment,
set the exact checked-out commit:

```bash
export ATLAS_BUILD_COMMIT_SHA="$(git rev-parse HEAD)"
test "${#ATLAS_BUILD_COMMIT_SHA}" -eq 40
docker compose -f compose.yaml -f deploy/free-tier/compose.yaml up -d --build --wait
```

Do not use `main`, `latest`, a shortened SHA, or a value copied from a different checkout. The
public probe fails if either endpoint reports a different commit or application version. This is
operator-supplied build metadata, not an independently reproducible-build or hardware attestation.

Create one immutable target after HTTPS is reachable:

```bash
mkdir -p artifacts/operations/evidence

uv run atlas-pulse-operations target \
  --origin "https://$ATLAS_PUBLIC_HOST" \
  --commit-sha "$ATLAS_BUILD_COMMIT_SHA" \
  --application-version 0.12.0 \
  --environment free-tier-public \
  --deployed-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --output artifacts/operations/target.json
```

By default, `gdelt` and `usgs` are the required freshness sources because they emit continuously.
NWS can legitimately have no active alert, and FIRMS is opt-in. Add a source only when the deployed
configuration and reviewed source-age threshold make its continuous freshness meaningful.

## 2. Capture public and resource evidence

One public probe performs only five fixed, same-origin GET requests:

- `/api/healthz`;
- `/api/readyz`;
- `/api/v1/source-freshness`;
- `/api/v1/events?limit=500`; and
- `/api/v1/agent-runs/preflight?q=operational%20boundary`.

It follows no redirect, accepts at most 1 MiB per response, verifies the hostname and public TLS
certificate, records response hashes rather than source text, and requires the exact eleven-check
default-deny execution state.

The freshness endpoint is parsed through the same strict public API contract. `poll_status`
measures the latest worker attempt against the configured cadence, while `source_data_status`
compares the last successful source timestamp against a source-specific age bound. A recent failed
poll is `degraded`; an overdue heartbeat is `stale`; missing first-success metadata is
`not_reported`; and negative ages fail as clock skew. Raw bodies, request URLs, credentials, and
exception messages never enter the poll ledger.

The dashboard renders the same two dimensions through an independently strict browser contract.
It shows `FRESHNESS UNKNOWN` when that contract or request fails and does not substitute event
recency. Its recovery timeline separately renders the bounded `/v1/source-polls` history and fails
closed when that contract is unavailable or malformed. Neither panel is an independent monitor;
only the content-addressed artifacts below count toward the sampled campaign.

```bash
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
uv run atlas-pulse-operations probe \
  --target artifacts/operations/target.json \
  --output "artifacts/operations/evidence/${stamp}-probe.evidence.json"
```

The command still writes an artifact when the probe fails and exits with status 1. Configuration,
schema, or filesystem failures exit with status 2.

Every command that records a boolean pass result follows the same convention: write the failing
artifact first, then return status 1. Backup recording has no pass claim and returns 0 after a valid
digest artifact is written.

Capture the host plus Compose service snapshot separately:

```bash
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
uv run atlas-pulse-operations capture-resource \
  --target artifacts/operations/target.json \
  --project-dir "$PWD" \
  --output "artifacts/operations/evidence/${stamp}-resource.evidence.json"
```

The collector calls only `docker compose ... ps --format json` and `docker stats --no-stream`. It
does not call `docker inspect`, read container logs, or retain environment values. Docker authority
is effectively root-equivalent; restrict access to the deployment account and evidence directory.

Run both commands at a fixed cadence using the host's timer facility. Keep failed samples rather
than deleting them. The repository intentionally does not pretend that one daily sample is an SLA;
choose a more frequent cadence when the host can sustain it.

## 3. Bind a restart drill

The CLI never restarts production. Capture a passing probe, perform an operator-reviewed restart,
wait for Compose health, and capture the second probe. Then bind those exact IDs:

```bash
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose -f compose.yaml -f deploy/free-tier/compose.yaml restart api web
docker compose -f compose.yaml -f deploy/free-tier/compose.yaml up -d --wait

uv run atlas-pulse-operations record-restart \
  --target artifacts/operations/target.json \
  --before-probe artifacts/operations/evidence/BEFORE-probe.evidence.json \
  --after-probe artifacts/operations/evidence/AFTER-probe.evidence.json \
  --restart-started-at "$started_at" \
  --trigger planned_stack_restart \
  --service api \
  --service web \
  --output artifacts/operations/evidence/restart.evidence.json
```

The record passes only when both exact probes passed. It reports the observed recovery interval but
does not impose or claim an SLO.

## 4. Bind a source-poll failure/recovery drill

Run this optional diagnostic drill only under a separately reviewed change window. The operations
CLI never changes source configuration, networking, or containers. Capture a passing probe, inject
a bounded fault using your approved operator procedure, wait for the selected required source to
record a terminal failure, and capture a second probe before clearing the fault. That failure probe
is expected to exit with status 1 because source freshness is degraded while readiness remains up.
After clearing the fault, wait for the next successful attempt and capture a passing third probe.
Finally, save a history page that contains both exact start/terminal pairs:

```bash
mkdir -p artifacts/operations/drills

uv run atlas-pulse-operations probe \
  --target artifacts/operations/target.json \
  --output artifacts/operations/drills/before-probe.json

# Inject the operator-reviewed fault outside this CLI and record its UTC start time.

uv run atlas-pulse-operations probe \
  --target artifacts/operations/target.json \
  --output artifacts/operations/drills/failure-probe.json
test "$?" -eq 1

# Clear the fault outside this CLI, record its UTC clearance time, and wait for a new success.

uv run atlas-pulse-operations probe \
  --target artifacts/operations/target.json \
  --output artifacts/operations/drills/recovery-probe.json

curl --fail --silent --show-error \
  "https://$ATLAS_PUBLIC_HOST/api/v1/source-polls?limit=100" \
  --output artifacts/operations/drills/source-polls.json
```

Identify the failed and successful attempt IDs in that retained page. Create a strict submission;
the example failure code must match the fault you expected to observe:

```json
{
  "schema_version": "1.0.0",
  "source": "usgs",
  "fault_started_at": "2026-09-15T11:00:00Z",
  "fault_cleared_at": "2026-09-15T11:03:00Z",
  "failure_attempt_id": "source-poll-REPLACE_WITH_32_LOWERCASE_HEX_CHARACTERS",
  "recovery_attempt_id": "source-poll-REPLACE_WITH_32_LOWERCASE_HEX_CHARACTERS",
  "expected_failure_code": "transport_exhausted",
  "operator_fault_injected": true,
  "production_data_deleted": false,
  "execution_enabled": false
}
```

Bind the exact artifacts without replaying the fault:

```bash
uv run atlas-pulse-operations record-source-recovery \
  --target artifacts/operations/target.json \
  --before-probe artifacts/operations/drills/before-probe.json \
  --failure-probe artifacts/operations/drills/failure-probe.json \
  --recovery-probe artifacts/operations/drills/recovery-probe.json \
  --history artifacts/operations/drills/source-polls.json \
  --submission artifacts/operations/drills/source-recovery-submission.json \
  --output artifacts/operations/drills/source-recovery.json
```

A passing record requires the selected source to be healthy/current before the fault, degraded with
the exact failed attempt while the fault remains active, and healthy/current on the exact selected
success after clearance. Every other required source, readiness, the other public endpoints, and
the default-deny execution boundary must stay passing. The artifact binds the full probe hashes,
canonical history hash, four stream IDs, attempt IDs, and chronology.

This is operator-scoped diagnostic evidence, not independent monitoring or proof of causality. It
does not count toward the 30-day campaign or its restart requirement, so keep it outside the
campaign's `evidence/` directory.

## 5. Bind backup and isolated restore evidence

Create a PostgreSQL custom-format dump using the deployment runbook, copy it to operator-controlled
off-host storage, and apply an independently reviewed encryption/retention policy. The evidence
command reads the file with no-follow semantics, verifies the `PGDMP` format marker, checks that the
regular file stayed unchanged while hashing, and never modifies it:

```bash
uv run atlas-pulse-operations record-backup \
  --target artifacts/operations/target.json \
  --backup-file backups/atlas-REPLACE.dump \
  --storage-scope off_host_verified \
  --encrypted-at-rest \
  --output artifacts/operations/evidence/backup.evidence.json
```

Restore only into a disposable environment. Never point a drill at production. After completing
the checks, create a strict submission in this canonical order:

```json
{
  "schema_version": "1.1.0",
  "environment_id": "disposable-restore-01",
  "started_at": "2026-09-15T10:00:00Z",
  "completed_at": "2026-09-15T10:08:30Z",
  "restored_commit_sha": "REPLACE_WITH_THE_40_CHARACTER_TARGET_COMMIT",
  "production_data_overwritten": false,
  "checks": [
    {"name": "agent_default_deny", "passed": true, "observed": "11 checks; execution not_started"},
    {"name": "backup_digest_verified", "passed": true, "observed": "SHA-256 matched backup evidence"},
    {"name": "database_readable", "passed": true, "observed": "restored database query completed"},
    {"name": "migration_head_matches", "passed": true, "observed": "Alembic head matched reviewed commit"},
    {"name": "readiness_passed", "passed": true, "observed": "isolated /readyz returned ready"}
  ]
}
```

Bind it to the exact backup:

```bash
uv run atlas-pulse-operations record-restore \
  --target artifacts/operations/target.json \
  --backup-evidence artifacts/operations/evidence/backup.evidence.json \
  --submission artifacts/operations/restore-submission.json \
  --output artifacts/operations/evidence/restore.evidence.json
```

The campaign counts a restore toward completion only when it passed and references an encrypted
off-host backup in the same exact-target evidence set.

## 6. Recompute the report

Evidence files discovered from a directory must end in `.evidence.json`. The report command loads
each file with no-follow, regular-file, stable-read, and 5 MiB bounds; verifies every content ID;
cross-validates target, probe, per-source freshness, restart, backup, restore, time, and commit
links; then recomputes all eight gates.

```bash
uv run atlas-pulse-operations report \
  --target artifacts/operations/target.json \
  --evidence-dir artifacts/operations/evidence \
  --output-json artifacts/operations/report.json \
  --output-markdown artifacts/operations/report.md
```

Keep the raw evidence, target, report, the Git commit used to run the collector, and an off-host
copy of the evidence directory. `artifacts/` remains ignored so real operational records and any
environment-specific details are not silently committed to the public repository.

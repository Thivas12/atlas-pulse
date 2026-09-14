# Sampled operational evidence campaigns

AtlasPulse v0.9 records deployment observations before making a public reliability statement. The
workflow is content-addressed, exact-commit-bound, and deliberately conservative. It does not run
a model or agent, change an approval, restart a service, create a backup, restore data, or claim an
SLA.

## Claim boundary

The strongest status produced by this workflow is `minimum_observation_set_complete`. It means
only that the checked-in v1 minimums are represented:

1. evidence spans at least 30 calendar days;
2. 30 consecutive UTC dates contain a passing public probe;
3. 30 consecutive UTC dates contain verified TLS evidence;
4. those same 30 consecutive UTC dates contain a passing resource snapshot;
5. every configured continuously emitting source was visible at least once;
6. one restart-recovery drill passed;
7. one encrypted off-host backup was observed; and
8. that same class of backup passed an isolated restore drill.

The probe success rate is a rate over collected samples, not continuous availability between the
daily observations. Public event
ages show only what was visible in the bounded `/v1/events` response. Since unchanged source
responses may be deduplicated, they do not prove poll freshness. Those limitations remain printed
in every JSON and Markdown report.

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
  --application-version 0.9.0 \
  --environment free-tier-public \
  --deployed-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --output artifacts/operations/target.json
```

By default, `gdelt` and `usgs` are the required visibility sources because they emit continuously.
NWS can legitimately have no active alert, and FIRMS is opt-in. Add a source only when the deployed
configuration makes its continued visibility a meaningful requirement.

## 2. Capture public and resource evidence

One public probe performs only four fixed, same-origin GET requests:

- `/api/healthz`;
- `/api/readyz`;
- `/api/v1/events?limit=500`; and
- `/api/v1/agent-runs/preflight?q=operational%20boundary`.

It follows no redirect, accepts at most 1 MiB per response, verifies the hostname and public TLS
certificate, records response hashes rather than source text, and requires the exact eleven-check
default-deny execution state.

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

## 4. Bind backup and isolated restore evidence

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
  "schema_version": "1.0.0",
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

## 5. Recompute the report

Evidence files discovered from a directory must end in `.evidence.json`. The report command loads
each file with no-follow, regular-file, stable-read, and 5 MiB bounds; verifies every content ID;
cross-validates target, probe, restart, backup, restore, time, and commit links; then recomputes all
eight gates.

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

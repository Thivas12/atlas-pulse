# Free-tier public deployment path

This runbook exposes the existing evidence dashboard through HTTPS on one free ARM virtual machine.
It does not enable a candidate model or agent, and it does not turn a fresh deployment into a
reliability or quality claim.

The reference host is an Oracle Cloud Infrastructure Ampere A1 Always Free VM with 2 OCPUs and
12 GB RAM. Oracle's current documentation says that Always Free services do not expire, while
availability is capacity-limited, a home region must be chosen carefully, idle accounts may be
suspended, and free-tier terms can change. Confirm the current
[Free Tier page](https://www.oracle.com/cloud/free/) and
[OCI documentation](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm) before
provisioning. Do not create a paid fallback resource.

## Topology and cost boundary

| Layer | Reference choice | Public exposure | Cost guardrail |
| --- | --- | --- | --- |
| Host | OCI Ampere A1, 2 OCPUs / 12 GB | SSH from operator IP only | Keep the total within the documented Always Free allocation |
| Edge | Pinned Caddy container | TCP 80/443 and UDP 443 | Open source; no managed load balancer |
| UI/API | AtlasPulse web proxy and FastAPI | Through Caddy only | Host ports 3000/8000 bind to loopback |
| State | Local PostgreSQL/PostGIS and Valkey volumes on per-client internal networks | None | No managed database or cache |
| DNS | A user-owned hostname or IP-derived `sslip.io` hostname | Resolves to the VM | `sslip.io` is optional third-party convenience, not an AtlasPulse dependency |
| TLS | Caddy automatic HTTPS | Browser-facing | Automated public certificate; no paid certificate service |
| CI | Standard GitHub-hosted runner on this public repository | None | GitHub documents public-repository standard runners as free |

The overlay applies per-container memory and CPU ceilings that fit inside the reference allocation.
Those ceilings prevent one service from taking the whole host; they are not load-test evidence or a
capacity guarantee.

## 1. Provision and lock down the VM

Create an ARM64 Ubuntu 24.04 VM in the account's home region. Allocate only Always Free-labelled
compute and block-volume resources. In the cloud network security list, allow:

- TCP 22 only from the operator's current public IP;
- TCP 80 from the internet;
- TCP 443 and UDP 443 from the internet; and
- no public access to 3000, 8000, 5432, or 6379.

Apply the same host firewall boundary:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from YOUR_OPERATOR_IP to any port 22 proto tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable
```

Replace `YOUR_OPERATOR_IP` before running the command. Keep the current SSH session open until a
second session proves that the rule is correct.

Install Docker Engine plus Compose v2.33.1 or later from a trusted package source, enable security
updates, and allow the deployment user to run Docker. The minimum Compose version supports the
explicit [`gw_priority`](https://docs.docker.com/reference/compose-file/services/#gw_priority)
egress default-gateway policy. Reconnect after changing group membership. The deployment user
effectively has root-equivalent Docker authority and must be protected accordingly.

## 2. Pin code and configure the public name

Clone the public repository and check out an explicit reviewed tag or commit, never a moving branch
for a claimed deployment:

```bash
git clone https://github.com/Thivas12/atlas-pulse.git
cd atlas-pulse
git checkout YOUR_REVIEWED_COMMIT
cp .env.example .env
```

Set at least these values in `.env`:

```dotenv
ATLAS_ENVIRONMENT=free-tier-public
ATLAS_BUILD_COMMIT_SHA=REPLACE_WITH_THE_FULL_40_CHARACTER_REVIEWED_COMMIT
ATLAS_SOURCE_USER_AGENT=AtlasPulse/0.12 (+https://github.com/Thivas12/atlas-pulse)
ATLAS_SOURCE_POLL_STALE_MULTIPLIER=3
ATLAS_USGS_SOURCE_STALE_SECONDS=600
ATLAS_NWS_SOURCE_STALE_SECONDS=900
ATLAS_GDELT_SOURCE_STALE_SECONDS=3600
ATLAS_PUBLIC_HOST=atlas.YOUR_PUBLIC_IP_WITH_DASHES.sslip.io
ATLAS_POSTGRES_PASSWORD=REPLACE_WITH_64_RANDOM_HEXADECIMAL_CHARACTERS
ATLAS_API_RATE_LIMIT_CLIENT_SECRET=REPLACE_WITH_A_DIFFERENT_64_RANDOM_HEXADECIMAL_VALUE
```

Generate each secret separately with `openssl rand -hex 32`; never reuse the database password as
the rate-limit client secret. The public overlay and checked-in policy reject either value when it
is absent, short, development-default, or URL-unsafe. The database value is injected into PostgreSQL and only
the API, migration, projection, and retrieval-indexer database URLs; it is not
available to the ingestor, web server, edge, or Valkey. Keep `.env` outside version control and
restrict it to the deployment account, for example with `chmod 600 .env`.

The rate-limit secret is injected only into the API and HMAC-pseudonymizes normalized client
addresses before short-lived quota keys enter Valkey. It is not an access token. The defaults allow
120 total `/v1/*` requests and 20 expensive search, correlation, evidence-pack, or preflight
requests per client per 60-second window. Keep them until observed latency and resource evidence
justifies a reviewed change; they are not measured capacity or availability claims.

`POSTGRES_PASSWORD` initializes a new data volume but does not alter an existing PostgreSQL role.
When upgrading a pre-segmentation deployment with retained data, generate and place the new value
in `.env`, then change the existing role immediately before switching commits:

```bash
read -r -s -p 'Paste the new database password from .env: ' ATLAS_POSTGRES_PASSWORD
printf '\n'
docker compose exec -T postgres \
  psql --username atlas --dbname atlas --set=ON_ERROR_STOP=1 <<SQL
ALTER ROLE atlas PASSWORD '$ATLAS_POSTGRES_PASSWORD';
SQL
unset ATLAS_POSTGRES_PASSWORD
```

The documented generator produces hexadecimal text, so this statement cannot introduce SQL
metacharacters. Run the rendered-model validator and start the new overlay immediately afterward;
old application containers cannot reconnect once the role changes. Take a current off-host backup
before rotating the credential. A fresh deployment with an empty data volume skips this step.

Confirm that `ATLAS_BUILD_COMMIT_SHA` exactly matches `git rev-parse HEAD`. Both public health
endpoints expose it, allowing the evidence collector to reject a version-correct image built from
the wrong revision.

For example, IP `203.0.113.10` becomes `atlas.203-0-113-10.sslip.io`. A normal DNS A/AAAA record
pointing to the VM is preferable when one is already available. The hostname must resolve publicly
before Caddy can obtain a certificate. Caddy documents that
[automatic HTTPS](https://caddyserver.com/docs/automatic-https) provisions and renews certificates
and redirects HTTP to HTTPS.

Leave FIRMS disabled or add only a free FIRMS MAP_KEY. If enabled, retain the documented
`ATLAS_FIRMS_SOURCE_STALE_SECONDS=129600` bound unless a reviewed operating policy replaces it.
Leave the release-assessment setting unset for the default-deny deployment. Do not put private
approval keys on this public host.

## 3. Validate and start

Every external image reference includes a readable release tag and an immutable multi-platform
digest. `deploy/container-images.json` is the review inventory, and repository-policy tests keep
the Dockerfiles, base Compose model, free-tier overlay, and CI service image synchronized with it.
The locally named `atlas-pulse-postgres` and `atlas-pulse-edge` images are build outputs; their
external PostGIS and Caddy parents are pinned in their Dockerfiles.

Review image digest updates like source changes. The Security workflow builds the deployable
runtime images, reports all HIGH/CRITICAL Trivy findings, and rejects fixable HIGH/CRITICAL
operating-system packages after applying current distribution security updates. Unfixed findings
and embedded vendor-binary fixes remain visible for risk review until an upstream image update can
actually carry the remediation.

Render the merged Compose model before building:

```bash
docker compose -f compose.yaml -f deploy/free-tier/compose.yaml config --format json \
  | python3 scripts/verify_compose_security.py --deployment free-tier
docker compose -f compose.yaml -f deploy/free-tier/compose.yaml up -d --build --wait
```

`deploy/compose-security-policy.json` is the reviewed connectivity and credential-scope contract.
The validator rejects a new service, an undeclared network path, widened egress, misplaced FIRMS or
database credentials, mismatched database passwords, and a weak public password. PostgreSQL and
Valkey expose a separate internal network to each client. The API and web tier share one internal
link; the public edge and web tier share another. The four loopback-published services each receive
their own non-internal host bridge because Docker does not publish ports through an internal-only
bridge. The ingestor and edge use separate purpose-specific egress networks. Every non-internal
network has exactly one service, so none creates a shared container-to-container path.

The host bridges can also provide outbound routing to the API, web, PostgreSQL, and Valkey
containers. Projector, retrieval-indexer, and migration containers remain internal-only. Treat
host-level outbound firewall policy as a separate deployment control, and attach any future
telemetry collector through a reviewed dedicated network rather than widening a data-plane link.

The first build downloads the pinned local BGE embedding artifact and can take several minutes.
The service restart policies bring the stack back after a normal host reboot.

Verify the public edge and the closed agent boundary:

```bash
export ATLAS_PUBLIC_HOST='atlas.YOUR_PUBLIC_IP_WITH_DASHES.sslip.io'
curl --fail --silent --show-error "https://$ATLAS_PUBLIC_HOST/" | grep '<title>AtlasPulse'
curl --fail --silent --show-error --head "https://$ATLAS_PUBLIC_HOST/" \
  | grep -Ei '^(content-security-policy|cross-origin-opener-policy|permissions-policy|referrer-policy|x-content-type-options|x-frame-options):'
curl --fail --silent --show-error "https://$ATLAS_PUBLIC_HOST/api/readyz"
curl --fail --silent --show-error --dump-header - --output /dev/null \
  "https://$ATLAS_PUBLIC_HOST/api/v1/events?limit=1" \
  | grep -Ei '^(x-ratelimit-limit|x-ratelimit-remaining|x-ratelimit-reset-after|x-ratelimit-policy):'
curl --fail --silent --show-error "https://$ATLAS_PUBLIC_HOST/api/v1/source-freshness" \
  | jq '{passed, sources: [.items[] | {source, poll_status, source_data_status, source_age_seconds}]}'
curl --fail --silent --show-error "https://$ATLAS_PUBLIC_HOST/api/v1/source-polls?limit=6" \
  | jq '{count, has_more, order, transitions: [.items[] |
        {source, transition, stage: .attempt.stage, failure_code: .attempt.failure_code}]}'
curl --fail --silent --show-error \
  "https://$ATLAS_PUBLIC_HOST/api/v1/agent-runs/preflight?q=earthquake" \
  | jq '{status: .manifest.status,
         release: .manifest.release.status,
         execution: .manifest.execution,
         checks: (.manifest.authorization.checks | length)}'
```

Expected boundary: manifest `blocked`, release `not_supplied`, 11 checks, execution `not_started`,
and every execution boolean false.

The six browser-response headers must match `web/security-headers.json`. The internal web server,
public Caddy edge, local production preview, Chromium smoke test, and container smoke test share and
verify that contract. HSTS remains public-edge-only because local and internal traffic uses HTTP.
The outer Caddy edge discards spoofed forwarded-address values by default. The internal proxy trusts
only private proxy/loopback peers, parses the chain from right to left, and replaces the API's
client-address header with its normalized result. Exhausted application budgets return `429` with
`Retry-After`; quota-backend failures return `503`. This does not rate-limit static/TLS work or stop
a distributed-address denial-of-service attack, so do not describe it as a WAF.

Inspect container health and confirm that only the intended sockets listen publicly:

```bash
docker compose -f compose.yaml -f deploy/free-tier/compose.yaml ps
sudo ss -lntup
```

## 4. Preserve and observe evidence

At minimum, retain off-host database backups and the raw snapshot volume before making a public
durability claim. A simple PostgreSQL export is:

```bash
mkdir -p "$PWD/backups"
docker compose -f compose.yaml -f deploy/free-tier/compose.yaml \
  exec -T postgres pg_dump --username atlas --dbname atlas --format=custom \
  > "$PWD/backups/atlas-$(date -u +%Y%m%dT%H%M%SZ).dump"
```

Copy backups to operator-controlled storage and test restoration on a separate disposable stack.
Do not describe local-only volumes as a backup. OCI lists Always Free Object Storage, but enabling
it requires a separately reviewed retention and credential policy.

Use the checked-in [operational evidence workflow](operational-evidence.md) to bind the deployed
commit, collect public probes and resource snapshots, and record operator-run restart, encrypted
off-host backup, and isolated restore drills. Its optional source-poll recovery recorder can bind a
separately approved fault window to exact degraded/recovered probes and retained transitions, but
it cannot inject the fault and does not count toward the sampled campaign. Collect at least 30
consecutive aligned UTC sample dates before
publishing even its narrow `minimum_observation_set_complete` result. Event visibility does not
prove source-poll freshness, and a process being up once is not measured availability.

## 5. Update and roll back

Before an update, record the current commit, create an off-host backup, and review the target diff.
For an image update, require the tag, digest, inventory, and use sites to change together and verify
the hosted container vulnerability scan. Then rebuild the pinned target:

```bash
git fetch --tags origin
git checkout YOUR_NEW_REVIEWED_COMMIT
docker compose -f compose.yaml -f deploy/free-tier/compose.yaml up -d --build --wait
```

If validation fails, check out the recorded known-good commit and run the same Compose command.
Named data volumes remain in place. Database migrations still require forward/backward compatibility
review; never assume a code rollback reverses a schema migration.

To stop without deleting evidence:

```bash
docker compose -f compose.yaml -f deploy/free-tier/compose.yaml down
```

Do not add `--volumes` unless permanent deletion of local PostgreSQL, Valkey, raw snapshots, and
Caddy certificate state is explicitly intended and independently backed up.

## Honest claim boundary

After the HTTPS checks pass, the accurate statement is: “AtlasPulse is reachable on a free-tier
host through a pinned HTTPS edge, and its health and default-deny execution boundary were observed
at this time.” It is not yet evidence of an SLA, complete source coverage, factual correctness,
model quality, autonomous operation, disaster recovery, or production-scale capacity.

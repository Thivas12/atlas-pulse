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
| State | Local PostgreSQL/PostGIS and Valkey volumes | None | No managed database or cache |
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

Install Docker Engine plus Compose v2 from a trusted package source, enable security updates, and
allow the deployment user to run Docker. Reconnect after changing group membership. The deployment
user effectively has root-equivalent Docker authority and must be protected accordingly.

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
ATLAS_SOURCE_USER_AGENT=AtlasPulse/0.8 (+https://github.com/Thivas12/atlas-pulse)
ATLAS_PUBLIC_HOST=atlas.YOUR_PUBLIC_IP_WITH_DASHES.sslip.io
```

For example, IP `203.0.113.10` becomes `atlas.203-0-113-10.sslip.io`. A normal DNS A/AAAA record
pointing to the VM is preferable when one is already available. The hostname must resolve publicly
before Caddy can obtain a certificate. Caddy documents that
[automatic HTTPS](https://caddyserver.com/docs/automatic-https) provisions and renews certificates
and redirects HTTP to HTTPS.

Leave FIRMS disabled or add only a free FIRMS MAP_KEY. Leave the release-assessment setting unset
for the default-deny deployment. Do not put private approval keys on this public host.

## 3. Validate and start

Render the merged Compose model before building:

```bash
docker compose -f compose.yaml -f deploy/free-tier/compose.yaml config --quiet
docker compose -f compose.yaml -f deploy/free-tier/compose.yaml up -d --build --wait
```

The first build downloads the pinned local BGE embedding artifact and can take several minutes.
The service restart policies bring the stack back after a normal host reboot.

Verify the public edge and the closed agent boundary:

```bash
export ATLAS_PUBLIC_HOST='atlas.YOUR_PUBLIC_IP_WITH_DASHES.sslip.io'
curl --fail --silent --show-error "https://$ATLAS_PUBLIC_HOST/" | grep '<title>AtlasPulse'
curl --fail --silent --show-error "https://$ATLAS_PUBLIC_HOST/api/readyz"
curl --fail --silent --show-error \
  "https://$ATLAS_PUBLIC_HOST/api/v1/agent-runs/preflight?q=earthquake" \
  | jq '{status: .manifest.status,
         release: .manifest.release.status,
         execution: .manifest.execution,
         checks: (.manifest.authorization.checks | length)}'
```

Expected boundary: manifest `blocked`, release `not_supplied`, 11 checks, execution `not_started`,
and every execution boolean false.

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

Collect at least 30 days of observed readiness, source freshness, ingest gaps, resource pressure,
restart recovery, certificate renewal, and restore-test evidence before claiming an evidence-backed
pilot. A process being up once is not measured availability.

## 5. Update and roll back

Before an update, record the current commit, create an off-host backup, and review the target diff.
Then rebuild the pinned target:

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

# Card-free workstation deployment with Tailscale Funnel

This runbook publishes the complete AtlasPulse evidence dashboard from an existing Windows
workstation through Tailscale Funnel. It requires no cloud VM, public IP, router port-forward, paid
database, or credit card. It does not enable a candidate model or agent, and it does not turn a
reachable workstation into a reliability or quality claim.

Tailscale documents Funnel as available on all plans and its Personal plan as free for personal
use. This path is intended for an independently operated, non-commercial deployment. Check current
[Funnel limits](https://tailscale.com/docs/features/tailscale-funnel) and
[plan eligibility](https://tailscale.com/pricing) before deployment because third-party terms can
change. Funnel is currently beta, permits only ports 443, 8443, and 10000, and has
non-configurable bandwidth limits.

## Topology and honest cost boundary

| Layer | Reference choice | Public exposure | Cost and availability boundary |
| --- | --- | --- | --- |
| Host | Existing Windows 11 PC with Docker Desktop and WSL 2 | None directly | PC, Docker, and internet connection must remain on |
| Public ingress | Tailscale Funnel on Windows | Public HTTPS on the device's `*.ts.net` name | Free Personal plan for eligible personal use; beta service limits apply |
| Local edge | Pinned Caddy container | `127.0.0.1:8443` only | Accepts required PROXY protocol v2 from the local host path |
| UI/API | AtlasPulse web proxy and FastAPI | Through both proxy layers only | Loopback ports remain available for operator diagnostics |
| State | Local PostgreSQL/PostGIS and Valkey volumes | None | Workstation disk is not an off-host backup |

Tailscale terminates public TLS and forwards TLS-terminated TCP plus PROXY protocol v2 to the
loopback-only Caddy edge. That preserves the original client address for the existing pseudonymous
API request budgets. The edge requires a valid PROXY header on every connection, adds the public
HSTS and browser security headers, preserves the original HTTPS scheme, and reaches only the
internal web proxy. No inbound Windows Firewall or router rule is required.

This is a practical evidence deployment, not high availability. Windows updates, power loss,
sleep, Docker restarts, home-internet failure, Funnel limits, or the workstation owner can
interrupt it. A 30-day sampled campaign needs the machine available on every sampled UTC date.

## 1. Prepare the workstation

Use a PC with at least 16 GB RAM, about 12 GB available to Docker, and at least 25 GB free disk.
Install current Docker Desktop with its WSL 2 backend. In Docker Desktop, enable **Start Docker
Desktop when you sign in**. Stop any development AtlasPulse stack before starting the public stack
because both intentionally bind diagnostic ports on host loopback.

Install [Tailscale for Windows](https://tailscale.com/download/windows) and sign in with a personal
identity eligible for the free Personal plan. In PowerShell, record the device's exact DNS name:

```powershell
$tailscale = "$env:ProgramFiles\Tailscale\tailscale.exe"
& $tailscale status
$publicHost = ((& $tailscale status --json | ConvertFrom-Json).Self.DNSName).TrimEnd('.')
$publicHost
```

The result should resemble `your-pc.your-tailnet.ts.net`. It is public metadata once Funnel is
enabled. Do not add `https://`, a port, a path, or the final DNS dot when configuring AtlasPulse.

## 2. Check out one reviewed commit and generate private settings

Use a fresh WSL checkout so the public deployment cannot reuse development volumes or an existing
`.env`. Replace `YOUR_REVIEWED_COMMIT` with a full 40-character commit that passed the required
repository checks:

```bash
git clone https://github.com/Thivas12/atlas-pulse.git atlas-pulse-public
cd atlas-pulse-public
git checkout YOUR_REVIEWED_COMMIT
git status --short
```

Copy the PowerShell DNS result into the next command. The helper binds the exact clean checkout,
generates two independent 256-bit secrets without printing them, writes `.env` with mode `0600`,
and refuses to overwrite an existing file:

```bash
python3 scripts/configure_workstation_funnel.py \
  --public-host your-pc.your-tailnet.ts.net
```

The workstation overlay uses the fixed Compose project name
`atlas-pulse-workstation-funnel`. Its PostgreSQL, Valkey, and raw-snapshot volumes are therefore
separate from the ordinary `atlas-pulse` development project. Keep `.env` outside source control,
terminal output, screenshots, and published artifacts. The database password is available only to
PostgreSQL and its four clients; the client-HMAC secret is available only to the API.

Leave FIRMS disabled unless you already have a free FIRMS MAP_KEY. Leave the release-assessment
setting unset and do not place private approval keys on this host. The public agent execution
boundary remains closed.

## 3. Validate and start the isolated stack

In WSL, render the exact merged model through the checked-in trust-graph validator before building:

```bash
docker compose -f compose.yaml -f deploy/workstation-funnel/compose.yaml \
  config --format json \
    | python3 scripts/verify_compose_security.py --deployment workstation-funnel

docker compose -f compose.yaml -f deploy/workstation-funnel/compose.yaml \
  up -d --build --wait
```

The first build downloads pinned images and the local BGE embedding artifact and can take several
minutes. If startup fails, inspect status without printing container environments:

```bash
docker compose -f compose.yaml -f deploy/workstation-funnel/compose.yaml ps --all
docker compose -f compose.yaml -f deploy/workstation-funnel/compose.yaml logs --no-color migrate
```

Do not publish logs until you have reviewed them. The edge is not a normal HTTP listener: a direct
browser or `curl` request to `127.0.0.1:8443` is expected to fail because it lacks the required
PROXY protocol header.

## 4. Enable public HTTPS through Funnel

Return to an ordinary Windows PowerShell session. This is the exact
[documented TLS-terminated TCP pattern](https://tailscale.com/docs/reference/tailscale-cli/funnel)
with PROXY protocol v2 and a background configuration:

```powershell
$tailscale = "$env:ProgramFiles\Tailscale\tailscale.exe"
& $tailscale funnel --bg --proxy-protocol=2 --tls-terminated-tcp=443 tcp://127.0.0.1:8443
& $tailscale funnel status
```

Tailscale may open a one-time approval page to enable Funnel and HTTPS for the tailnet. Approve only
the displayed device and port. The `--bg` configuration automatically resumes after a Tailscale or
device restart, but the AtlasPulse containers and Windows PC must also be running.

Open `https://your-pc.your-tailnet.ts.net/` from a phone using cellular data or another device
outside the home network. Then verify the fixed public boundary from WSL:

```bash
export ATLAS_PUBLIC_HOST="$(sed -n 's/^ATLAS_PUBLIC_HOST=//p' .env)"
export ATLAS_BUILD_COMMIT_SHA="$(sed -n 's/^ATLAS_BUILD_COMMIT_SHA=//p' .env)"
test -n "$ATLAS_PUBLIC_HOST"
test "${#ATLAS_BUILD_COMMIT_SHA}" -eq 40

curl --fail --silent --show-error "https://$ATLAS_PUBLIC_HOST/" \
  | grep '<title>AtlasPulse'
curl --fail --silent --show-error --head "https://$ATLAS_PUBLIC_HOST/" \
  | grep -Ei '^(content-security-policy|strict-transport-security|x-content-type-options):'
curl --fail --silent --show-error "https://$ATLAS_PUBLIC_HOST/api/readyz"
curl --fail --silent --show-error --dump-header - --output /dev/null \
  "https://$ATLAS_PUBLIC_HOST/api/v1/events?limit=1" \
  | grep -Ei '^(x-ratelimit-limit|x-ratelimit-remaining|x-ratelimit-policy):'
curl --fail --silent --show-error \
  "https://$ATLAS_PUBLIC_HOST/api/v1/agent-runs/preflight?q=earthquake"
```

Expected boundary: readiness reports the exact checked-out commit; the agent manifest is
`blocked`, release assessment is `not_supplied`, execution is `not_started`, and every execution
boolean is false. Source freshness can initially be `starting` until each enabled poller records a
terminal attempt.

## 5. Keep the evidence deployment alive

For the duration of a campaign:

- keep the PC plugged in and configure Windows not to sleep while plugged in;
- keep Docker Desktop and Tailscale set to start at sign-in;
- check `tailscale funnel status` and Compose health after Windows updates or reboots;
- keep at least 20% free disk and review resource evidence before increasing any limit; and
- do not advertise uptime, capacity, or recovery guarantees from sampled observations.

Create the immutable operations target with this profile's two Compose files:

```bash
mkdir -p artifacts/operations/evidence

uv run atlas-pulse-operations target \
  --origin "https://$ATLAS_PUBLIC_HOST" \
  --commit-sha "$ATLAS_BUILD_COMMIT_SHA" \
  --application-version 0.12.0 \
  --environment workstation-funnel-public \
  --deployed-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --compose-file compose.yaml \
  --compose-file deploy/workstation-funnel/compose.yaml \
  --output artifacts/operations/target.json
```

Continue with the [sampled operational evidence campaign](operational-evidence.md). A copy on the
same workstation is not an off-host backup. For the campaign's backup/restore requirement, use an
encrypted removable drive or a separately controlled second device, remove or disconnect it after
copying, and perform the checked-in isolated-restore drill. Never claim durability before that
evidence passes.

Run one sample manually before scheduling it:

```bash
~/.local/bin/uv run --frozen --offline python scripts/collect_daily_evidence.py
echo "Daily evidence exit code: $?"
tail -n 80 artifacts/operations/daily-evidence.log
```

Exit `0` means both the public probe and resource observation passed. Exit `1` means the failing
observation was still saved for the campaign. Exit `2` means the checkout, target, tool, or command
was invalid. The helper refuses to capture when Git `HEAD` differs from the immutable target,
records one shared UTC timestamp for both observations, refreshes the current report, and appends a
private local log without reading container logs or environments.

On Windows, schedule that exact checked-in command from an ordinary PowerShell session. Replace
only the WSL username, distribution, project path, and desired local trigger time:

```powershell
$taskName = "AtlasPulse Daily Evidence"
$wslDistro = "Ubuntu-24.04"
$projectDir = "/home/YOUR_WSL_USER/atlas-pulse-public"
$uvPath = "/home/YOUR_WSL_USER/.local/bin/uv"
$actionArgs = "-d $wslDistro --cd $projectDir -e $uvPath run --frozen --offline python scripts/collect_daily_evidence.py"
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wsl.exe" -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -Daily -At 10:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
  -Settings $settings -Description "Capture exact-target AtlasPulse operational evidence" -Force
```

After a scheduled run, PowerShell reports its result without opening a transient terminal:

```powershell
Get-ScheduledTaskInfo -TaskName "AtlasPulse Daily Evidence" |
  Format-List LastRunTime, LastTaskResult, NextRunTime
```

Read `artifacts/operations/daily-evidence.log` in WSL for the exact failed command and exit code.
Never delete a valid failing evidence artifact merely to make the campaign report green.

## 6. Stop or revoke public access

Disable public ingress immediately in PowerShell:

```powershell
$tailscale = "$env:ProgramFiles\Tailscale\tailscale.exe"
& $tailscale funnel reset
& $tailscale funnel status
```

Stop the application without deleting evidence or data:

```bash
docker compose -f compose.yaml -f deploy/workstation-funnel/compose.yaml down
```

Do not add `--volumes` unless permanent deletion of the separate PostgreSQL, Valkey, and raw
snapshot volumes is intentional and independently backed up.

## Honest claim boundary

After the checks pass, the accurate statement is: “AtlasPulse was reachable through Tailscale
Funnel from this workstation, and its exact-commit health and default-deny execution boundary were
observed at this time.” It is not an SLA, cloud deployment, complete source-coverage claim,
capacity result, disaster-recovery claim, factual-correctness result, model-quality result, or
autonomous-agent deployment.

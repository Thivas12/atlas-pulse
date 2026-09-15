# ADR 0035: Add a card-free workstation deployment through Tailscale Funnel

## Status

Accepted.

## Context

The existing public-reference runbook targets an OCI Always Free VM. Account activation can still
require a payment card, so that path is unavailable to an operator without one. AtlasPulse needs
the full PostgreSQL/PostGIS, Valkey, API, worker, local embedding, and web stack; splitting it over
small hosted free services would weaken the tested single-host model and introduce sleep, expiry,
storage, or memory limits.

An existing Windows/WSL workstation already has enough compute and persistent storage. Publishing
router ports directly would expose a residential host, discard the reviewed public TLS boundary,
and make safe client-address propagation harder. Tailscale Funnel can instead terminate public TLS
for a stable device `*.ts.net` name and forward only to a local loopback target. Its PROXY protocol
mode preserves the original source address required by the existing pseudonymous per-client API
budgets.

## Decision

1. Add a separate `workstation-funnel` Compose overlay rather than changing the OCI overlay or
   development topology.
2. Give it the fixed Compose project name `atlas-pulse-workstation-funnel` so new public secrets
   initialize separate state volumes instead of silently reusing a local-development database.
3. Reuse the public overlay's container resource ceilings, database-password requirement,
   API-only client-HMAC secret, security posture, and exact per-client network segmentation.
4. Publish the new outer Caddy edge only as `127.0.0.1:8443`. Do not open a router, Windows
   Firewall, or container port directly to the internet.
5. Configure Tailscale Funnel to terminate public TLS on port 443 and forward TCP to the loopback
   edge with PROXY protocol v2. Require that protocol on every edge connection; the published host
   socket remains loopback-only.
6. Keep the internal web proxy responsible for strict trusted-proxy parsing and for overwriting the
   API's `X-Atlas-Client-IP`. Continue HMAC-pseudonymizing that address before storing short-lived
   request-budget keys.
7. Add HSTS and the synchronized browser response policy at the workstation edge. Disable Caddy's
   admin endpoint and persistent runtime configuration because Tailscale owns certificates and the
   file-mounted edge configuration is immutable. Explicitly pass the original public scheme as
   HTTPS because the edge receives TLS-terminated plaintext.
8. Add a standard-library setup helper that accepts only a full Tailscale DNS name, binds the exact
   clean Git revision, generates two independent 256-bit secrets without printing them, creates a
   private `.env`, and refuses to overwrite an existing file.
9. Machine-check the merged topology, exact published sockets, and credential owners as a distinct
   deployment profile. In hosted CI, render the profile and validate the Caddy configuration with
   the pinned edge image.
10. Preserve the same exact-commit and sampled operational-evidence claim boundary. Treat the
    workstation, Docker daemon, local Tailscale client, power, and home connection as trusted and
    fallible operator infrastructure.

## Consequences

- An eligible personal portfolio deployment no longer depends on a credit card, cloud VM, public
  IP, DNS purchase, managed database, router port-forward, or paid certificate.
- Public traffic reaches only Tailscale's Funnel ingress and a loopback-only, PROXY-aware edge.
  PostgreSQL, Valkey, API, workers, and internal web links retain the reviewed container boundary.
- The preserved source address keeps existing per-client rate limits meaningful without retaining
  raw addresses. Funnel is not a WAF, and a distributed-address attack can still exceed them.
- The public state is isolated from an ordinary development Compose project, but the same host and
  Docker administrator can inspect or alter both.
- Availability now depends on the workstation staying powered, awake, connected, signed in, and
  healthy. Funnel is beta with limited allowed ports and non-configurable bandwidth limits.
- A workstation disk is not an off-host backup. An encrypted removable drive or second controlled
  device is still required before the operational campaign can make its narrow backup/restore
  observation.
- Tailscale's free plan and Funnel behavior are third-party terms. The runbook must be rechecked if
  plan eligibility, limits, or CLI behavior changes.

## Rejected alternatives

- **Require OCI account activation.** This does not solve the missing-card blocker.
- **Use Render free services.** Free web services sleep after inactivity, local files are
  ephemeral, and free PostgreSQL expires; that conflicts with a 30-day stateful evidence campaign.
- **Use Railway's small free allowance.** Its recurring free resource allowance is too small for
  the reviewed full stack and cannot preserve the tested deployment boundary.
- **Use a Cloudflare Quick Tunnel.** Quick Tunnels are documented for testing, have a random
  hostname and no SLA, and cannot serve as the stable 30-day reference deployment.
- **Forward router ports to Caddy.** This expands the residential-host attack surface, requires
  firewall/DNS/TLS administration, and is unnecessary for the bounded portfolio deployment.
- **Use an ordinary Funnel HTTP reverse proxy.** It is simpler, but the PROXY protocol path is
  needed to preserve the original client address through both reviewed proxies.

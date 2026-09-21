# ADR 0040: Pin workstation Funnel probes to public relay addresses

## Status

Accepted.

## Context

The operational collector rejects a deployment origin unless its preflight DNS result contains
only globally routable addresses. That boundary prevents a nominal public hostname from reaching a
loopback, private, link-local, or metadata endpoint and avoids resolving the hostname again between
the preflight and the request.

Tailscale Funnel has two intentional views of the same `*.ts.net` device name. Public DNS directs an
internet client to a Funnel relay, while MagicDNS can direct a client on the tailnet to the device's
non-public address. A workstation running its own collector can therefore reach the deployment with
an ordinary HTTPS client while the collector correctly rejects the local DNS answer. Accepting the
tailnet address would make the command pass, but it would no longer be a sample of the public Funnel
path.

## Decision

1. Keep the system-resolver, all-addresses-global rule for ordinary public deployment profiles.
2. Treat `workstation-funnel-public` as an explicit routing profile. Require its origin to be a full
   `*.ts.net` device name and its immutable target to include the workstation Funnel Compose
   overlay.
3. Resolve that origin through public DNS-over-HTTPS using two fixed providers. Bootstrap each
   resolver by its global address, retain certificate verification, and fail closed when neither
   resolver returns an acceptable answer.
4. Reject the complete public DNS result if it is empty, malformed, or contains any non-global
   address.
5. Pin every fixed HTTP probe and the certificate observation to the validated global addresses.
   Preserve the original origin hostname as the HTTP `Host` value and TLS SNI name so the normal
   public certificate and virtual-host checks still apply.
6. Keep redirects disabled, response bodies bounded to 1 MiB, proxy environment ignored, and the
   five-path allowlist unchanged. Do not fall back to MagicDNS or a tailnet address after public
   resolution or relay failure.
7. Continue describing the result as a point-in-time sample from the collector machine, not an
   independent monitor or availability SLA. Public DNS and its providers remain trusted external
   dependencies.

## Consequences

- A workstation can sample the actual public Funnel relay path even when local MagicDNS shadows the
  public answer.
- The address checked before a request is the address used for the request, closing the previous
  system-DNS re-resolution gap for real probes.
- A DNS-over-HTTPS provider outage or blocked outbound HTTPS can produce a failing sample even when
  Funnel itself is healthy. The second provider reduces but does not remove that dependency.
- Existing content-addressed target and evidence schemas do not change; routing is selected by the
  already-bound deployment environment and exact implementation commit.

## Rejected alternatives

- **Allow Tailscale CGNAT addresses.** This would test private tailnet reachability, not public
  Funnel reachability, and would weaken the generic private-address boundary.
- **Accept any one global system-DNS answer.** A mixed result could still let the HTTP client choose
  a private address, and a later lookup would retain a DNS-rebinding gap.
- **Use an unpinned HTTP client after public resolution.** Re-resolving through MagicDNS would undo
  the public-path guarantee.
- **Require an external monitoring service immediately.** An independent monitor is valuable but
  is not required for this deliberately bounded, independently operated observation campaign.


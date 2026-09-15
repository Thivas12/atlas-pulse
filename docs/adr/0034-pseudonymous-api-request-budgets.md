# ADR 0034: Bound public API requests with pseudonymous shared quotas

## Status

Accepted

## Context

The free-tier deployment exposes an unauthenticated read API on a small host. Query parameters and
container resources are bounded, but a client can still repeat valid expensive searches,
correlations, evidence-pack builds, or preflight requests fast enough to consume the shared CPU and
database budget. An in-process counter would reset on restart and would not coordinate multiple API
processes. The standard Caddy image does not include an application rate-limit handler, and adding a
third-party plugin would create another compiled supply-chain input.

The public request crosses two Caddy proxies. The outer edge is the first public hop and Caddy
ignores client-supplied `X-Forwarded-*` values by default. The internal web proxy therefore needs an
explicit, narrow rule for deriving one stable client address without allowing an internet client to
choose its quota key. Raw client addresses should not become durable Valkey key material.

## Decision

1. Apply a 120-request fixed-window budget per client over 60 seconds to every `/v1/*` route. Apply
   an additional 20-request budget over the same window to search, correlation, evidence-pack, and
   agent-preflight routes. Keep both values configurable, while rejecting an expensive-route budget
   greater than the general budget.
2. Exempt `/healthz` and `/readyz` so orchestrator health checks do not consume a public client's
   quota. Readiness already fails when the shared Valkey event stream is unavailable.
3. Have the internal Caddy proxy trust forwarded chains only from private proxy or loopback peers,
   parse the chain from right to left, and overwrite `X-Atlas-Client-IP` with Caddy's normalized
   `{client_ip}` value on the API hop. The public edge remains the only internet-facing service and
   uses Caddy's default spoof-resistant forwarded-header behavior.
4. Parse only a single valid IPv4 or IPv6 address in the API. If the reviewed header is absent or
   malformed, fall back to the direct peer address. HMAC the canonical address with a
   deployment-specific 32-128 character secret before constructing a Valkey key; never store or
   return the raw address.
5. Consume each budget with one atomic Valkey Lua operation. The first request sets the expiry;
   later requests increment without extending it. Return remaining-budget headers on handled
   `/v1/*` responses and return `429` plus `Retry-After` after exhaustion.
6. Fail closed with `503` when the quota cannot be evaluated. The limiter uses a separate client so
   its lifecycle is explicit, while reusing the API's already-authorized Valkey network path.
7. Require a deployment-supplied client-HMAC secret in the public Compose overlay and enforce that
   it is API-only in the rendered trust-graph policy.

## Consequences

- One client cannot continuously exceed the configured application request budget merely by
  reconnecting or by distributing calls across API workers on the same Valkey deployment.
- Valkey contains only pseudonymous HMAC digests, policy names, counters, and short expiries. The
  HMAC secret is not an authentication credential, but rotating it invalidates existing quota keys.
- The defaults are conservative starting controls, not capacity measurements or availability
  guarantees. Tune them only from observed latency, resource, and legitimate-traffic evidence.
- A distributed attack can use many source addresses, and static-file/TLS work occurs before the
  application limiter. Host firewalling, upstream filtering, or a separately reviewed edge/WAF
  control is still required for stronger denial-of-service resistance.
- Host and Docker administrators remain trusted. The loopback development path may supply forwarded
  headers because anyone able to reach it is already inside that boundary.

## Rejected alternatives

- **Compile a Caddy rate-limit plugin.** This moves enforcement earlier but introduces a new Go
  module and custom edge binary before measured need justifies that supply-chain expansion.
- **Use per-process memory counters.** They diverge across workers, reset on restart, and are easy to
  bypass during rolling updates.
- **Trust arbitrary `X-Forwarded-For` in FastAPI.** Two proxy hops and the loopback path make naive
  left-most parsing spoofable and deployment-dependent.
- **Add user accounts or API keys.** AtlasPulse has no private or multi-tenant data; authentication
  is a separate product and browser trust boundary, not a prerequisite for a bounded public demo.

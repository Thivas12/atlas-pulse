# ADR 0031: Synchronize and execute-test the browser response policy

## Status

Accepted.

## Context

The internal web container already emitted a partial Content Security Policy, but the public edge
relied on that upstream header being preserved, the threat model still described CSP as absent,
and the production-build Playwright test used Vite preview without security headers. The result was
an unverified control whose documentation, local browser path, and deployed path could drift.

AtlasPulse renders untrusted public-source text, opens external evidence links, and loads MapLibre,
OpenFreeMap tiles, and Google Fonts. A browser policy therefore has to be restrictive without
silently breaking the map or replacing a real compatibility check with a static string assertion.

## Decision

1. Treat `web/security-headers.json` as the machine-readable browser-response contract used by
   Vite production preview and test assertions.
2. Declare the same values explicitly in both the internal web Caddyfile and the public TLS-edge
   Caddyfile. A repository policy test rejects drift between either declaration and the JSON
   contract.
3. Enforce a CSP that defaults to same-origin, blocks base replacement, forms, frames, objects, and
   media, and allows only the external origins required for OpenFreeMap and Google Fonts.
4. Retain `blob:` workers and inline styles because MapLibre needs them. These are bounded
   exceptions, not general permission to add inline scripts or arbitrary origins.
5. Run Playwright against Vite preview with the policy enabled, assert every document header, fail
   on CSP console violations, and require MapLibre to emit a viewport query. The container smoke
   separately verifies the headers emitted by the built web image.
6. Keep HSTS at the public TLS edge only. Local preview and the internal Compose hop intentionally
   use HTTP and must not imitate a transport guarantee they do not provide.
7. Do not add a CSP report collector. It would create a new public ingestion surface, persistence
   and privacy obligations for a control that can be verified deterministically in CI.

## Consequences

- A new script, style, font, image, connection, frame, form, or worker requirement fails closed
  until the contract, both Caddyfiles, tests, and threat model are reviewed together.
- Browser smoke now proves that the built dashboard's current critical path operates under the
  declared policy, while container smoke proves the actual web image emits it.
- OpenFreeMap and Google Fonts remain browser-side trust dependencies. The dashboard keeps usable
  system-font fallbacks if the font provider is unavailable.
- `style-src 'unsafe-inline'` and `worker-src blob:` remain explicit residual risks. Removing them
  requires a measured MapLibre-compatible design rather than an untested policy edit.
- This change does not add authentication, make source content trustworthy, or enable model or
  agent execution.

## Rejected alternatives

- **CSP report-only.** It detects some violations but does not enforce the boundary.
- **Trust implicit reverse-proxy propagation.** It leaves the public policy coupled to an upstream
  implementation detail and permits silent drift.
- **Allow broad HTTPS or wildcard origins.** It weakens the value of CSP and hides new external
  dependencies.
- **Add a managed CDN or WAF for headers.** It adds cost and another control plane without solving
  repository-level contract drift.

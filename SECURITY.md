# Security policy

## Supported versions

AtlasPulse is an evidence-first portfolio system under active development. Security fixes target
the current `main` branch. Older commits, images, and local artifacts are not maintained as
separate supported releases.

| Version | Supported |
| --- | --- |
| Current `main` | Yes |
| Older commits or images | No |

## Report a vulnerability privately

Use GitHub's **Security → Report a vulnerability** flow for this repository. Do not open a public
issue with exploit details, credentials, private source data, or a working proof of concept. If the
private form is unavailable, open a minimal issue asking the maintainer for a private contact path
without disclosing the vulnerability.

Include the affected commit, component, realistic impact, reproduction prerequisites, and the
smallest safe reproduction you can provide. Replace any real credential or personal data with a
clearly marked sentinel.

Reports are handled on a best-effort basis; this project does not promise a response SLA or a bug
bounty. The maintainer will validate the report, coordinate a fix and disclosure when warranted,
and credit reporters who want attribution.

## Scope

Useful reports include vulnerabilities in the source adapters, API, browser application,
projection and retrieval paths, evaluation tooling, agent governance boundary, container images,
deployment configuration, or CI workflows. Upstream dependency reports are useful when they show
an exploitable AtlasPulse path rather than only restating an advisory.

The project deliberately exposes a read-only public evidence surface. Missing multi-tenant
authentication, paid-service integrations, high-availability infrastructure, and executable agent
actions are outside the current product scope. A way to bypass the documented default-deny agent
boundary or disclose a configured credential is in scope.

## Operational credential response

Never commit a live NASA FIRMS key, signing private key, database credential, or deployment secret.
If one is exposed, revoke or rotate it first, remove it from active systems, and then assess history
and logs. Rewriting Git history alone does not invalidate a credential.

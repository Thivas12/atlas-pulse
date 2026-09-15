# ADR 0033: Segment runtime trust boundaries and scope injected credentials

## Status

Accepted.

## Context

AtlasPulse exposed database, Valkey, API, and web ports only on loopback and made the public edge
the sole internet-facing container. However, Docker Compose still attached every service to one
implicit network. A compromised web server, source ingestor, or worker could therefore address
unrelated containers directly. One shared environment anchor also injected both database and
Valkey connection settings into processes that did not use them, including the database URL in the
internet-facing ingestor.

The single-host free-tier design cannot become zero trust, but it can make the reviewed service
graph the enforced default. The control must preserve source polling, automatic HTTPS, local host
access, real PostGIS/Valkey integration, ARM compatibility, and a zero-cost deployment.

## Decision

1. Replace the implicit shared network with one internal two-member network for each client-to-
   PostgreSQL or client-to-Valkey dependency. PostgreSQL and Valkey may join several networks, but
   clients do not share those networks with one another.
2. Put the API and internal web proxy on one dedicated internal network. In the public overlay, put
   the web proxy and Caddy edge on a separate internal network.
3. Give the ingestor and public edge separate single-service purpose-specific egress networks, so
   source polling and ACME do not create a shared lateral path.
4. Split the Compose environment anchor into runtime, source-policy, Valkey, and database maps.
   Inject database, Valkey, FIRMS, and approval settings only into processes that consume them. The
   API and ingestor retain an identical source-freshness policy without sharing the ingestor's
   source credential.
5. Keep the known `atlas` database credential only in the loopback local-development profile. The
   public overlay requires `ATLAS_POSTGRES_PASSWORD`, injects it into PostgreSQL and the four
   database clients, and constructs every client URL from that same value.
6. Require the public password to contain 32-128 URL-safe ASCII letters, digits, underscores, or
   hyphens. This makes direct URL construction unambiguous; the runbook recommends a 256-bit
   hexadecimal value from `openssl rand -hex 32`.
7. Store the exact network membership, internal/egress status, and sensitive environment owners in
   `deploy/compose-security-policy.json`. Validate the rendered base and public-overlay Compose
   models in CI. Reject unknown services or networks, changed memberships, widened credential
   scope, mismatched database credentials, and weak public passwords.
8. Keep the API, database, cache, and internal web ports bound to host loopback for development and
   operator diagnostics. Attach each published service to its own non-internal host bridge because
   Docker does not route a published port through an internal-only bridge. These networks have no
   container peers but can provide outbound routing.
9. Set `gw_priority` on every purpose-specific egress and host attachment so it is the deterministic
   default gateway instead of relying on network attachment order.
10. Do not add a model, agent, executable side effect, external secrets service, service mesh, paid
   firewall, or hosted control plane.

## Consequences

- Compromising the web proxy no longer gives it a network path to PostgreSQL, Valkey, the ingestor,
  or workers. Compromising one worker does not expose another worker's network endpoint.
- The ingestor can reach public sources and its dedicated Valkey endpoint, but not PostgreSQL, the
  API, web, edge, projector, or retrieval indexer by service network.
- The edge can reach public ACME/TLS services and the web proxy, but not internal application or
  data services directly.
- A compromise of PostgreSQL or Valkey remains serious because each is intentionally present on
  every required client link. Docker/host administrators can inspect networks and environment
  values regardless of container segmentation.
- Four database clients still share one application role. Separate read, write, migration, and
  projection roles require a later schema-privilege design and migration plan.
- Migration, projection, and retrieval-indexer containers have no non-internal network. The API,
  web, PostgreSQL, and Valkey host bridges may permit outbound traffic; host firewall egress policy
  remains a separate control.
- Existing local workflows retain the convenient loopback credential. It must never be represented
  as suitable for a public deployment.
- PostgreSQL applies `POSTGRES_PASSWORD` only when initializing an empty data directory. The runbook
  includes an explicit role-rotation step for an existing public volume; changing `.env` alone is
  insufficient and would prevent clients from reconnecting.
- Adding a service or connection requires a deliberate policy update and hosted rendered-model
  validation, making topology drift visible in review.

## Rejected alternatives

- **Keep one shared default network.** Loopback host bindings do not prevent container-to-container
  lateral movement on that network.
- **Use one database network and one Valkey network.** This blocks cross-datastore access but still
  lets every client on a datastore network address every other client.
- **Give runtime services one shared outbound network.** It preserves optional external telemetry
  but recreates a broad lateral path. Single-service host bridges and reviewed dedicated networks
  are narrower.
- **Remove loopback database and cache ports.** That would unnecessarily break the documented local
  development and operator workflow; loopback exposure is a separate host boundary.
- **Put the public password directly in the repository.** A non-default checked-in secret is still
  public and reusable. The deployment must supply its own value outside version control.
- **Adopt a service mesh or managed secrets platform.** The operational and cost burden is not
  justified for this single-host public-reference deployment.

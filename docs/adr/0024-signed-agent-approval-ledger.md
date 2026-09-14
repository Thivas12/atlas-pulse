# ADR 0024: Bind human approval to a signed append-only agent-run ledger

## Status

Accepted

## Context

The v1 preflight records an immutable blocked decision, but it deliberately omits durable actor
identity, approval lifetime, revocation, signatures, and a run ledger. Its manifest identity also
covers the authorization result. Binding an approval directly to that identity would be circular:
the approval would change the human-release result, which would create a different manifest.

AtlasPulse must make the human gate inspectable without turning approval into execution. A database
row with mutable `approved=true` would not preserve who approved which exact evidence and policy,
when that authority ends, or whether the record was rewritten later.

## Decision

Introduce a separate `agent-run-proposal-v1` identity and the `agent-authorization-v2` policy.

1. Derive `proposal-<sha256>` from the exact request, evidence-pack binding, capability scope, and
   policy snapshot. Authorization observations and timestamps are excluded, so an approval can
   target a stable scope without a hash cycle.
2. Represent a grant as immutable `agent-approval-v1` content. Its `approval-<sha256>` identity
   covers the issuer-qualified human actor ID, exact proposal and source-manifest IDs, policy
   version, issue time, expiry, rationale, and locked caveat.
3. Require expiry after issue and cap approval lifetime at 24 hours. Retrieval evidence can change
   independently, so a changed pack produces a new proposal that the old approval cannot satisfy.
4. Represent revocation as a separate immutable `agent-approval-revocation-v1` artifact. It binds
   the exact approval, proposal, source manifest, revoker identity, time, and rationale. No grant
   row is mutated.
5. Store grants, revocations, and explicitly recorded blocked preflights as
   `signed-agent-run-ledger-v1` entries. Every entry contains the previous entry ID and is signed
   with Ed25519 over canonical JSON. The entry ID covers both signed content and signature.
6. Derive `ed25519-<sha256>` key IDs from raw public-key bytes and carry the public key with each
   entry for independent cryptographic verification. Deployment configuration supplies the trusted
   key-ID allowlist; the default list is empty and therefore cannot activate an approval.
7. Serialize appends with a PostgreSQL transaction-scoped advisory lock. Enforce one genesis,
   unique predecessor and artifact identities, bind indexed columns to the signed JSON entry, and
   reject every `UPDATE`, `DELETE`, or `TRUNCATE` with database triggers.
8. Keep governance mutation outside the unauthenticated HTTP API. The operator-only CLI creates
   keys, records preflights, grants, revokes, checks status, and verifies the complete chain.
9. Let `GET /v1/agent-runs/preflight` accept an optional `approval_id`. It resolves chain integrity,
   key trust, proposal scope, expiry, and revocation at the current time. Only `active` can pass the
   human-release check.
10. Keep execution hard-disabled. Even an active approval leaves the model, live relationship,
    grounded-answer, and execution-release checks blocked, with every execution flag false.

## Consequences

- An operator can prove which key signed which actor assertion, proposal, lifetime, and revocation.
- The same no-approval preflight remains deterministic. Approval-aware manifests include
  `evaluated_at`, so their content identity intentionally changes with the time-qualified decision.
- Expired, revoked, wrong-scope, untrusted, malformed, unavailable, and missing approvals remain
  distinct machine-readable blocked states.
- Trusted key rotation requires retaining every still-relevant historical key ID in the allowlist;
  resolution fails closed if any entry in the chain has an untrusted signer.
- Ed25519 proves possession of a configured key, not the real-world truth of a claimed actor ID.
  Operators must protect private keys and manage the key-to-person mapping outside AtlasPulse.
- PostgreSQL permissions still matter. The trigger prevents ordinary mutation by the application
  role, while signatures and hash chaining expose forged inserts or out-of-band tampering.
- A recorded preflight is explicitly not a run receipt. No new event type claims that execution
  started, completed, failed, used a tool, or caused a side effect.

## Rejected alternatives

- **Approve the v1 manifest ID.** Its authorization result includes the human gate, creating an
  identity cycle as soon as that gate changes.
- **Use an HMAC.** Symmetric verification would require distributing the signing secret to every
  auditor and could not separate signing authority from public verification.
- **Store mutable approval status.** Updating a grant in place would erase the historical order and
  weaken revocation evidence.
- **Trust any embedded public key.** A self-signed entry proves integrity but not authority; a
  deployment-owned key allowlist is required.
- **Expose unauthenticated approval POST endpoints.** The current API has no operator authentication
  boundary strong enough for release mutations.
- **Let human approval enable the agent.** Approval is one gate, not a substitute for representative
  model evidence, release thresholds, or the separate execution switch.

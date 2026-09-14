# Signed agent-run approval ledger

AtlasPulse now has a durable governance layer for the future evidence-triage specialist. It can
record a blocked preflight, a short-lived human approval, and an immutable revocation. It still
cannot execute the proposed agent.

## Identity and trust chain

| Artifact | Identity | What it binds |
| --- | --- | --- |
| Evidence pack | `pack-<sha256>` | Exact retrieval result, budgets, citations, and admitted text |
| Run proposal | `proposal-<sha256>` | Pack reference, requested capabilities, and policy scope |
| Approval | `approval-<sha256>` | Proposal, source manifest, actor, policy, issue/expiry, and reason |
| Revocation | `revocation-<sha256>` | Approval, proposal, revoker, time, and reason |
| Ledger entry | `ledger-<sha256>` | Event, previous entry, Ed25519 public key, and signature |

All content identities use `sha256-canonical-json-v1`. The signing payload uses the same sorted,
compact UTF-8 encoding. A signing key is named `ed25519-<sha256>` from its 32 raw public-key bytes.
The public key travels with the entry, but it becomes authoritative only when its key ID appears in
`ATLAS_AGENT_APPROVAL_TRUSTED_KEY_IDS`.

The default trusted-key list is empty. That is intentional: copying a public key into a ledger entry
does not grant it release authority.

## Approval states

| State | Human-release result | Meaning |
| --- | --- | --- |
| `active` | Passed | Trusted signature, exact proposal, current time inside lifetime, no effective revocation |
| `not_supplied` / `not_found` | Blocked | No usable approval was requested or found |
| `not_yet_valid` / `expired` | Blocked | Evaluation time is outside the approved interval |
| `revoked` | Blocked | A trusted immutable revocation is already effective |
| `scope_mismatch` | Blocked | The live pack/policy produced a different proposal |
| `untrusted_signer` | Blocked | At least one ledger signer is outside the configured trust anchors |
| `ledger_invalid` | Blocked | Identity, signature, artifact, order, or hash-chain verification failed |
| `ledger_unavailable` | Blocked | Durable approval state could not be read |

An active approval passes only `human_release`. AtlasPulse still reports `blocked` because quality
gates require a separately validated relationship, grounded-answer, trajectory, drift, and release
assessment, while execution release remains hard-disabled. The execution state
remains `not_started` with every action flag false.

## Operator workflow

Apply the ledger migration and capture a preflight response:

```bash
uv run alembic upgrade head
curl -fsS --get 'http://localhost:8000/v1/agent-runs/preflight' \
  --data-urlencode 'q=residents ordered to shelter from a dangerous storm' \
  --data-urlencode 'bbox=-125,24,-66,50' \
  > artifacts/agent-preflight.json
```

Generate a dedicated local Ed25519 key pair. The CLI refuses to overwrite either path and creates
the private key with mode `0600`:

```bash
uv run atlas-pulse-govern-agent-runs keygen \
  --private-key .secrets/agent-release.pem \
  --public-key .secrets/agent-release.pub.pem
```

Copy the printed key ID—not the private key—into the API environment as a JSON list, then restart
the API:

```bash
ATLAS_AGENT_APPROVAL_TRUSTED_KEY_IDS='["ed25519-<sha256>"]'
```

Optionally record the blocked preflight, then grant a one-hour approval for its exact proposal:

```bash
uv run atlas-pulse-govern-agent-runs record-preflight \
  --preflight artifacts/agent-preflight.json \
  --private-key .secrets/agent-release.pem \
  --actor-id github:12345

uv run atlas-pulse-govern-agent-runs grant \
  --preflight artifacts/agent-preflight.json \
  --private-key .secrets/agent-release.pem \
  --actor-id github:12345 \
  --ttl-minutes 60 \
  --reason 'Reviewed exact evidence, scope, and blocked release gates.'
```

The grant command prints the signed ledger entry. Supply its `event.approval_id` when repeating the
same preflight:

```bash
curl -fsS --get 'http://localhost:8000/v1/agent-runs/preflight' \
  --data-urlencode 'q=residents ordered to shelter from a dangerous storm' \
  --data-urlencode 'bbox=-125,24,-66,50' \
  --data-urlencode 'approval_id=approval-<sha256>'
```

Live evidence may change between calls. If it does, the proposal identity changes and the approval
correctly resolves to `scope_mismatch`.

Inspect status, revoke, and verify the complete chain:

```bash
uv run atlas-pulse-govern-agent-runs status \
  --approval-id approval-<sha256> \
  --proposal-id proposal-<sha256> \
  --trusted-public-key .secrets/agent-release.pub.pem

uv run atlas-pulse-govern-agent-runs revoke \
  --approval-id approval-<sha256> \
  --private-key .secrets/agent-release.pem \
  --actor-id github:12345 \
  --reason 'Release withdrawn after evidence or policy review.'

uv run atlas-pulse-govern-agent-runs verify \
  --trusted-public-key .secrets/agent-release.pub.pem
```

`verify` checks sequence continuity, the previous-entry chain, unique identities, every embedded
artifact identity, every Ed25519 signature, and the supplied trust anchors.

## Persistence boundary

`agent_run_ledger_entries` is a single ordered PostgreSQL chain. Appends acquire a transaction-level
advisory lock, verify all retained entries, derive the next sequence and predecessor, sign the new
event, and insert it atomically. Constraints allow one genesis and prevent branches or duplicate
artifacts, and bind every indexed column back to the signed JSON entry. Database triggers reject
every update, delete, and table truncation; database owners can still replace schema objects, so
normal PostgreSQL privilege separation and backups remain required.

This is tamper-evident application governance, not an external transparency log or hardware-backed
identity system. Backups, database access controls, offline private-key protection, and a documented
key-to-human mapping remain operational responsibilities. See
[`ADR 0024`](adr/0024-signed-agent-approval-ledger.md) for the decision boundary.

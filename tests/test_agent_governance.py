"""Signed approval and append-only agent-run ledger contract tests."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from pydantic import ValidationError

from atlas_pulse.agent_governance import (
    AGENT_APPROVAL_CAVEAT,
    AGENT_APPROVAL_MAX_TTL,
    AgentApprovalGrant,
    Ed25519LedgerSigner,
    GovernanceActor,
    InMemoryAgentRunLedger,
    build_agent_approval,
    build_agent_approval_revocation,
    build_agent_preflight_record,
    build_signed_ledger_entry,
    key_id_for_public_key,
    key_id_from_public_pem,
    ledger_entry_from_json,
    ledger_entry_from_mapping,
    ledger_entry_json,
    resolve_agent_approval,
    verify_agent_run_ledger,
)

NOW = datetime(2026, 9, 14, 9, tzinfo=UTC)
PROPOSAL_ID = f"proposal-{'a' * 64}"
OTHER_PROPOSAL_ID = f"proposal-{'b' * 64}"
MANIFEST_ID = f"manifest-{'c' * 64}"


def _approval(
    *, issued_at: datetime = NOW, ttl: timedelta = timedelta(hours=1)
) -> AgentApprovalGrant:
    return build_agent_approval(
        proposal_id=PROPOSAL_ID,
        source_manifest_id=MANIFEST_ID,
        approver_id="github:12345",
        issued_at=issued_at,
        expires_at=issued_at + ttl,
        reason="Reviewed the exact evidence-bound proposal.",
    )


def test_approval_identity_covers_scope_actor_lifetime_and_reason() -> None:
    approval = _approval()
    repeated = _approval()
    offset = timezone(timedelta(hours=5, minutes=30))
    normalized = _approval(issued_at=NOW.astimezone(offset))
    changed = build_agent_approval(
        proposal_id=PROPOSAL_ID,
        source_manifest_id=MANIFEST_ID,
        approver_id="github:12345",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        reason="Reviewed the exact proposal with a second rationale.",
    )

    assert approval == repeated == normalized
    assert approval.approval_id.startswith("approval-")
    assert len(approval.approval_id) == 73
    assert approval.approval_id != changed.approval_id
    assert approval.approver == GovernanceActor(
        actor_id="github:12345", role="human_release_approver"
    )
    assert approval.caveat == AGENT_APPROVAL_CAVEAT


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"proposal_id": "proposal-short"}, "proposal_id"),
        ({"approver_id": "anonymous"}, "issuer-qualified"),
        ({"reason": "short"}, "8 to 1000"),
        ({"issued_at": datetime(2026, 9, 14, 9)}, "timezone-aware"),
        ({"ttl": timedelta(0)}, "later than issue"),
        ({"ttl": AGENT_APPROVAL_MAX_TTL + timedelta(seconds=1)}, "24 hours"),
    ],
)
def test_approval_rejects_unbounded_or_ambiguous_inputs(
    changes: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "proposal_id": PROPOSAL_ID,
        "source_manifest_id": MANIFEST_ID,
        "approver_id": "github:12345",
        "issued_at": NOW,
        "ttl": timedelta(hours=1),
        "reason": "Reviewed the exact evidence-bound proposal.",
    }
    values.update(changes)
    issued_at = values["issued_at"]
    ttl = values["ttl"]
    assert isinstance(issued_at, datetime)
    assert isinstance(ttl, timedelta)
    with pytest.raises((ValueError, ValidationError), match=message):
        build_agent_approval(
            proposal_id=str(values["proposal_id"]),
            source_manifest_id=str(values["source_manifest_id"]),
            approver_id=str(values["approver_id"]),
            issued_at=issued_at,
            expires_at=issued_at + ttl,
            reason=str(values["reason"]),
        )


def test_approval_model_rejects_tampering_and_unknown_fields() -> None:
    approval = _approval()
    payload = approval.model_dump(mode="python")
    payload["reason"] = "A different release reason."
    with pytest.raises(ValidationError, match="identity does not match"):
        AgentApprovalGrant.model_validate(payload)

    payload = approval.model_dump(mode="python")
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentApprovalGrant.model_validate(payload)


def test_ed25519_key_identity_and_pem_round_trip() -> None:
    signer = Ed25519LedgerSigner.generate()
    loaded = Ed25519LedgerSigner.from_private_pem(signer.private_key_pem())

    assert loaded.key_id == signer.key_id
    assert loaded.public_key_base64 == signer.public_key_base64
    assert key_id_from_public_pem(signer.public_key_pem()) == signer.key_id
    assert key_id_for_public_key(signer.public_key_bytes) == signer.key_id
    with pytest.raises(ValueError, match="32 raw bytes"):
        key_id_for_public_key(b"short")

    rsa_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    rsa_pem = rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    with pytest.raises(ValueError, match="must be an unencrypted Ed25519"):
        Ed25519LedgerSigner.from_private_pem(rsa_pem)

    encrypted = ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"not-a-runtime-secret"),
    )
    with pytest.raises(ValueError, match="must be an unencrypted Ed25519"):
        Ed25519LedgerSigner.from_private_pem(encrypted)


def test_signed_chain_round_trips_and_verifies_every_artifact() -> None:
    signer = Ed25519LedgerSigner.generate()
    approval = _approval()
    record = build_agent_preflight_record(
        manifest_id=MANIFEST_ID,
        proposal_id=PROPOSAL_ID,
        blocking_reasons=("model_adapter_not_selected", "execution_disabled"),
        recorder_id="github:12345",
        recorded_at=NOW - timedelta(minutes=1),
    )
    revocation = build_agent_approval_revocation(
        approval,
        revoker_id="github:67890",
        revoked_at=NOW + timedelta(minutes=10),
        reason="Release was withdrawn after operator review.",
    )
    first = build_signed_ledger_entry(record, sequence=1, previous_entry_id=None, signer=signer)
    second = build_signed_ledger_entry(
        approval,
        sequence=2,
        previous_entry_id=first.entry_id,
        signer=signer,
    )
    third = build_signed_ledger_entry(
        revocation,
        sequence=3,
        previous_entry_id=second.entry_id,
        signer=signer,
    )

    verification = verify_agent_run_ledger((first, second, third), trusted_key_ids={signer.key_id})

    assert verification.valid is True
    assert verification.entry_count == 3
    assert verification.head_entry_id == third.entry_id
    assert second.previous_entry_id == first.entry_id
    assert second.event_type == "approval_granted"
    assert third.event_type == "approval_revoked"
    assert ledger_entry_from_json(ledger_entry_json(second)) == second
    assert ledger_entry_from_mapping(json.loads(ledger_entry_json(second))) == second


def test_chain_verification_reports_order_duplicates_trust_and_tampering() -> None:
    signer = Ed25519LedgerSigner.generate()
    approval = _approval()
    first = build_signed_ledger_entry(approval, sequence=1, previous_entry_id=None, signer=signer)
    untrusted = verify_agent_run_ledger((first,), trusted_key_ids=set())
    assert untrusted.valid is False
    assert untrusted.errors == ("entry 1: signing key is not trusted",)

    bad_signature = first.model_copy(
        update={
            "signature": first.signature.model_copy(
                update={"value": base64.b64encode(b"x" * 64).decode()}
            )
        }
    )
    assert "signature is invalid" in verify_agent_run_ledger((bad_signature,)).errors[0]

    bad_metadata = first.model_copy(update={"artifact_id": f"approval-{'f' * 64}"})
    assert "metadata does not match" in verify_agent_run_ledger((bad_metadata,)).errors[0]

    bad_version = first.model_copy(update={"rule_version": "signed-agent-run-ledger-v0"})
    assert "contract version is not supported" in verify_agent_run_ledger((bad_version,)).errors[0]

    duplicated = verify_agent_run_ledger((first, first))
    assert duplicated.valid is False
    assert any("sequence is 1, expected 2" in error for error in duplicated.errors)
    assert any("previous entry does not match" in error for error in duplicated.errors)
    assert any("duplicate entry identity" in error for error in duplicated.errors)
    assert any("duplicate artifact identity" in error for error in duplicated.errors)


def test_signature_envelope_rejects_invalid_base64_key_id_and_length() -> None:
    signer = Ed25519LedgerSigner.generate()
    entry = build_signed_ledger_entry(
        _approval(), sequence=1, previous_entry_id=None, signer=signer
    )
    invalid_base64 = entry.model_copy(
        update={"signature": entry.signature.model_copy(update={"public_key": "***"})}
    )
    assert "canonical base64" in verify_agent_run_ledger((invalid_base64,)).errors[0]

    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    final_character = entry.signature.public_key[-2]
    noncanonical_character = alphabet[alphabet.index(final_character) + 1]
    noncanonical_base64 = entry.model_copy(
        update={
            "signature": entry.signature.model_copy(
                update={"public_key": f"{entry.signature.public_key[:-2]}{noncanonical_character}="}
            )
        }
    )
    assert "canonical base64" in verify_agent_run_ledger((noncanonical_base64,)).errors[0]

    wrong_key_id = entry.model_copy(
        update={"signature": entry.signature.model_copy(update={"key_id": f"ed25519-{'f' * 64}"})}
    )
    assert "key ID does not match" in verify_agent_run_ledger((wrong_key_id,)).errors[0]

    short_signature = entry.model_copy(
        update={
            "signature": entry.signature.model_copy(
                update={"value": base64.b64encode(b"short").decode()}
            )
        }
    )
    assert "exactly 64 bytes" in verify_agent_run_ledger((short_signature,)).errors[0]


def test_ledger_entry_rejects_an_invalid_genesis_boundary() -> None:
    signer = Ed25519LedgerSigner.generate()
    with pytest.raises(ValueError, match="only ledger sequence 1"):
        build_signed_ledger_entry(_approval(), sequence=2, previous_entry_id=None, signer=signer)
    with pytest.raises(ValueError, match="only ledger sequence 1"):
        build_signed_ledger_entry(
            _approval(),
            sequence=1,
            previous_entry_id=f"ledger-{'d' * 64}",
            signer=signer,
        )


def test_approval_resolution_is_time_scope_trust_and_revocation_aware() -> None:
    signer = Ed25519LedgerSigner.generate()
    approval = _approval()
    grant = build_signed_ledger_entry(approval, sequence=1, previous_entry_id=None, signer=signer)

    def resolve(
        evaluated_at: datetime,
        *,
        proposal_id: str = PROPOSAL_ID,
    ) -> str:
        return resolve_agent_approval(
            (grant,),
            approval_id=approval.approval_id,
            proposal_id=proposal_id,
            evaluated_at=evaluated_at,
            trusted_key_ids={signer.key_id},
        ).status

    assert resolve(NOW - timedelta(seconds=1)) == "not_yet_valid"
    active = resolve_agent_approval(
        (grant,),
        approval_id=approval.approval_id,
        proposal_id=PROPOSAL_ID,
        evaluated_at=NOW + timedelta(minutes=30),
        trusted_key_ids={signer.key_id},
    )
    assert active.status == "active"
    assert active.approver_id == "github:12345"
    assert active.signing_key_id == signer.key_id
    assert resolve(NOW + timedelta(hours=1)) == "expired"
    assert resolve(NOW, proposal_id=OTHER_PROPOSAL_ID) == "scope_mismatch"
    assert (
        resolve_agent_approval(
            (grant,),
            approval_id=approval.approval_id,
            proposal_id=PROPOSAL_ID,
            evaluated_at=NOW,
            trusted_key_ids=set(),
        ).status
        == "untrusted_signer"
    )
    assert (
        resolve_agent_approval(
            (),
            approval_id=approval.approval_id,
            proposal_id=PROPOSAL_ID,
            evaluated_at=NOW,
            trusted_key_ids={signer.key_id},
        ).status
        == "not_found"
    )

    revocation = build_agent_approval_revocation(
        approval,
        revoker_id="github:67890",
        revoked_at=NOW + timedelta(minutes=10),
        reason="Release withdrawn after a new evidence review.",
    )
    revoked_entry = build_signed_ledger_entry(
        revocation,
        sequence=2,
        previous_entry_id=grant.entry_id,
        signer=signer,
    )
    before = resolve_agent_approval(
        (grant, revoked_entry),
        approval_id=approval.approval_id,
        proposal_id=PROPOSAL_ID,
        evaluated_at=NOW + timedelta(minutes=5),
        trusted_key_ids={signer.key_id},
    )
    after = resolve_agent_approval(
        (grant, revoked_entry),
        approval_id=approval.approval_id,
        proposal_id=PROPOSAL_ID,
        evaluated_at=NOW + timedelta(minutes=10),
        trusted_key_ids={signer.key_id},
    )
    assert before.status == "active"
    assert after.status == "revoked"
    assert after.revocation_id == revocation.revocation_id


def test_resolution_fails_closed_for_a_broken_chain_or_ambiguous_revocation() -> None:
    signer = Ed25519LedgerSigner.generate()
    approval = _approval()
    grant = build_signed_ledger_entry(approval, sequence=1, previous_entry_id=None, signer=signer)
    broken = grant.model_copy(update={"artifact_id": f"approval-{'f' * 64}"})
    assert (
        resolve_agent_approval(
            (broken,),
            approval_id=approval.approval_id,
            proposal_id=PROPOSAL_ID,
            evaluated_at=NOW,
            trusted_key_ids={signer.key_id},
        ).status
        == "ledger_invalid"
    )

    first_revocation = build_agent_approval_revocation(
        approval,
        revoker_id="github:67890",
        revoked_at=NOW + timedelta(minutes=1),
        reason="First explicit release revocation.",
    )
    second_revocation = build_agent_approval_revocation(
        approval,
        revoker_id="github:67890",
        revoked_at=NOW + timedelta(minutes=2),
        reason="Second explicit release revocation.",
    )
    second = build_signed_ledger_entry(
        first_revocation,
        sequence=2,
        previous_entry_id=grant.entry_id,
        signer=signer,
    )
    third = build_signed_ledger_entry(
        second_revocation,
        sequence=3,
        previous_entry_id=second.entry_id,
        signer=signer,
    )
    assert (
        resolve_agent_approval(
            (grant, second, third),
            approval_id=approval.approval_id,
            proposal_id=PROPOSAL_ID,
            evaluated_at=NOW + timedelta(minutes=3),
            trusted_key_ids={signer.key_id},
        ).status
        == "ledger_invalid"
    )


def test_revocation_and_preflight_contracts_reject_invalid_inputs() -> None:
    approval = _approval()
    with pytest.raises(ValueError, match="cannot predate"):
        build_agent_approval_revocation(
            approval,
            revoker_id="github:67890",
            revoked_at=NOW - timedelta(seconds=1),
            reason="Withdraw the reviewed release.",
        )
    with pytest.raises(ValidationError, match="at least 1 item"):
        build_agent_preflight_record(
            manifest_id=MANIFEST_ID,
            proposal_id=PROPOSAL_ID,
            blocking_reasons=(),
            recorder_id="github:12345",
            recorded_at=NOW,
        )


async def test_in_memory_ledger_appends_resolves_and_rejects_duplicate_artifacts() -> None:
    signer = Ed25519LedgerSigner.generate()
    approval = _approval()
    ledger = InMemoryAgentRunLedger(trusted_key_ids={signer.key_id})

    entry = await ledger.append(approval, signer=signer)
    assert await ledger.entries() == (entry,)
    assert (
        await ledger.resolve_approval(
            approval_id=approval.approval_id,
            proposal_id=PROPOSAL_ID,
            evaluated_at=NOW + timedelta(minutes=1),
        )
    ).status == "active"
    with pytest.raises(ValueError, match="already exists"):
        await ledger.append(approval, signer=signer)
    await ledger.close()

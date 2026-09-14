"""Signed approval artifacts and a tamper-evident agent-run ledger."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Protocol, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atlas_pulse.agent_runs import (
    AGENT_AUTHORIZATION_POLICY_VERSION,
    AgentApprovalObservation,
    AgentApprovalStatus,
)
from atlas_pulse.identity import canonical_json_bytes, canonical_json_sha256

AGENT_APPROVAL_SCHEMA_VERSION = "1.0.0"
AGENT_APPROVAL_RULE_VERSION = "agent-approval-v1"
AGENT_APPROVAL_REVOCATION_RULE_VERSION = "agent-approval-revocation-v1"
AGENT_PREFLIGHT_RECORD_RULE_VERSION = "agent-preflight-record-v1"
AGENT_RUN_LEDGER_SCHEMA_VERSION = "1.0.0"
AGENT_RUN_LEDGER_RULE_VERSION = "signed-agent-run-ledger-v1"
AGENT_RUN_LEDGER_IDENTITY_ALGORITHM = "sha256-canonical-json-v1"
AGENT_RUN_LEDGER_SIGNATURE_ALGORITHM = "ed25519-v1"
AGENT_APPROVAL_MAX_TTL = timedelta(hours=24)
AGENT_APPROVAL_CAVEAT = (
    "This approval applies only to the exact proposal identity until expiry or revocation. "
    "It can satisfy only the human-release check and does not enable execution."
)
AGENT_REVOCATION_CAVEAT = (
    "Revocation is immutable and applies only to the referenced approval and proposal."
)
AGENT_PREFLIGHT_RECORD_CAVEAT = (
    "This ledger event records a no-execution preflight decision; it is not a run receipt."
)

GovernanceActorRole = Literal[
    "human_release_approver",
    "human_release_revoker",
    "preflight_recorder",
]
AgentLedgerEventType = Literal[
    "approval_granted",
    "approval_revoked",
    "preflight_recorded",
]

_ACTOR_ID = re.compile(r"^[a-z][a-z0-9+.-]*:[^\s:][^\s]{0,179}$")
_HEX = frozenset("0123456789abcdef")


class _ImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _prefixed_hash(value: str, prefix: str, field: str) -> str:
    suffix = value.removeprefix(prefix)
    if (
        len(suffix) != 64
        or not value.startswith(prefix)
        or any(char not in _HEX for char in suffix)
    ):
        raise ValueError(f"{field} must use {prefix}<sha256>")
    return value


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _reason(value: str, field: str = "reason") -> str:
    if value != value.strip() or not 8 <= len(value) <= 1_000:
        raise ValueError(f"{field} must contain 8 to 1000 non-whitespace characters")
    return value


class GovernanceActor(_ImmutableModel):
    """Issuer-qualified human identity and its explicit governance role."""

    actor_id: str
    role: GovernanceActorRole

    @field_validator("actor_id")
    @classmethod
    def validate_actor_id(cls, value: str) -> str:
        if not _ACTOR_ID.fullmatch(value):
            raise ValueError("actor_id must be an issuer-qualified value such as github:12345")
        return value


class AgentApprovalGrant(_ImmutableModel):
    """Content-addressed human release for one immutable run proposal."""

    artifact_type: Literal["approval"] = "approval"
    approval_id: str = Field(pattern=r"^approval-[0-9a-f]{64}$")
    schema_version: Literal["1.0.0"] = "1.0.0"
    rule_version: Literal["agent-approval-v1"] = "agent-approval-v1"
    identity_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{64}$")
    source_manifest_id: str = Field(pattern=r"^manifest-[0-9a-f]{64}$")
    policy_version: Literal["agent-authorization-v2"] = "agent-authorization-v2"
    approver: GovernanceActor
    issued_at: datetime
    expires_at: datetime
    reason: str
    caveat: Literal[
        "This approval applies only to the exact proposal identity until expiry or revocation. "
        "It can satisfy only the human-release check and does not enable execution."
    ] = (
        "This approval applies only to the exact proposal identity until expiry or revocation. "
        "It can satisfy only the human-release check and does not enable execution."
    )

    @field_validator("issued_at", "expires_at")
    @classmethod
    def validate_timestamp(cls, value: datetime, info: object) -> datetime:
        field = cast(str, getattr(info, "field_name", "timestamp"))
        return _utc(value, field)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _reason(value)

    @model_validator(mode="after")
    def validate_contract(self) -> AgentApprovalGrant:
        if self.approver.role != "human_release_approver":
            raise ValueError("approval actor must have the human_release_approver role")
        if self.expires_at <= self.issued_at:
            raise ValueError("approval expiry must be later than issue time")
        if self.expires_at - self.issued_at > AGENT_APPROVAL_MAX_TTL:
            raise ValueError("approval lifetime must not exceed 24 hours")
        expected = f"approval-{canonical_json_sha256(_approval_payload(self))}"
        if self.approval_id != expected:
            raise ValueError("approval identity does not match its canonical content")
        return self


class AgentApprovalRevocation(_ImmutableModel):
    """Content-addressed revocation of one exact approval."""

    artifact_type: Literal["revocation"] = "revocation"
    revocation_id: str = Field(pattern=r"^revocation-[0-9a-f]{64}$")
    schema_version: Literal["1.0.0"] = "1.0.0"
    rule_version: Literal["agent-approval-revocation-v1"] = "agent-approval-revocation-v1"
    identity_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    approval_id: str = Field(pattern=r"^approval-[0-9a-f]{64}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{64}$")
    source_manifest_id: str = Field(pattern=r"^manifest-[0-9a-f]{64}$")
    revoked_by: GovernanceActor
    revoked_at: datetime
    reason: str
    caveat: Literal[
        "Revocation is immutable and applies only to the referenced approval and proposal."
    ] = "Revocation is immutable and applies only to the referenced approval and proposal."

    @field_validator("revoked_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _utc(value, "revoked_at")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _reason(value)

    @model_validator(mode="after")
    def validate_contract(self) -> AgentApprovalRevocation:
        if self.revoked_by.role != "human_release_revoker":
            raise ValueError("revocation actor must have the human_release_revoker role")
        expected = f"revocation-{canonical_json_sha256(_revocation_payload(self))}"
        if self.revocation_id != expected:
            raise ValueError("revocation identity does not match its canonical content")
        return self


class AgentPreflightRecord(_ImmutableModel):
    """Timestamped ledger artifact proving a blocked preflight was observed."""

    artifact_type: Literal["preflight_record"] = "preflight_record"
    record_id: str = Field(pattern=r"^preflight-[0-9a-f]{64}$")
    schema_version: Literal["1.0.0"] = "1.0.0"
    rule_version: Literal["agent-preflight-record-v1"] = "agent-preflight-record-v1"
    identity_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    manifest_id: str = Field(pattern=r"^manifest-[0-9a-f]{64}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{64}$")
    decision: Literal["blocked"] = "blocked"
    blocking_reasons: tuple[str, ...] = Field(min_length=1)
    execution_status: Literal["not_started"] = "not_started"
    recorded_by: GovernanceActor
    recorded_at: datetime
    caveat: Literal[
        "This ledger event records a no-execution preflight decision; it is not a run receipt."
    ] = "This ledger event records a no-execution preflight decision; it is not a run receipt."

    @field_validator("recorded_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _utc(value, "recorded_at")

    @field_validator("blocking_reasons")
    @classmethod
    def validate_blocking_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not reason for reason in value):
            raise ValueError("blocking reasons cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> AgentPreflightRecord:
        if self.recorded_by.role != "preflight_recorder":
            raise ValueError("preflight actor must have the preflight_recorder role")
        expected = f"preflight-{canonical_json_sha256(_preflight_payload(self))}"
        if self.record_id != expected:
            raise ValueError("preflight record identity does not match its canonical content")
        return self


AgentGovernanceEvent = Annotated[
    AgentApprovalGrant | AgentApprovalRevocation | AgentPreflightRecord,
    Field(discriminator="artifact_type"),
]


class AgentRunLedgerSignature(_ImmutableModel):
    """Self-contained Ed25519 signature envelope for one ledger entry."""

    algorithm: Literal["ed25519-v1"] = "ed25519-v1"
    key_id: str = Field(pattern=r"^ed25519-[0-9a-f]{64}$")
    public_key: str
    value: str


class AgentRunLedgerEntry(_ImmutableModel):
    """One signed, hash-linked append-only governance event."""

    entry_id: str = Field(pattern=r"^ledger-[0-9a-f]{64}$")
    schema_version: Literal["1.0.0"] = "1.0.0"
    rule_version: Literal["signed-agent-run-ledger-v1"] = "signed-agent-run-ledger-v1"
    identity_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    sequence: int = Field(ge=1)
    previous_entry_id: str | None = Field(default=None, pattern=r"^ledger-[0-9a-f]{64}$")
    event_type: AgentLedgerEventType
    occurred_at: datetime
    artifact_id: str
    manifest_id: str = Field(pattern=r"^manifest-[0-9a-f]{64}$")
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{64}$")
    event: AgentGovernanceEvent
    signature: AgentRunLedgerSignature

    @field_validator("occurred_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _utc(value, "occurred_at")

    @model_validator(mode="after")
    def validate_contract(self) -> AgentRunLedgerEntry:
        _verify_entry(self)
        return self


@dataclass(frozen=True, slots=True)
class AgentRunLedgerVerification:
    """Complete chain, artifact, identity, and signature verification result."""

    valid: bool
    entry_count: int
    head_entry_id: str | None
    errors: tuple[str, ...]


class AgentRunLedgerReader(Protocol):
    """Read boundary used by preflight; no private signing material is required."""

    async def entries(self) -> tuple[AgentRunLedgerEntry, ...]: ...

    async def resolve_approval(
        self,
        *,
        approval_id: str,
        proposal_id: str,
        evaluated_at: datetime,
    ) -> AgentApprovalObservation: ...

    async def close(self) -> None: ...


class Ed25519LedgerSigner:
    """In-memory signing key whose stable ID is derived from its raw public key."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key

    @classmethod
    def generate(cls) -> Ed25519LedgerSigner:
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_pem(cls, value: bytes) -> Ed25519LedgerSigner:
        try:
            key = serialization.load_pem_private_key(value, password=None)
        except (TypeError, ValueError) as error:
            raise ValueError("private key must be an unencrypted Ed25519 PEM key") from error
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("private key must be an unencrypted Ed25519 PEM key")
        return cls(key)

    @property
    def public_key_bytes(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    @property
    def public_key_base64(self) -> str:
        return base64.b64encode(self.public_key_bytes).decode("ascii")

    @property
    def key_id(self) -> str:
        return key_id_for_public_key(self.public_key_bytes)

    def private_key_pem(self) -> bytes:
        return self._private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def public_key_pem(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def sign(self, payload: bytes) -> AgentRunLedgerSignature:
        return AgentRunLedgerSignature(
            key_id=self.key_id,
            public_key=self.public_key_base64,
            value=base64.b64encode(self._private_key.sign(payload)).decode("ascii"),
        )


def key_id_for_public_key(public_key: bytes) -> str:
    """Return the trust-anchor identity for raw Ed25519 public-key bytes."""
    if len(public_key) != 32:
        raise ValueError("Ed25519 public keys must contain exactly 32 raw bytes")
    return f"ed25519-{hashlib.sha256(public_key).hexdigest()}"


def key_id_from_public_pem(value: bytes) -> str:
    """Load an Ed25519 public PEM and return its content-derived key identity."""
    key = serialization.load_pem_public_key(value)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("public key must be an Ed25519 PEM key")
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key_id_for_public_key(raw)


def _model_payload(model: _ImmutableModel, *, identity_field: str) -> dict[str, object]:
    payload = model.model_dump(mode="python")
    payload.pop(identity_field)
    return payload


def _approval_payload(approval: AgentApprovalGrant) -> dict[str, object]:
    return _model_payload(approval, identity_field="approval_id")


def _revocation_payload(revocation: AgentApprovalRevocation) -> dict[str, object]:
    return _model_payload(revocation, identity_field="revocation_id")


def _preflight_payload(record: AgentPreflightRecord) -> dict[str, object]:
    return _model_payload(record, identity_field="record_id")


def build_agent_approval(
    *,
    proposal_id: str,
    source_manifest_id: str,
    approver_id: str,
    issued_at: datetime,
    expires_at: datetime,
    reason: str,
    policy_version: str = AGENT_AUTHORIZATION_POLICY_VERSION,
) -> AgentApprovalGrant:
    """Create a bounded approval whose identity covers actor, scope, and lifetime."""
    proposal_id = _prefixed_hash(proposal_id, "proposal-", "proposal_id")
    source_manifest_id = _prefixed_hash(source_manifest_id, "manifest-", "source_manifest_id")
    issued_at = _utc(issued_at, "issued_at")
    expires_at = _utc(expires_at, "expires_at")
    reason = _reason(reason.strip())
    payload: dict[str, object] = {
        "artifact_type": "approval",
        "schema_version": AGENT_APPROVAL_SCHEMA_VERSION,
        "rule_version": AGENT_APPROVAL_RULE_VERSION,
        "identity_algorithm": AGENT_RUN_LEDGER_IDENTITY_ALGORITHM,
        "proposal_id": proposal_id,
        "source_manifest_id": source_manifest_id,
        "policy_version": policy_version,
        "approver": GovernanceActor(
            actor_id=approver_id,
            role="human_release_approver",
        ).model_dump(mode="python"),
        "issued_at": issued_at,
        "expires_at": expires_at,
        "reason": reason,
        "caveat": AGENT_APPROVAL_CAVEAT,
    }
    return AgentApprovalGrant.model_validate(
        {"approval_id": f"approval-{canonical_json_sha256(payload)}", **payload}
    )


def build_agent_approval_revocation(
    approval: AgentApprovalGrant,
    *,
    revoker_id: str,
    revoked_at: datetime,
    reason: str,
) -> AgentApprovalRevocation:
    """Create an immutable revocation bound to the approval's exact scope."""
    revoked_at = _utc(revoked_at, "revoked_at")
    if revoked_at < approval.issued_at:
        raise ValueError("revocation cannot predate the approval")
    payload: dict[str, object] = {
        "artifact_type": "revocation",
        "schema_version": AGENT_APPROVAL_SCHEMA_VERSION,
        "rule_version": AGENT_APPROVAL_REVOCATION_RULE_VERSION,
        "identity_algorithm": AGENT_RUN_LEDGER_IDENTITY_ALGORITHM,
        "approval_id": approval.approval_id,
        "proposal_id": approval.proposal_id,
        "source_manifest_id": approval.source_manifest_id,
        "revoked_by": GovernanceActor(
            actor_id=revoker_id,
            role="human_release_revoker",
        ).model_dump(mode="python"),
        "revoked_at": revoked_at,
        "reason": _reason(reason.strip()),
        "caveat": AGENT_REVOCATION_CAVEAT,
    }
    return AgentApprovalRevocation.model_validate(
        {"revocation_id": f"revocation-{canonical_json_sha256(payload)}", **payload}
    )


def build_agent_preflight_record(
    *,
    manifest_id: str,
    proposal_id: str,
    blocking_reasons: Sequence[str],
    recorder_id: str,
    recorded_at: datetime,
) -> AgentPreflightRecord:
    """Create a content-addressed receipt for a blocked, zero-execution preflight."""
    reasons = tuple(blocking_reasons)
    payload: dict[str, object] = {
        "artifact_type": "preflight_record",
        "schema_version": AGENT_APPROVAL_SCHEMA_VERSION,
        "rule_version": AGENT_PREFLIGHT_RECORD_RULE_VERSION,
        "identity_algorithm": AGENT_RUN_LEDGER_IDENTITY_ALGORITHM,
        "manifest_id": _prefixed_hash(manifest_id, "manifest-", "manifest_id"),
        "proposal_id": _prefixed_hash(proposal_id, "proposal-", "proposal_id"),
        "decision": "blocked",
        "blocking_reasons": reasons,
        "execution_status": "not_started",
        "recorded_by": GovernanceActor(
            actor_id=recorder_id,
            role="preflight_recorder",
        ).model_dump(mode="python"),
        "recorded_at": _utc(recorded_at, "recorded_at"),
        "caveat": AGENT_PREFLIGHT_RECORD_CAVEAT,
    }
    return AgentPreflightRecord.model_validate(
        {"record_id": f"preflight-{canonical_json_sha256(payload)}", **payload}
    )


def _event_metadata(
    event: AgentGovernanceEvent,
) -> tuple[AgentLedgerEventType, datetime, str, str, str]:
    if isinstance(event, AgentApprovalGrant):
        return (
            "approval_granted",
            event.issued_at,
            event.approval_id,
            event.source_manifest_id,
            event.proposal_id,
        )
    if isinstance(event, AgentApprovalRevocation):
        return (
            "approval_revoked",
            event.revoked_at,
            event.revocation_id,
            event.source_manifest_id,
            event.proposal_id,
        )
    return (
        "preflight_recorded",
        event.recorded_at,
        event.record_id,
        event.manifest_id,
        event.proposal_id,
    )


def _entry_payload(
    *,
    sequence: int,
    previous_entry_id: str | None,
    event: AgentGovernanceEvent,
) -> dict[str, object]:
    event_type, occurred_at, artifact_id, manifest_id, proposal_id = _event_metadata(event)
    return {
        "schema_version": AGENT_RUN_LEDGER_SCHEMA_VERSION,
        "rule_version": AGENT_RUN_LEDGER_RULE_VERSION,
        "identity_algorithm": AGENT_RUN_LEDGER_IDENTITY_ALGORITHM,
        "sequence": sequence,
        "previous_entry_id": previous_entry_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "artifact_id": artifact_id,
        "manifest_id": manifest_id,
        "proposal_id": proposal_id,
        "event": event.model_dump(mode="python"),
    }


def build_signed_ledger_entry(
    event: AgentGovernanceEvent,
    *,
    sequence: int,
    previous_entry_id: str | None,
    signer: Ed25519LedgerSigner,
) -> AgentRunLedgerEntry:
    """Sign one event and bind it to the previous immutable ledger head."""
    if (sequence == 1) != (previous_entry_id is None):
        raise ValueError("only ledger sequence 1 may omit previous_entry_id")
    if previous_entry_id is not None:
        _prefixed_hash(previous_entry_id, "ledger-", "previous_entry_id")
    payload = _entry_payload(
        sequence=sequence,
        previous_entry_id=previous_entry_id,
        event=event,
    )
    signature = signer.sign(canonical_json_bytes(payload))
    signed = {**payload, "signature": signature.model_dump(mode="python")}
    return AgentRunLedgerEntry.model_validate(
        {"entry_id": f"ledger-{canonical_json_sha256(signed)}", **signed}
    )


def _decode_base64(value: str, field: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{field} must be canonical base64") from error
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError(f"{field} must be canonical base64")
    return decoded


def _verify_entry(entry: AgentRunLedgerEntry) -> None:
    if (
        entry.schema_version != AGENT_RUN_LEDGER_SCHEMA_VERSION
        or entry.rule_version != AGENT_RUN_LEDGER_RULE_VERSION
        or entry.identity_algorithm != AGENT_RUN_LEDGER_IDENTITY_ALGORITHM
        or entry.signature.algorithm != AGENT_RUN_LEDGER_SIGNATURE_ALGORITHM
    ):
        raise ValueError("ledger entry contract version is not supported")
    _prefixed_hash(entry.entry_id, "ledger-", "entry_id")
    if (entry.sequence == 1) != (entry.previous_entry_id is None):
        raise ValueError("only ledger sequence 1 may omit previous_entry_id")
    if entry.previous_entry_id is not None:
        _prefixed_hash(entry.previous_entry_id, "ledger-", "previous_entry_id")
    event_json = canonical_json_bytes(entry.event.model_dump(mode="python"))
    if isinstance(entry.event, AgentApprovalGrant):
        AgentApprovalGrant.model_validate_json(event_json)
    elif isinstance(entry.event, AgentApprovalRevocation):
        AgentApprovalRevocation.model_validate_json(event_json)
    else:
        AgentPreflightRecord.model_validate_json(event_json)
    if _event_metadata(entry.event) != (
        entry.event_type,
        entry.occurred_at,
        entry.artifact_id,
        entry.manifest_id,
        entry.proposal_id,
    ):
        raise ValueError("ledger metadata does not match its embedded event")
    payload = _entry_payload(
        sequence=entry.sequence,
        previous_entry_id=entry.previous_entry_id,
        event=entry.event,
    )
    public_key = _decode_base64(entry.signature.public_key, "signature.public_key")
    if entry.signature.key_id != key_id_for_public_key(public_key):
        raise ValueError("ledger signature key ID does not match its public key")
    signature = _decode_base64(entry.signature.value, "signature.value")
    if len(signature) != 64:
        raise ValueError("Ed25519 signatures must contain exactly 64 bytes")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            canonical_json_bytes(payload),
        )
    except InvalidSignature as error:
        raise ValueError("ledger entry signature is invalid") from error
    signed = {**payload, "signature": entry.signature.model_dump(mode="python")}
    if entry.entry_id != f"ledger-{canonical_json_sha256(signed)}":
        raise ValueError("ledger entry identity does not match its signed content")


def verify_agent_run_ledger(
    entries: Sequence[AgentRunLedgerEntry],
    *,
    trusted_key_ids: Collection[str] | None = None,
) -> AgentRunLedgerVerification:
    """Verify ordering, the hash chain, artifacts, signatures, and optional trust anchors."""
    errors: list[str] = []
    previous: str | None = None
    seen_entries: set[str] = set()
    seen_artifacts: set[str] = set()
    trusted = set(trusted_key_ids) if trusted_key_ids is not None else None
    for expected_sequence, entry in enumerate(entries, start=1):
        if entry.sequence != expected_sequence:
            errors.append(
                f"entry {expected_sequence}: sequence is {entry.sequence}, expected {expected_sequence}"
            )
        if entry.previous_entry_id != previous:
            errors.append(
                f"entry {expected_sequence}: previous entry does not match the chain head"
            )
        if entry.entry_id in seen_entries:
            errors.append(f"entry {expected_sequence}: duplicate entry identity")
        if entry.artifact_id in seen_artifacts:
            errors.append(f"entry {expected_sequence}: duplicate artifact identity")
        if trusted is not None and entry.signature.key_id not in trusted:
            errors.append(f"entry {expected_sequence}: signing key is not trusted")
        try:
            _verify_entry(entry)
        except ValueError as error:
            errors.append(f"entry {expected_sequence}: {error}")
        seen_entries.add(entry.entry_id)
        seen_artifacts.add(entry.artifact_id)
        previous = entry.entry_id
    return AgentRunLedgerVerification(
        valid=not errors,
        entry_count=len(entries),
        head_entry_id=entries[-1].entry_id if entries else None,
        errors=tuple(errors),
    )


def _observation(
    status: AgentApprovalStatus,
    *,
    approval_id: str,
    evaluated_at: datetime,
    approval: AgentApprovalGrant | None = None,
    signing_key_id: str | None = None,
    revocation_id: str | None = None,
) -> AgentApprovalObservation:
    return AgentApprovalObservation(
        status=status,
        approval_id=approval_id,
        approved_proposal_id=approval.proposal_id if approval else None,
        source_manifest_id=approval.source_manifest_id if approval else None,
        approver_id=approval.approver.actor_id if approval else None,
        signing_key_id=signing_key_id,
        issued_at=approval.issued_at if approval else None,
        expires_at=approval.expires_at if approval else None,
        revocation_id=revocation_id,
        evaluated_at=evaluated_at,
    )


def resolve_agent_approval(
    entries: Sequence[AgentRunLedgerEntry],
    *,
    approval_id: str,
    proposal_id: str,
    evaluated_at: datetime,
    trusted_key_ids: Collection[str],
) -> AgentApprovalObservation:
    """Resolve one approval from immutable events, failing closed on any ledger anomaly."""
    _prefixed_hash(approval_id, "approval-", "approval_id")
    _prefixed_hash(proposal_id, "proposal-", "proposal_id")
    evaluated_at = _utc(evaluated_at, "evaluated_at")
    if not verify_agent_run_ledger(entries).valid:
        return _observation("ledger_invalid", approval_id=approval_id, evaluated_at=evaluated_at)
    trusted = set(trusted_key_ids)
    if any(entry.signature.key_id not in trusted for entry in entries):
        return _observation("untrusted_signer", approval_id=approval_id, evaluated_at=evaluated_at)
    grants = [
        entry
        for entry in entries
        if isinstance(entry.event, AgentApprovalGrant) and entry.event.approval_id == approval_id
    ]
    if not grants:
        return _observation("not_found", approval_id=approval_id, evaluated_at=evaluated_at)
    if len(grants) != 1:
        return _observation("ledger_invalid", approval_id=approval_id, evaluated_at=evaluated_at)
    grant_entry = grants[0]
    approval = cast(AgentApprovalGrant, grant_entry.event)
    if approval.proposal_id != proposal_id:
        return _observation(
            "scope_mismatch",
            approval_id=approval_id,
            evaluated_at=evaluated_at,
            approval=approval,
            signing_key_id=grant_entry.signature.key_id,
        )
    revocations = [
        entry
        for entry in entries
        if isinstance(entry.event, AgentApprovalRevocation)
        and entry.event.approval_id == approval_id
    ]
    if len(revocations) > 1:
        return _observation("ledger_invalid", approval_id=approval_id, evaluated_at=evaluated_at)
    if revocations:
        revocation = cast(AgentApprovalRevocation, revocations[0].event)
        if (
            revocation.proposal_id != approval.proposal_id
            or revocation.source_manifest_id != approval.source_manifest_id
            or revocation.revoked_at < approval.issued_at
        ):
            return _observation(
                "ledger_invalid", approval_id=approval_id, evaluated_at=evaluated_at
            )
        if revocation.revoked_at <= evaluated_at:
            return _observation(
                "revoked",
                approval_id=approval_id,
                evaluated_at=evaluated_at,
                approval=approval,
                signing_key_id=grant_entry.signature.key_id,
                revocation_id=revocation.revocation_id,
            )
    status: AgentApprovalStatus
    if evaluated_at < approval.issued_at:
        status = "not_yet_valid"
    elif evaluated_at >= approval.expires_at:
        status = "expired"
    else:
        status = "active"
    return _observation(
        status,
        approval_id=approval_id,
        evaluated_at=evaluated_at,
        approval=approval,
        signing_key_id=grant_entry.signature.key_id,
    )


class InMemoryAgentRunLedger:
    """Deterministic ledger implementation for contracts and API tests."""

    def __init__(self, *, trusted_key_ids: Collection[str] = ()) -> None:
        self._entries: list[AgentRunLedgerEntry] = []
        self._trusted_key_ids = frozenset(trusted_key_ids)

    async def append(
        self,
        event: AgentGovernanceEvent,
        *,
        signer: Ed25519LedgerSigner,
    ) -> AgentRunLedgerEntry:
        entry = build_signed_ledger_entry(
            event,
            sequence=len(self._entries) + 1,
            previous_entry_id=self._entries[-1].entry_id if self._entries else None,
            signer=signer,
        )
        if any(existing.artifact_id == entry.artifact_id for existing in self._entries):
            raise ValueError("ledger artifact identity already exists")
        self._entries.append(entry)
        return entry

    async def entries(self) -> tuple[AgentRunLedgerEntry, ...]:
        return tuple(self._entries)

    async def resolve_approval(
        self,
        *,
        approval_id: str,
        proposal_id: str,
        evaluated_at: datetime,
    ) -> AgentApprovalObservation:
        return resolve_agent_approval(
            self._entries,
            approval_id=approval_id,
            proposal_id=proposal_id,
            evaluated_at=evaluated_at,
            trusted_key_ids=self._trusted_key_ids,
        )

    async def close(self) -> None:
        return None


def ledger_entry_json(entry: AgentRunLedgerEntry) -> str:
    """Serialize an entry in the same canonical form covered by its identity."""
    return canonical_json_bytes(entry.model_dump(mode="python")).decode("utf-8")


def ledger_entry_from_mapping(value: Mapping[str, object]) -> AgentRunLedgerEntry:
    """Strictly parse and verify one stored JSON entry."""
    return AgentRunLedgerEntry.model_validate_json(
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    )


def ledger_entry_from_json(value: str | bytes) -> AgentRunLedgerEntry:
    """Strictly decode and verify a JSON ledger entry."""
    return AgentRunLedgerEntry.model_validate_json(value)

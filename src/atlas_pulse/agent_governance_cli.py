"""Operator CLI for the signed agent-run governance ledger."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from sqlalchemy.exc import SQLAlchemyError

from atlas_pulse.agent_governance import (
    AgentApprovalGrant,
    Ed25519LedgerSigner,
    build_agent_approval,
    build_agent_approval_revocation,
    build_agent_preflight_record,
    key_id_from_public_pem,
    verify_agent_run_ledger,
)
from atlas_pulse.agent_ledger import PostgresAgentRunLedger
from atlas_pulse.agent_runs import (
    AGENT_AUTHORIZATION_POLICY_VERSION,
    AGENT_RUN_IDENTITY_ALGORITHM,
    AGENT_RUN_PROPOSAL_RULE_VERSION,
    AGENT_RUN_RULE_VERSION,
    AGENT_RUN_SCHEMA_VERSION,
)
from atlas_pulse.identity import canonical_json_sha256

_DEFAULT_DATABASE_URL = "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas"


@dataclass(frozen=True, slots=True)
class _ManifestReference:
    manifest_id: str
    proposal_id: str
    policy_version: str
    blocking_reasons: tuple[str, ...]


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be a JSON object")
    return cast(dict[str, object], value)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _manifest_reference(path: Path) -> _ManifestReference:
    try:
        root = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"cannot read preflight JSON: {error}") from error
    document = _object(root, "preflight")
    manifest = _object(document.get("manifest", document), "manifest")
    manifest_id = _string(manifest.get("manifest_id"), "manifest_id")
    payload = {key: value for key, value in manifest.items() if key != "manifest_id"}
    if manifest_id != f"manifest-{canonical_json_sha256(payload)}":
        raise ValueError("manifest identity does not match its canonical content")
    if manifest.get("schema_version") != AGENT_RUN_SCHEMA_VERSION:
        raise ValueError("manifest schema version is not supported")
    if manifest.get("rule_version") != AGENT_RUN_RULE_VERSION:
        raise ValueError("manifest rule version is not supported")
    if manifest.get("identity_algorithm") != AGENT_RUN_IDENTITY_ALGORITHM:
        raise ValueError("manifest identity algorithm is not supported")
    if manifest.get("status") != "blocked":
        raise ValueError("only blocked, no-execution manifests can enter this ledger")
    request = _object(manifest.get("request"), "manifest.request")
    evidence = _object(manifest.get("evidence"), "manifest.evidence")
    policy = _object(manifest.get("policy"), "manifest.policy")
    release = _object(manifest.get("release"), "manifest.release")
    proposal_id = _string(manifest.get("proposal_id"), "proposal_id")
    proposal_payload = {
        "schema_version": AGENT_RUN_SCHEMA_VERSION,
        "manifest_rule_version": AGENT_RUN_RULE_VERSION,
        "proposal_rule_version": AGENT_RUN_PROPOSAL_RULE_VERSION,
        "identity_algorithm": AGENT_RUN_IDENTITY_ALGORITHM,
        "request": request,
        "evidence": evidence,
        "policy": policy,
        "release": release,
    }
    if proposal_id != f"proposal-{canonical_json_sha256(proposal_payload)}":
        raise ValueError("proposal identity does not match the manifest scope")
    if policy.get("execution_enabled") is not False:
        raise ValueError("manifest policy must keep execution disabled")
    policy_version = _string(policy.get("policy_version"), "policy.policy_version")
    if policy_version != AGENT_AUTHORIZATION_POLICY_VERSION:
        raise ValueError("manifest authorization policy is not supported")
    execution = _object(manifest.get("execution"), "manifest.execution")
    if execution != {
        "status": "not_started",
        "agent_model_invoked": False,
        "agent_network_accessed": False,
        "agent_tools_invoked": False,
        "answer_generated": False,
        "agent_side_effects_performed": False,
    }:
        raise ValueError("manifest execution state must remain exactly not_started")
    authorization = _object(manifest.get("authorization"), "manifest.authorization")
    reasons = authorization.get("blocking_reasons")
    if (
        not isinstance(reasons, list)
        or not reasons
        or any(not isinstance(reason, str) or not reason for reason in reasons)
    ):
        raise ValueError("manifest must contain non-empty blocking reasons")
    return _ManifestReference(
        manifest_id=manifest_id,
        proposal_id=proposal_id,
        policy_version=policy_version,
        blocking_reasons=tuple(cast(list[str], reasons)),
    )


def _signer(path: Path) -> Ed25519LedgerSigner:
    try:
        mode = path.stat().st_mode
        if mode & 0o077:
            raise ValueError("private key permissions must not allow group or world access")
        return Ed25519LedgerSigner.from_private_pem(path.read_bytes())
    except OSError as error:
        raise ValueError(f"cannot read private key: {error}") from error


def _trusted_key_ids(paths: list[Path]) -> frozenset[str]:
    try:
        return frozenset(key_id_from_public_pem(path.read_bytes()) for path in paths)
    except OSError as error:
        raise ValueError(f"cannot read trusted public key: {error}") from error


def _write_new(path: Path, value: bytes, *, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(value)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _keygen(private_path: Path, public_path: Path) -> dict[str, object]:
    if private_path == public_path:
        raise ValueError("private and public key paths must differ")
    if private_path.exists() or public_path.exists():
        raise ValueError("key generation refuses to overwrite an existing path")
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    signer = Ed25519LedgerSigner.generate()
    _write_new(private_path, signer.private_key_pem(), mode=0o600)
    try:
        _write_new(public_path, signer.public_key_pem(), mode=0o644)
    except BaseException:
        private_path.unlink(missing_ok=True)
        raise
    return {
        "key_id": signer.key_id,
        "private_key": str(private_path),
        "public_key": str(public_path),
        "private_key_permissions": "0600",
    }


def _database_url(args: argparse.Namespace) -> str:
    return cast(str, args.database_url)


def _ttl_minutes(value: str) -> int:
    try:
        minutes = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("TTL must be an integer from 1 to 1440") from error
    if not 1 <= minutes <= 1_440:
        raise argparse.ArgumentTypeError("TTL must be an integer from 1 to 1440")
    return minutes


async def _grant(args: argparse.Namespace) -> dict[str, object]:
    reference = _manifest_reference(args.preflight)
    signer = _signer(args.private_key)
    issued_at = datetime.now(UTC)
    approval = build_agent_approval(
        proposal_id=reference.proposal_id,
        source_manifest_id=reference.manifest_id,
        policy_version=reference.policy_version,
        approver_id=args.actor_id,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=args.ttl_minutes),
        reason=args.reason,
    )
    ledger = PostgresAgentRunLedger(database_url=_database_url(args))
    try:
        entry = await ledger.append(approval, signer=signer)
    finally:
        await ledger.close()
    return entry.model_dump(mode="python")


async def _revoke(args: argparse.Namespace) -> dict[str, object]:
    signer = _signer(args.private_key)
    ledger = PostgresAgentRunLedger(database_url=_database_url(args))
    try:
        entries = await ledger.entries()
        grants = [
            entry.event
            for entry in entries
            if isinstance(entry.event, AgentApprovalGrant)
            and entry.event.approval_id == args.approval_id
        ]
        if len(grants) != 1:
            raise ValueError("approval ID must identify exactly one ledger grant")
        revocation = build_agent_approval_revocation(
            grants[0],
            revoker_id=args.actor_id,
            revoked_at=datetime.now(UTC),
            reason=args.reason,
        )
        entry = await ledger.append(revocation, signer=signer)
    finally:
        await ledger.close()
    return entry.model_dump(mode="python")


async def _record_preflight(args: argparse.Namespace) -> dict[str, object]:
    reference = _manifest_reference(args.preflight)
    signer = _signer(args.private_key)
    record = build_agent_preflight_record(
        manifest_id=reference.manifest_id,
        proposal_id=reference.proposal_id,
        blocking_reasons=reference.blocking_reasons,
        recorder_id=args.actor_id,
        recorded_at=datetime.now(UTC),
    )
    ledger = PostgresAgentRunLedger(database_url=_database_url(args))
    try:
        entry = await ledger.append(record, signer=signer)
    finally:
        await ledger.close()
    return entry.model_dump(mode="python")


async def _verify(args: argparse.Namespace) -> tuple[dict[str, object], bool]:
    trusted = _trusted_key_ids(args.trusted_public_key)
    ledger = PostgresAgentRunLedger(database_url=_database_url(args))
    try:
        entries = await ledger.entries()
    finally:
        await ledger.close()
    result = verify_agent_run_ledger(
        entries,
        trusted_key_ids=trusted if args.trusted_public_key else None,
    )
    return asdict(result), result.valid


async def _status(args: argparse.Namespace) -> dict[str, object]:
    trusted = _trusted_key_ids(args.trusted_public_key)
    ledger = PostgresAgentRunLedger(
        database_url=_database_url(args),
        trusted_key_ids=trusted,
    )
    try:
        observation = await ledger.resolve_approval(
            approval_id=args.approval_id,
            proposal_id=args.proposal_id,
            evaluated_at=datetime.now(UTC),
        )
    finally:
        await ledger.close()
    return asdict(observation)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-pulse-govern-agent-runs",
        description="Manage the signed, no-execution AtlasPulse agent-run ledger.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    keygen = subparsers.add_parser("keygen", help="generate an Ed25519 governance key pair")
    keygen.add_argument("--private-key", type=Path, required=True)
    keygen.add_argument("--public-key", type=Path, required=True)

    def database(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--database-url",
            default=os.getenv("ATLAS_DATABASE_URL", _DEFAULT_DATABASE_URL),
        )

    def signing(command: argparse.ArgumentParser) -> None:
        command.add_argument("--private-key", type=Path, required=True)
        command.add_argument("--actor-id", required=True)

    grant = subparsers.add_parser("grant", help="append a proposal-scoped approval")
    database(grant)
    signing(grant)
    grant.add_argument("--preflight", type=Path, required=True)
    grant.add_argument(
        "--ttl-minutes",
        type=_ttl_minutes,
        default=60,
        metavar="MINUTES",
        help="approval lifetime from 1 to 1440 minutes (default: 60)",
    )
    grant.add_argument("--reason", required=True)

    revoke = subparsers.add_parser("revoke", help="append an immutable approval revocation")
    database(revoke)
    signing(revoke)
    revoke.add_argument("--approval-id", required=True)
    revoke.add_argument("--reason", required=True)

    record = subparsers.add_parser(
        "record-preflight", help="append a blocked no-execution preflight receipt"
    )
    database(record)
    signing(record)
    record.add_argument("--preflight", type=Path, required=True)

    verify = subparsers.add_parser("verify", help="verify the entire signed hash chain")
    database(verify)
    verify.add_argument("--trusted-public-key", action="append", type=Path, default=[])

    status = subparsers.add_parser("status", help="resolve approval state for one proposal")
    database(status)
    status.add_argument("--approval-id", required=True)
    status.add_argument("--proposal-id", required=True)
    status.add_argument("--trusted-public-key", action="append", type=Path, required=True)
    return parser


async def _run(args: argparse.Namespace) -> int:
    if args.command == "keygen":
        result = _keygen(args.private_key, args.public_key)
        valid = True
    elif args.command == "grant":
        result = await _grant(args)
        valid = True
    elif args.command == "revoke":
        result = await _revoke(args)
        valid = True
    elif args.command == "record-preflight":
        result = await _record_preflight(args)
        valid = True
    elif args.command == "verify":
        result, valid = await _verify(args)
    else:
        result = await _status(args)
        valid = True
    print(
        json.dumps(
            result,
            sort_keys=True,
            indent=2,
            default=lambda value: (
                value.isoformat().replace("+00:00", "Z")
                if isinstance(value, datetime)
                else str(value)
            ),
        )
    )
    return 0 if valid else 1


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Run one governance command and map operational failures to a stable exit code."""
    parser = _parser()
    try:
        return asyncio.run(_run(parser.parse_args(argv)))
    except (OSError, RuntimeError, SQLAlchemyError, ValueError) as error:
        print(f"agent governance failed: {error}", file=sys.stderr)
        return 2


def main() -> None:
    """Installed signed-governance entry point."""
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()

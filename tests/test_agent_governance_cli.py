"""Offline signed-governance CLI tests."""

from __future__ import annotations

import json
import os
from argparse import Namespace
from dataclasses import asdict
from pathlib import Path
from typing import cast

import pytest

import atlas_pulse.agent_governance_cli as cli
from atlas_pulse.agent_governance import Ed25519LedgerSigner, InMemoryAgentRunLedger
from atlas_pulse.agent_runs import build_agent_run_manifest
from atlas_pulse.evidence_packs import build_evidence_pack
from atlas_pulse.identity import canonical_json_bytes
from atlas_pulse.retrieval import SearchQuery, SearchResult
from atlas_pulse.retrieval.ranking import RANKING_RULE, RETRIEVAL_CAVEAT


def _preflight(path: Path) -> tuple[str, str]:
    pack = build_evidence_pack(
        SearchResult(
            hits=(),
            candidates_considered=0,
            embedding_model="test/local",
            ranking_mode="hybrid",
            ranking_rule=RANKING_RULE,
            caveat=RETRIEVAL_CAVEAT,
        ),
        SearchQuery(text="missing disruption"),
    )
    manifest = build_agent_run_manifest(pack)
    path.write_bytes(canonical_json_bytes({"manifest": asdict(manifest)}))
    return manifest.manifest_id, manifest.proposal_id


def test_manifest_reference_verifies_manifest_proposal_and_no_execution(tmp_path: Path) -> None:
    path = tmp_path / "preflight.json"
    manifest_id, proposal_id = _preflight(path)

    reference = cli._manifest_reference(path)

    assert reference.manifest_id == manifest_id
    assert reference.proposal_id == proposal_id
    assert reference.blocking_reasons[-1] == "execution_disabled"

    document = json.loads(path.read_bytes())
    document["manifest"]["execution"]["answer_generated"] = True
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="manifest identity does not match"):
        cli._manifest_reference(path)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (b"not-json", "cannot read preflight JSON"),
        (b"[]", "preflight must be a JSON object"),
    ],
)
def test_manifest_reference_rejects_invalid_documents(
    tmp_path: Path, document: bytes, message: str
) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(document)
    with pytest.raises(ValueError, match=message):
        cli._manifest_reference(path)


def test_keygen_is_non_overwriting_private_and_content_addressed(tmp_path: Path) -> None:
    private_path = tmp_path / "keys" / "operator.pem"
    public_path = tmp_path / "keys" / "operator.pub.pem"

    result = cli._keygen(private_path, public_path)

    assert result["key_id"] == next(iter(cli._trusted_key_ids([public_path])))
    assert oct(private_path.stat().st_mode & 0o777) == "0o600"
    assert oct(public_path.stat().st_mode & 0o777) == "0o644"
    assert cli._signer(private_path).key_id == result["key_id"]
    with pytest.raises(ValueError, match="refuses to overwrite"):
        cli._keygen(private_path, public_path)
    with pytest.raises(ValueError, match="paths must differ"):
        cli._keygen(tmp_path / "same.pem", tmp_path / "same.pem")


def test_private_key_loader_rejects_loose_permissions(tmp_path: Path) -> None:
    path = tmp_path / "operator.pem"
    path.write_bytes(Ed25519LedgerSigner.generate().private_key_pem())
    os.chmod(path, 0o644)

    with pytest.raises(ValueError, match="permissions"):
        cli._signer(path)


async def test_cli_grant_record_revoke_verify_and_status_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preflight_path = tmp_path / "preflight.json"
    _, proposal_id = _preflight(preflight_path)
    private_path = tmp_path / "operator.pem"
    public_path = tmp_path / "operator.pub.pem"
    key = cli._keygen(private_path, public_path)
    ledger = InMemoryAgentRunLedger(trusted_key_ids={str(key["key_id"])})
    monkeypatch.setattr(cli, "PostgresAgentRunLedger", lambda **_kwargs: ledger)
    common = {
        "database_url": "postgresql+asyncpg://unused",
        "private_key": private_path,
        "actor_id": "github:12345",
    }

    granted = await cli._grant(
        Namespace(
            **common,
            preflight=preflight_path,
            ttl_minutes=60,
            reason="Reviewed the exact evidence-bound proposal.",
        )
    )
    grant_event = cast(dict[str, object], granted["event"])
    approval_id = cast(str, grant_event["approval_id"])
    assert granted["event_type"] == "approval_granted"

    recorded = await cli._record_preflight(Namespace(**common, preflight=preflight_path))
    assert recorded["event_type"] == "preflight_recorded"

    verified, valid = await cli._verify(
        Namespace(
            database_url="postgresql+asyncpg://unused",
            trusted_public_key=[public_path],
        )
    )
    assert valid is True
    assert verified["entry_count"] == 2

    status = await cli._status(
        Namespace(
            database_url="postgresql+asyncpg://unused",
            approval_id=approval_id,
            proposal_id=proposal_id,
            trusted_public_key=[public_path],
        )
    )
    assert status["status"] == "active"

    revoked = await cli._revoke(
        Namespace(
            **common,
            approval_id=approval_id,
            reason="Release withdrawn after a new evidence review.",
        )
    )
    assert revoked["event_type"] == "approval_revoked"


async def test_run_dispatch_prints_key_metadata(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_path = tmp_path / "operator.pem"
    public_path = tmp_path / "operator.pub.pem"
    status = await cli._run(
        Namespace(command="keygen", private_key=private_path, public_key=public_path)
    )

    assert status == 0
    output = json.loads(capsys.readouterr().out)
    assert output["key_id"].startswith("ed25519-")


def test_parser_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit):
        cli._parser().parse_args([])
    with pytest.raises(SystemExit):
        cli._parser().parse_args(
            [
                "grant",
                "--private-key",
                "key.pem",
                "--actor-id",
                "github:1",
                "--preflight",
                "preflight.json",
                "--ttl-minutes",
                "1441",
                "--reason",
                "Long enough reason.",
            ]
        )


def test_run_cli_maps_validation_failures_to_a_stable_exit_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = cli.run_cli(
        [
            "keygen",
            "--private-key",
            str(tmp_path / "same.pem"),
            "--public-key",
            str(tmp_path / "same.pem"),
        ]
    )

    assert status == 2
    assert (
        "agent governance failed: private and public key paths must differ"
        in capsys.readouterr().err
    )

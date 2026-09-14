"""Real PostgreSQL proof for append-only signed agent-run ledger behavior."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from atlas_pulse.agent_governance import (
    Ed25519LedgerSigner,
    build_agent_approval,
    build_signed_ledger_entry,
    verify_agent_run_ledger,
)
from atlas_pulse.agent_ledger import _INSERT_ENTRY, PostgresAgentRunLedger
from atlas_pulse.identity import canonical_json_bytes

DATABASE_URL = os.getenv("ATLAS_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(DATABASE_URL is None, reason="ATLAS_TEST_DATABASE_URL is not set")
async def test_signed_ledger_is_durable_verifiable_and_database_append_only() -> None:
    assert DATABASE_URL is not None
    signer = Ed25519LedgerSigner.generate()
    now = datetime.now(UTC)
    nonce = hashlib.sha256(f"{now.isoformat()}:{signer.key_id}".encode()).hexdigest()
    approval = build_agent_approval(
        proposal_id=f"proposal-{nonce}",
        source_manifest_id=f"manifest-{nonce}",
        approver_id=f"integration:{nonce[:16]}",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        reason="Integration proof for immutable signed ledger persistence.",
    )
    ledger = PostgresAgentRunLedger(
        database_url=DATABASE_URL,
        trusted_key_ids={signer.key_id},
    )
    engine = create_async_engine(DATABASE_URL)
    try:
        entry = await ledger.append(approval, signer=signer)
        entries = await ledger.entries()
        verification = verify_agent_run_ledger(entries)
        assert verification.valid is True
        assert verification.head_entry_id == entry.entry_id

        with pytest.raises(DBAPIError, match="append-only"):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE agent_run_ledger_entries "
                        "SET event_type = event_type WHERE entry_id = :entry_id"
                    ),
                    {"entry_id": entry.entry_id},
                )
        with pytest.raises(DBAPIError, match="append-only"):
            async with engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM agent_run_ledger_entries WHERE entry_id = :entry_id"),
                    {"entry_id": entry.entry_id},
                )
        with pytest.raises(DBAPIError, match="append-only"):
            async with engine.begin() as connection:
                await connection.execute(text("TRUNCATE TABLE agent_run_ledger_entries"))

        second_approval = build_agent_approval(
            proposal_id=f"proposal-{nonce}",
            source_manifest_id=f"manifest-{nonce}",
            approver_id=f"integration:{nonce[:16]}",
            issued_at=now,
            expires_at=now + timedelta(minutes=4),
            reason="Second integration artifact for relational binding proof.",
        )
        second_entry = build_signed_ledger_entry(
            second_approval,
            sequence=entry.sequence + 1,
            previous_entry_id=entry.entry_id,
            signer=signer,
        )
        with pytest.raises(DBAPIError, match="ck_agent_run_ledger_json_columns"):
            async with engine.begin() as connection:
                await connection.execute(
                    text(_INSERT_ENTRY),
                    {
                        "sequence_no": second_entry.sequence,
                        "entry_id": f"ledger-{'f' * 64}",
                        "previous_entry_id": second_entry.previous_entry_id,
                        "event_type": second_entry.event_type,
                        "occurred_at": second_entry.occurred_at,
                        "artifact_id": second_entry.artifact_id,
                        "manifest_id": second_entry.manifest_id,
                        "proposal_id": second_entry.proposal_id,
                        "signing_key_id": second_entry.signature.key_id,
                        "entry_json": canonical_json_bytes(
                            second_entry.model_dump(mode="python")
                        ).decode("utf-8"),
                    },
                )

        async with engine.connect() as connection:
            retained = await connection.scalar(
                text("SELECT count(*) FROM agent_run_ledger_entries WHERE entry_id = :entry_id"),
                {"entry_id": entry.entry_id},
            )
        assert retained == 1
    finally:
        await ledger.close()
        await engine.dispose()

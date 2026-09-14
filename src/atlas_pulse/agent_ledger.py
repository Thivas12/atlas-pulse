"""PostgreSQL persistence for the signed append-only agent-run ledger."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import cast

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from atlas_pulse.agent_governance import (
    AgentGovernanceEvent,
    AgentRunLedgerEntry,
    Ed25519LedgerSigner,
    build_signed_ledger_entry,
    ledger_entry_from_json,
    ledger_entry_from_mapping,
    resolve_agent_approval,
    verify_agent_run_ledger,
)
from atlas_pulse.agent_runs import AgentApprovalObservation
from atlas_pulse.identity import canonical_json_bytes

_LEDGER_LOCK_ID = 4_186_248_635_831_251_449

_INSERT_ENTRY = """
INSERT INTO agent_run_ledger_entries (
    sequence_no,
    entry_id,
    previous_entry_id,
    event_type,
    occurred_at,
    artifact_id,
    manifest_id,
    proposal_id,
    signing_key_id,
    entry_json
) VALUES (
    :sequence_no,
    :entry_id,
    :previous_entry_id,
    :event_type,
    :occurred_at,
    :artifact_id,
    :manifest_id,
    :proposal_id,
    :signing_key_id,
    CAST(:entry_json AS jsonb)
)
"""


def _entry_from_database(value: object) -> AgentRunLedgerEntry:
    if isinstance(value, bytes | str):
        return ledger_entry_from_json(value)
    if isinstance(value, Mapping):
        return ledger_entry_from_mapping(cast(Mapping[str, object], value))
    raise ValueError("ledger entry JSON must be an object, string, or bytes")


class PostgresAgentRunLedger:
    """Serialized appends plus verified reads over the immutable PostgreSQL table."""

    def __init__(
        self,
        *,
        database_url: str,
        trusted_key_ids: Collection[str] = (),
        engine: AsyncEngine | None = None,
    ) -> None:
        self._owns_engine = engine is None
        self._engine = engine or create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
        )
        self._trusted_key_ids = frozenset(trusted_key_ids)

    async def append(
        self,
        event: AgentGovernanceEvent,
        *,
        signer: Ed25519LedgerSigner,
    ) -> AgentRunLedgerEntry:
        """Lock the single chain, verify its head, and atomically append one signed event."""
        async with self._engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _LEDGER_LOCK_ID},
            )
            rows = (
                await connection.execute(
                    text("SELECT entry_json FROM agent_run_ledger_entries ORDER BY sequence_no")
                )
            ).scalars()
            entries = tuple(_entry_from_database(row) for row in rows)
            verification = verify_agent_run_ledger(entries)
            if not verification.valid:
                raise ValueError(
                    "cannot append to an invalid agent-run ledger: "
                    + "; ".join(verification.errors)
                )
            entry = build_signed_ledger_entry(
                event,
                sequence=len(entries) + 1,
                previous_entry_id=entries[-1].entry_id if entries else None,
                signer=signer,
            )
            await connection.execute(
                text(_INSERT_ENTRY),
                {
                    "sequence_no": entry.sequence,
                    "entry_id": entry.entry_id,
                    "previous_entry_id": entry.previous_entry_id,
                    "event_type": entry.event_type,
                    "occurred_at": entry.occurred_at,
                    "artifact_id": entry.artifact_id,
                    "manifest_id": entry.manifest_id,
                    "proposal_id": entry.proposal_id,
                    "signing_key_id": entry.signature.key_id,
                    "entry_json": canonical_json_bytes(entry.model_dump(mode="python")).decode(
                        "utf-8"
                    ),
                },
            )
            return entry

    async def entries(self) -> tuple[AgentRunLedgerEntry, ...]:
        async with self._engine.connect() as connection:
            rows = (
                await connection.execute(
                    text("SELECT entry_json FROM agent_run_ledger_entries ORDER BY sequence_no")
                )
            ).scalars()
            return tuple(_entry_from_database(row) for row in rows)

    async def resolve_approval(
        self,
        *,
        approval_id: str,
        proposal_id: str,
        evaluated_at: datetime,
    ) -> AgentApprovalObservation:
        try:
            entries = await self.entries()
        except (OSError, SQLAlchemyError):
            return AgentApprovalObservation(
                status="ledger_unavailable",
                approval_id=approval_id,
                evaluated_at=evaluated_at,
            )
        except ValueError:
            return AgentApprovalObservation(
                status="ledger_invalid",
                approval_id=approval_id,
                evaluated_at=evaluated_at,
            )
        return resolve_agent_approval(
            entries,
            approval_id=approval_id,
            proposal_id=proposal_id,
            evaluated_at=evaluated_at,
            trusted_key_ids=self._trusted_key_ids,
        )

    async def is_ready(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1 FROM agent_run_ledger_entries LIMIT 1"))
            return True
        except (OSError, SQLAlchemyError):
            return False

    async def close(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

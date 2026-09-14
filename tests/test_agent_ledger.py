"""PostgreSQL signed-ledger persistence boundary tests."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from atlas_pulse.agent_governance import (
    AgentApprovalGrant,
    Ed25519LedgerSigner,
    build_agent_approval,
    build_signed_ledger_entry,
    ledger_entry_json,
)
from atlas_pulse.agent_ledger import PostgresAgentRunLedger, _entry_from_database

NOW = datetime(2026, 9, 14, 9, tzinfo=UTC)
PROPOSAL_ID = f"proposal-{'a' * 64}"
MANIFEST_ID = f"manifest-{'b' * 64}"


class FakeResult:
    def __init__(self, values: list[object] | None = None) -> None:
        self.values = values or []

    def scalars(self) -> Iterator[object]:
        return iter(self.values)


class FakeConnection:
    def __init__(self, results: list[FakeResult]) -> None:
        self.results = results
        self.executions: list[tuple[str, object]] = []

    async def execute(self, statement: object, parameters: object = None) -> FakeResult:
        self.executions.append((str(statement), parameters))
        return self.results.pop(0) if self.results else FakeResult()


class FakeContext:
    def __init__(self, connection: FakeConnection, error: Exception | None = None) -> None:
        self.connection = connection
        self.error = error

    async def __aenter__(self) -> FakeConnection:
        if self.error is not None:
            raise self.error
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        return None


class FakeEngine:
    def __init__(
        self,
        *,
        results: list[FakeResult] | None = None,
        connect_error: Exception | None = None,
    ) -> None:
        self.connection = FakeConnection(results or [])
        self.connect_error = connect_error
        self.disposed = False

    def connect(self) -> FakeContext:
        return FakeContext(self.connection, self.connect_error)

    def begin(self) -> FakeContext:
        return FakeContext(self.connection)

    async def dispose(self) -> None:
        self.disposed = True


def _approval() -> AgentApprovalGrant:
    return build_agent_approval(
        proposal_id=PROPOSAL_ID,
        source_manifest_id=MANIFEST_ID,
        approver_id="github:12345",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        reason="Reviewed the exact evidence-bound proposal.",
    )


def _store(engine: FakeEngine, *, trusted: set[str] | None = None) -> PostgresAgentRunLedger:
    return PostgresAgentRunLedger(
        database_url="postgresql+asyncpg://unused",
        trusted_key_ids=trusted or set(),
        engine=cast(AsyncEngine, engine),
    )


def test_database_entry_conversion_accepts_driver_json_shapes() -> None:
    signer = Ed25519LedgerSigner.generate()
    entry = build_signed_ledger_entry(
        _approval(),
        sequence=1,
        previous_entry_id=None,
        signer=signer,
    )
    mapping = entry.model_dump(mode="json")

    assert _entry_from_database(mapping) == entry
    assert _entry_from_database(ledger_entry_json(entry)) == entry
    assert _entry_from_database(ledger_entry_json(entry).encode()) == entry
    with pytest.raises(ValueError, match="object, string, or bytes"):
        _entry_from_database(7)


async def test_postgres_ledger_serializes_and_appends_under_one_lock() -> None:
    signer = Ed25519LedgerSigner.generate()
    engine = FakeEngine(results=[FakeResult(), FakeResult(), FakeResult()])
    store = _store(engine)

    entry = await store.append(_approval(), signer=signer)

    assert entry.sequence == 1
    assert entry.previous_entry_id is None
    assert len(engine.connection.executions) == 3
    assert "pg_advisory_xact_lock" in engine.connection.executions[0][0]
    assert "ORDER BY sequence_no" in engine.connection.executions[1][0]
    statement, parameters = engine.connection.executions[2]
    assert "INSERT INTO agent_run_ledger_entries" in statement
    assert isinstance(parameters, Mapping)
    assert parameters["entry_id"] == entry.entry_id
    assert parameters["signing_key_id"] == signer.key_id
    assert isinstance(parameters["entry_json"], str)


async def test_postgres_ledger_extends_the_verified_database_head() -> None:
    signer = Ed25519LedgerSigner.generate()
    first = build_signed_ledger_entry(
        _approval(),
        sequence=1,
        previous_entry_id=None,
        signer=signer,
    )
    second_approval = build_agent_approval(
        proposal_id=PROPOSAL_ID,
        source_manifest_id=MANIFEST_ID,
        approver_id="github:67890",
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
        reason="Independent review approved the same proposal.",
    )
    engine = FakeEngine(
        results=[FakeResult(), FakeResult([first.model_dump(mode="json")]), FakeResult()]
    )

    second = await _store(engine).append(second_approval, signer=signer)

    assert second.sequence == 2
    assert second.previous_entry_id == first.entry_id


async def test_postgres_ledger_refuses_to_extend_invalid_stored_content() -> None:
    signer = Ed25519LedgerSigner.generate()
    first = build_signed_ledger_entry(
        _approval(),
        sequence=1,
        previous_entry_id=None,
        signer=signer,
    )
    malformed = first.model_dump(mode="json")
    malformed["artifact_id"] = f"approval-{'f' * 64}"
    engine = FakeEngine(results=[FakeResult(), FakeResult([malformed])])

    with pytest.raises(ValueError, match="ledger metadata does not match"):
        await _store(engine).append(_approval(), signer=signer)


async def test_postgres_ledger_reads_and_resolves_trusted_approval() -> None:
    signer = Ed25519LedgerSigner.generate()
    approval = _approval()
    entry = build_signed_ledger_entry(
        approval,
        sequence=1,
        previous_entry_id=None,
        signer=signer,
    )
    engine = FakeEngine(
        results=[
            FakeResult([entry.model_dump(mode="json")]),
            FakeResult([entry.model_dump(mode="json")]),
        ]
    )
    store = _store(engine, trusted={signer.key_id})

    assert await store.entries() == (entry,)
    observation = await store.resolve_approval(
        approval_id=approval.approval_id,
        proposal_id=PROPOSAL_ID,
        evaluated_at=NOW + timedelta(minutes=1),
    )

    assert observation.status == "active"


async def test_postgres_approval_resolution_maps_storage_failures_to_closed_states() -> None:
    unavailable = _store(FakeEngine(connect_error=SQLAlchemyError("database down")))
    observation = await unavailable.resolve_approval(
        approval_id=f"approval-{'a' * 64}",
        proposal_id=PROPOSAL_ID,
        evaluated_at=NOW,
    )
    assert observation.status == "ledger_unavailable"

    invalid = _store(FakeEngine(results=[FakeResult([{"not": "a ledger entry"}])]))
    observation = await invalid.resolve_approval(
        approval_id=f"approval-{'a' * 64}",
        proposal_id=PROPOSAL_ID,
        evaluated_at=NOW,
    )
    assert observation.status == "ledger_invalid"


async def test_postgres_ledger_readiness_and_engine_ownership() -> None:
    ready = _store(FakeEngine(results=[FakeResult()]))
    assert await ready.is_ready() is True

    failing_engine = FakeEngine(connect_error=SQLAlchemyError("database down"))
    failing = _store(failing_engine)
    assert await failing.is_ready() is False
    await failing.close()
    assert failing_engine.disposed is False

    owned = PostgresAgentRunLedger(database_url="postgresql+asyncpg://unused")
    await owned.close()

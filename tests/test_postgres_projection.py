"""Fast unit tests for PostGIS projection SQL and boundary conversion."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

import pytest
from agent_rag_core import Event, GeoPoint
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from atlas_pulse.projections import GeoBounds, PostgresSignalStore, SignalQuery
from atlas_pulse.projections.postgres import (
    _event_from_database,
    parse_stream_id,
    projection_values,
)
from atlas_pulse.streams import StreamMessage


class FakeResult:
    def __init__(
        self,
        *,
        scalar: str | None = None,
        rows: list[Mapping[str, object]] | None = None,
    ) -> None:
        self.scalar = scalar
        self.rows = rows or []

    def scalar_one_or_none(self) -> str | None:
        return self.scalar

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[Mapping[str, object]]:
        return self.rows


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


def make_event(
    event_id: str = "event-1",
    *,
    source: str = "usgs",
    payload: dict[str, object] | None = None,
    location: GeoPoint | None = None,
) -> Event:
    return Event(
        event_id=event_id,
        event_type="weather.alert" if source == "nws" else "seismic.earthquake",
        source=source,
        occurred_at=datetime(2024, 7, 10, 12, tzinfo=UTC),
        ingested_at=datetime(2024, 7, 10, 12, 1, tzinfo=UTC),
        location=location,
        payload=payload or {},
    )


def store_with(engine: FakeEngine) -> PostgresSignalStore:
    return PostgresSignalStore(
        database_url="postgresql+asyncpg://unused",
        engine=cast(AsyncEngine, engine),
    )


def test_parse_stream_id_uses_numeric_valkey_ordering() -> None:
    assert parse_stream_id("1720000000000-12") == (1_720_000_000_000, 12)


@pytest.mark.parametrize("stream_id", ["", "123", "a-1", "1-b", "1-2-3"])
def test_parse_stream_id_rejects_malformed_values(stream_id: str) -> None:
    with pytest.raises(ValueError, match="invalid stream id"):
        parse_stream_id(stream_id)


def test_projection_values_extracts_spatial_and_indexed_fields() -> None:
    geometry = {
        "type": "Polygon",
        "coordinates": [[[-98.0, 34.0], [-96.0, 34.0], [-96.0, 36.0], [-98.0, 34.0]]],
    }
    event = make_event(
        source="nws",
        location=GeoPoint(latitude=35, longitude=-97, altitude_km=None),
        payload={
            "geometry": geometry,
            "severity_rank": 3,
            "magnitude": 4,
            "expires_at": "2024-07-11T00:00:00Z",
            "place": "Test County",
            "title": "Tornado Warning",
        },
    )

    values = projection_values(StreamMessage(stream_id="1000-2", event=event))

    assert values["stream_ms"] == 1000
    assert values["stream_seq"] == 2
    assert values["longitude"] == -97
    assert values["latitude"] == 35
    assert values["footprint_json"] == json.dumps(geometry, sort_keys=True, separators=(",", ":"))
    assert values["severity_rank"] == 3
    assert values["magnitude"] == 4.0
    assert values["expires_at"] == datetime(2024, 7, 11, tzinfo=UTC)
    assert values["place"] == "Test County"


def test_projection_values_ignores_unindexable_payload_values() -> None:
    event = make_event(
        payload={
            "geometry": {"type": "Point", "coordinates": [1, 2]},
            "severity_rank": True,
            "magnitude": False,
            "expires_at": "not-a-date",
            "place": 42,
        }
    )

    values = projection_values(StreamMessage(stream_id="1-0", event=event))

    assert values["footprint_json"] is None
    assert values["severity_rank"] is None
    assert values["magnitude"] is None
    assert values["expires_at"] is None
    assert values["place"] is None
    assert values["longitude"] is None


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(make_event().model_dump(mode="json"), id="mapping"),
        pytest.param(make_event().model_dump_json(), id="string"),
        pytest.param(make_event().model_dump_json().encode(), id="bytes"),
    ],
)
def test_event_from_database_accepts_driver_json_shapes(value: object) -> None:
    assert _event_from_database(value).event_id == "event-1"


def test_event_from_database_rejects_an_unknown_driver_shape() -> None:
    with pytest.raises(TypeError, match="event_json"):
        _event_from_database(7)


async def test_checkpoint_reads_the_named_projection() -> None:
    engine = FakeEngine(results=[FakeResult(scalar="123-4")])
    store = store_with(engine)

    assert await store.checkpoint("signals") == "123-4"
    assert "projection_checkpoints" in engine.connection.executions[0][0]
    assert engine.connection.executions[0][1] == {"projection_name": "signals"}


async def test_project_atomically_writes_revisions_current_rows_and_checkpoint() -> None:
    engine = FakeEngine()
    store = store_with(engine)
    messages = (
        StreamMessage(stream_id="100-0", event=make_event("one")),
        StreamMessage(stream_id="100-1", event=make_event("two")),
    )

    await store.project(projection_name="signals", messages=messages)

    assert len(engine.connection.executions) == 3
    statements = [statement for statement, _parameters in engine.connection.executions]
    assert "INSERT INTO event_revisions" in statements[0]
    assert "INSERT INTO current_signals" in statements[1]
    assert "INSERT INTO projection_checkpoints" in statements[2]
    assert engine.connection.executions[2][1] == {
        "projection_name": "signals",
        "stream_id": "100-1",
        "stream_ms": 100,
        "stream_seq": 1,
    }


async def test_project_empty_batch_is_a_noop() -> None:
    engine = FakeEngine()
    await store_with(engine).project(projection_name="signals", messages=())
    assert engine.connection.executions == []


@pytest.mark.parametrize(
    "stream_ids",
    [("2-0", "1-0"), ("1-0", "1-0")],
)
async def test_project_rejects_non_deterministic_batches(stream_ids: tuple[str, str]) -> None:
    messages = tuple(
        StreamMessage(stream_id=stream_id, event=make_event(str(index)))
        for index, stream_id in enumerate(stream_ids)
    )
    with pytest.raises(ValueError, match="unique oldest-first"):
        await store_with(FakeEngine()).project(projection_name="signals", messages=messages)


async def test_query_current_builds_all_filters_and_keyset_page() -> None:
    first = make_event("first", source="nws")
    second = make_event("second", source="nws")
    engine = FakeEngine(
        results=[
            FakeResult(
                rows=[
                    {"stream_id": "200-2", "event_json": first.model_dump(mode="json")},
                    {"stream_id": "200-1", "event_json": second.model_dump_json()},
                ]
            )
        ]
    )
    store = store_with(engine)
    occurred_after = datetime(2024, 7, 1, tzinfo=UTC)
    occurred_before = datetime(2024, 7, 31, tzinfo=UTC)

    page = await store.query_current(
        SignalQuery(
            limit=1,
            after="300-4",
            source="nws",
            min_severity=3,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            bounds=GeoBounds(west=-100, south=20, east=-80, north=40),
            include_area_only=True,
            active_only=True,
        )
    )

    assert [message.event.event_id for message in page.items] == ["first"]
    assert page.next_cursor == "200-2"
    assert page.has_more is True
    statement, parameters = engine.connection.executions[0]
    assert "cs.source = :source" in statement
    assert "er.severity_rank >= :min_severity" in statement
    assert "er.occurred_at >= :occurred_after" in statement
    assert "er.occurred_at <= :occurred_before" in statement
    assert "er.expires_at IS NULL" in statement
    assert "(cs.stream_ms, cs.stream_seq) <" in statement
    assert "ST_Intersects" in statement
    assert "er.footprint IS NULL AND er.point IS NULL" in statement
    assert parameters == {
        "fetch_limit": 2,
        "source": "nws",
        "min_severity": 3,
        "occurred_after": occurred_after,
        "occurred_before": occurred_before,
        "cursor_ms": 300,
        "cursor_seq": 4,
        "west": -100,
        "south": 20,
        "east": -80,
        "north": 40,
    }


async def test_query_current_supports_empty_unfiltered_and_strict_spatial_pages() -> None:
    unfiltered_engine = FakeEngine(results=[FakeResult(rows=[])])
    unfiltered = await store_with(unfiltered_engine).query_current(
        SignalQuery(limit=10, active_only=False)
    )
    assert unfiltered.items == ()
    assert unfiltered.next_cursor is None
    assert unfiltered.has_more is False
    assert "ST_Intersects" not in unfiltered_engine.connection.executions[0][0]

    spatial_engine = FakeEngine(results=[FakeResult(rows=[])])
    await store_with(spatial_engine).query_current(
        SignalQuery(
            bounds=GeoBounds(west=0, south=0, east=1, north=1),
            include_area_only=False,
        )
    )
    statement = spatial_engine.connection.executions[0][0]
    assert "ST_Intersects" in statement
    assert "er.footprint IS NULL AND er.point IS NULL" not in statement


async def test_store_readiness_and_engine_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    external = FakeEngine()
    external_store = store_with(external)
    assert await external_store.is_ready() is True
    assert "projection_checkpoints" in external.connection.executions[0][0]
    await external_store.close()
    assert external.disposed is False

    owned = FakeEngine()
    monkeypatch.setattr(
        "atlas_pulse.projections.postgres.create_async_engine",
        lambda *_args, **_kwargs: cast(AsyncEngine, owned),
    )
    owned_store = PostgresSignalStore(database_url="postgresql+asyncpg://unused")
    await owned_store.close()
    assert owned.disposed is True


@pytest.mark.parametrize("error", [OSError("down"), SQLAlchemyError("down")])
async def test_store_readiness_handles_database_failures(error: Exception) -> None:
    store = store_with(FakeEngine(connect_error=error))
    assert await store.is_ready() is False

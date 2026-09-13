"""Fast unit tests for the atomic PostgreSQL/pgvector retrieval store."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest
from agent_rag_core import Event, GeoPoint
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from atlas_pulse.projections import GeoBounds
from atlas_pulse.retrieval import GeoRadius, IndexedDocument, SearchQuery
from atlas_pulse.retrieval.postgres import (
    PostgresRetrievalStore,
    _event_from_database,
    _number,
    _optional_number,
    _relaxed_websearch_query,
    _vector_literal,
    retrieval_document_values,
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

    def begin(self) -> FakeContext:
        return FakeContext(self)


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
    source: str = "nws",
    expires_at: str | None = "2099-01-01T00:00:00Z",
) -> Event:
    geometry = {
        "type": "Polygon",
        "coordinates": [[[-98.0, 34.0], [-96.0, 34.0], [-96.0, 36.0], [-98.0, 34.0]]],
    }
    return Event(
        event_id=event_id,
        event_type="weather.alert",
        source=source,
        occurred_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 12, 12, 1, tzinfo=UTC),
        location=GeoPoint(latitude=35, longitude=-97, altitude_km=None),
        payload={
            "title": "Tornado warning",
            "geometry": geometry,
            "expires_at": expires_at,
            "source_url": "https://api.weather.gov/alerts/event-1",
        },
    )


def make_document(stream_id: str = "100-2", event_id: str = "event-1") -> IndexedDocument:
    return IndexedDocument(
        message=StreamMessage(stream_id=stream_id, event=make_event(event_id)),
        text="Source: nws\nTitle: Tornado warning",
        document_hash="a" * 64,
        embedding_model="test/model",
        embedding=(0.6, 0.8, 0.0),
    )


def store_with(engine: FakeEngine) -> PostgresRetrievalStore:
    return PostgresRetrievalStore(
        database_url="postgresql+asyncpg://unused",
        engine=cast(AsyncEngine, engine),
    )


def test_retrieval_document_values_preserve_event_geometry_and_vector() -> None:
    values = retrieval_document_values(make_document())
    assert values["stream_ms"] == 100
    assert values["stream_seq"] == 2
    assert values["document_text"] == "Source: nws\nTitle: Tornado warning"
    assert values["document_hash"] == "a" * 64
    assert values["embedding_model"] == "test/model"
    assert values["embedding"] == "[0.6,0.8,0]"
    assert values["footprint_json"] is not None
    assert _vector_literal((1 / 3, -0.25)) == "[0.333333333,-0.25]"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(make_event().model_dump(mode="json"), id="mapping"),
        pytest.param(make_event().model_dump_json(), id="string"),
        pytest.param(make_event().model_dump_json().encode(), id="bytes"),
    ],
)
def test_database_event_conversion_accepts_driver_shapes(value: object) -> None:
    assert _event_from_database(value).event_id == "event-1"


def test_database_boundary_conversion_rejects_invalid_shapes() -> None:
    with pytest.raises(TypeError, match="event_json"):
        _event_from_database(7)
    for value in (None, True, "0.7"):
        with pytest.raises(TypeError, match="score"):
            _number(value, field="score")
    assert _number(Decimal("0.75"), field="score") == 0.75
    assert _optional_number(None, field="distance") is None
    assert _optional_number(12, field="distance") == 12


def test_relaxed_lexical_query_is_bounded_deduplicated_and_syntax_safe() -> None:
    assert (
        _relaxed_websearch_query("earthquake near homes, roads OR a populated area")
        == "earthquake OR near OR homes OR roads OR a OR populated OR area"
    )
    assert _relaxed_websearch_query('"danger" -roads NOT danger') == "danger OR roads"
    terms = _relaxed_websearch_query(" ".join(f"term{index}" for index in range(40))).split(" OR ")
    assert len(terms) == 32
    assert terms[-1] == "term31"


async def test_checkpoint_reads_the_named_retrieval_projection() -> None:
    engine = FakeEngine(results=[FakeResult(scalar="123-4")])
    store = store_with(engine)
    assert await store.checkpoint("retrieval") == "123-4"
    assert engine.connection.executions[0][1] == {"projection_name": "retrieval"}


async def test_index_atomically_upserts_documents_and_checkpoint() -> None:
    engine = FakeEngine()
    store = store_with(engine)
    documents = (make_document("100-0", "event-1"), make_document("100-1", "event-2"))

    await store.index(projection_name="retrieval", documents=documents)

    assert len(engine.connection.executions) == 2
    statement, values = engine.connection.executions[0]
    assert "INSERT INTO retrieval_documents" in statement
    assert "CAST(:embedding AS vector)" in statement
    assert "retrieval_documents.stream_ms" in statement
    assert "IS DISTINCT FROM" in statement
    assert isinstance(values, list) and len(values) == 2
    assert engine.connection.executions[1][1] == {
        "projection_name": "retrieval",
        "stream_id": "100-1",
        "stream_ms": 100,
        "stream_seq": 1,
    }


async def test_index_empty_batch_is_noop_and_order_is_enforced() -> None:
    engine = FakeEngine()
    store = store_with(engine)
    await store.index(projection_name="retrieval", documents=())
    assert engine.connection.executions == []

    for documents in (
        (make_document("2-0", "one"), make_document("1-0", "two")),
        (make_document("1-0", "one"), make_document("1-0", "two")),
    ):
        with pytest.raises(ValueError, match="unique oldest-first"):
            await store.index(projection_name="retrieval", documents=documents)


async def test_candidates_apply_all_filters_in_one_repeatable_read_snapshot() -> None:
    lexical_event = make_event("event-1")
    dense_event = make_event("event-2")
    lexical_row = {
        "stream_id": "200-0",
        "event_json": lexical_event.model_dump(mode="json"),
        "document_text": "Tornado warning",
        "score": Decimal("0.81"),
        "distance_km": Decimal("12.5"),
    }
    dense_row = {
        "stream_id": "201-0",
        "event_json": dense_event.model_dump_json(),
        "document_text": "Residents should shelter",
        "score": 0.93,
        "distance_km": 8,
    }
    engine = FakeEngine(
        results=[
            FakeResult(),
            FakeResult(),
            FakeResult(rows=[lexical_row]),
            FakeResult(rows=[dense_row]),
        ]
    )
    store = store_with(engine)
    after = datetime(2026, 9, 1, tzinfo=UTC)
    before = datetime(2026, 9, 13, tzinfo=UTC)
    query = SearchQuery(
        text="dangerous storm",
        limit=5,
        candidate_limit=25,
        source="nws",
        occurred_after=after,
        occurred_before=before,
        bounds=GeoBounds(west=-100, south=30, east=-90, north=40),
        near=GeoRadius(longitude=-97, latitude=35, radius_km=100),
    )

    batch = await store.candidates(
        query,
        (0.6, 0.8, 0.0),
        embedding_model="test/model",
    )

    assert batch.lexical[0].message.event.event_id == "event-1"
    assert batch.lexical[0].rank == 1
    assert batch.lexical[0].score == 0.81
    assert batch.lexical[0].distance_km == 12.5
    assert batch.dense[0].message.event.event_id == "event-2"
    assert batch.dense[0].score == 0.93
    executions = engine.connection.executions
    assert "REPEATABLE READ" in executions[0][0]
    assert "hnsw.iterative_scan" in executions[1][0]
    lexical_sql, parameters = executions[2]
    dense_sql = executions[3][0]
    for statement in (lexical_sql, dense_sql):
        assert "source = :source" in statement
        assert "occurred_at >= :occurred_after" in statement
        assert "occurred_at <= :occurred_before" in statement
        assert "expires_at IS NULL" in statement
        assert "ST_Intersects" in statement
        assert "ST_DWithin" in statement
        assert "ST_Distance" in statement
    assert "websearch_to_tsquery" in lexical_sql
    assert "CROSS JOIN lexical_query" in lexical_sql
    assert "numnode(lexical_query.value) > 0" in lexical_sql
    assert "text_search @@ lexical_query.value" in lexical_sql
    assert "embedding <=>" in dense_sql
    assert "embedding_model = :embedding_model" not in lexical_sql
    assert "embedding_model = :embedding_model" in dense_sql
    assert parameters == {
        "candidate_limit": 25,
        "source": "nws",
        "occurred_after": after,
        "occurred_before": before,
        "west": -100,
        "south": 30,
        "east": -90,
        "north": 40,
        "near_longitude": -97,
        "near_latitude": 35,
        "radius_metres": 100_000,
        "lexical_query": "dangerous OR storm",
        "query_embedding": "[0.6,0.8,0]",
        "embedding_model": "test/model",
    }


async def test_candidates_support_unfiltered_inactive_search_without_distance() -> None:
    event = make_event("event-1", expires_at=None)
    row = {
        "stream_id": "1-0",
        "event_json": event.model_dump_json().encode(),
        "document_text": "earth movement",
        "score": 0.5,
        "distance_km": None,
    }
    engine = FakeEngine(
        results=[FakeResult(), FakeResult(), FakeResult(rows=[]), FakeResult(rows=[row])]
    )
    batch = await store_with(engine).candidates(
        SearchQuery(text="earthquake", candidate_limit=10, active_only=False),
        (1.0, 0.0, 0.0),
        embedding_model="test/model",
    )
    assert batch.lexical == ()
    assert batch.dense[0].distance_km is None
    dense_statement = engine.connection.executions[3][0]
    assert "ST_Intersects" not in dense_statement
    assert "ST_DWithin" not in dense_statement
    assert "NULL::double precision AS distance_km" in dense_statement
    assert "expires_at IS NULL" not in dense_statement


async def test_retrieval_store_readiness_and_engine_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external = FakeEngine()
    store = store_with(external)
    assert await store.is_ready() is True
    assert "retrieval_documents" in external.connection.executions[0][0]
    await store.close()
    assert external.disposed is False

    owned = FakeEngine()
    monkeypatch.setattr(
        "atlas_pulse.retrieval.postgres.create_async_engine",
        lambda *_args, **_kwargs: cast(AsyncEngine, owned),
    )
    owned_store = PostgresRetrievalStore(database_url="postgresql+asyncpg://unused")
    await owned_store.close()
    assert owned.disposed is True


@pytest.mark.parametrize("error", [OSError("down"), SQLAlchemyError("down")])
async def test_retrieval_readiness_handles_database_failures(error: Exception) -> None:
    assert await store_with(FakeEngine(connect_error=error)).is_ready() is False

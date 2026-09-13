"""Real PostGIS proof for durable current-state projection behavior."""

import os
from datetime import UTC, datetime

import pytest
from agent_rag_core import Event, GeoPoint
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from atlas_pulse.projections import CorrelationQuery, GeoBounds, PostgresSignalStore, SignalQuery
from atlas_pulse.retrieval import (
    GeoRadius,
    HybridSearchService,
    IndexedDocument,
    PostgresRetrievalStore,
    SearchQuery,
    document_hash,
    render_event_document,
)
from atlas_pulse.retrieval.base import Embedding
from atlas_pulse.streams import StreamMessage

DATABASE_URL = os.getenv("ATLAS_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration

_STREAM_IDS = (
    "9100000-0",
    "9100001-0",
    "9100002-0",
    "9100003-0",
    "9100004-0",
    "9100005-0",
    "9100006-0",
)
_PROJECTION = "day5-integration"
_RETRIEVAL_PROJECTION = "day9-retrieval-integration"


async def _clean(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM retrieval_documents WHERE event_id LIKE 'day5-%'")
        )
        await connection.execute(text("DELETE FROM current_signals WHERE event_id LIKE 'day5-%'"))
        await connection.execute(text("DELETE FROM event_revisions WHERE stream_id LIKE '910000%'"))
        await connection.execute(
            text(
                "DELETE FROM projection_checkpoints "
                "WHERE projection_name IN (:projection_name, :retrieval_projection_name)"
            ),
            {
                "projection_name": _PROJECTION,
                "retrieval_projection_name": _RETRIEVAL_PROJECTION,
            },
        )
    await engine.dispose()


def _event(
    event_id: str,
    *,
    source: str,
    occurred_at: datetime,
    payload: dict[str, object],
    location: GeoPoint | None,
) -> Event:
    return Event(
        event_id=event_id,
        event_type=(
            "weather.alert"
            if source == "nws"
            else "fire.thermal_anomaly"
            if source == "firms"
            else "geopolitical.gdelt_event"
            if source == "gdelt"
            else "seismic.earthquake"
        ),
        source=source,
        occurred_at=occurred_at,
        ingested_at=occurred_at,
        location=location,
        payload=payload,
    )


def _embedding(index: int) -> Embedding:
    values = [0.0] * 384
    values[index] = 1.0
    return tuple(values)


class IntegrationQueryEmbedder:
    model_name = "test/integration-384"
    dimensions = 384

    async def embed_documents(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
        return tuple(_embedding(0) for _text in texts)

    async def embed_query(self, text: str) -> Embedding:
        if "heat" in text or "thermal" in text:
            return _embedding(5)
        if "fight" in text:
            return _embedding(6)
        return _embedding(0)


@pytest.mark.skipif(DATABASE_URL is None, reason="ATLAS_TEST_DATABASE_URL is not set")
async def test_postgis_projection_is_durable_current_and_spatial() -> None:
    assert DATABASE_URL is not None
    await _clean(DATABASE_URL)
    engine = create_async_engine(DATABASE_URL)
    store = PostgresSignalStore(database_url=DATABASE_URL, engine=engine)
    retrieval_store = PostgresRetrievalStore(database_url=DATABASE_URL, engine=engine)
    occurred = datetime.now(UTC)
    geometry = {
        "type": "Polygon",
        "coordinates": [[[-98.0, 34.0], [-96.0, 34.0], [-96.0, 36.0], [-98.0, 34.0]]],
    }
    messages = (
        StreamMessage(
            stream_id=_STREAM_IDS[0],
            event=_event(
                "day5-quake",
                source="usgs",
                occurred_at=occurred,
                location=GeoPoint(latitude=12.25, longitude=77.5, altitude_km=-10),
                payload={"magnitude": 4.0, "title": "Original quake"},
            ),
        ),
        StreamMessage(
            stream_id=_STREAM_IDS[1],
            event=_event(
                "day5-quake",
                source="usgs",
                occurred_at=occurred,
                location=GeoPoint(latitude=12.3, longitude=77.6, altitude_km=-11),
                payload={"magnitude": 5.0, "title": "Revised quake"},
            ),
        ),
        StreamMessage(
            stream_id=_STREAM_IDS[2],
            event=_event(
                "day5-polygon",
                source="nws",
                occurred_at=occurred,
                location=GeoPoint(latitude=35, longitude=-97, altitude_km=None),
                payload={
                    "geometry": geometry,
                    "severity_rank": 3,
                    "expires_at": "2099-01-01T00:00:00Z",
                    "title": "Active polygon alert",
                },
            ),
        ),
        StreamMessage(
            stream_id=_STREAM_IDS[3],
            event=_event(
                "day5-area-only",
                source="nws",
                occurred_at=occurred,
                location=None,
                payload={
                    "severity_rank": 4,
                    "expires_at": "2099-01-01T00:00:00Z",
                    "title": "Active area-code alert",
                },
            ),
        ),
        StreamMessage(
            stream_id=_STREAM_IDS[4],
            event=_event(
                "day5-expired",
                source="nws",
                occurred_at=occurred,
                location=GeoPoint(latitude=35, longitude=-97, altitude_km=None),
                payload={
                    "severity_rank": 2,
                    "expires_at": "2020-01-01T00:00:00Z",
                    "title": "Expired alert",
                },
            ),
        ),
        StreamMessage(
            stream_id=_STREAM_IDS[5],
            event=_event(
                "day5-fire",
                source="firms",
                occurred_at=occurred,
                location=GeoPoint(latitude=34.12, longitude=-118.54, altitude_km=None),
                payload={
                    "confidence": "High",
                    "confidence_rank": 3,
                    "fire_radiative_power_mw": 18.4,
                    "satellite": "N20",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "title": "VIIRS thermal anomaly",
                },
            ),
        ),
        StreamMessage(
            stream_id=_STREAM_IDS[6],
            event=_event(
                "day5-gdelt",
                source="gdelt",
                occurred_at=occurred,
                location=GeoPoint(latitude=13.0827, longitude=80.2707, altitude_km=None),
                payload={
                    "category": "Fight",
                    "severity_rank": 3,
                    "goldstein_scale": -10.0,
                    "expires_at": "2099-01-01T00:00:00Z",
                    "title": "Fight: GOVERNMENT → REBELS",
                },
            ),
        ),
    )

    try:
        await store.project(projection_name=_PROJECTION, messages=messages)
        await store.project(projection_name=_PROJECTION, messages=messages)
        retrieval_documents = tuple(
            IndexedDocument(
                message=message,
                text=(rendered := render_event_document(message.event)),
                document_hash=document_hash(rendered),
                embedding_model="test/integration-384",
                embedding=_embedding(index),
            )
            for index, message in enumerate(messages)
        )
        await retrieval_store.index(
            projection_name=_RETRIEVAL_PROJECTION,
            documents=retrieval_documents,
        )
        await retrieval_store.index(
            projection_name=_RETRIEVAL_PROJECTION,
            documents=retrieval_documents,
        )

        assert await store.checkpoint(_PROJECTION) == _STREAM_IDS[-1]
        all_current = await store.query_current(SignalQuery(limit=10, active_only=False))
        assert len(all_current.items) == 6
        assert all_current.items[-1].event.payload["title"] == "Revised quake"

        active = await store.query_current(SignalQuery(limit=10))
        assert {message.event.event_id for message in active.items} == {
            "day5-quake",
            "day5-polygon",
            "day5-area-only",
            "day5-fire",
            "day5-gdelt",
        }

        bounds = GeoBounds(west=-100, south=30, east=-90, north=40)
        mapped = await store.query_current(SignalQuery(limit=10, bounds=bounds))
        assert [message.event.event_id for message in mapped.items] == ["day5-polygon"]
        with_area_only = await store.query_current(
            SignalQuery(limit=10, bounds=bounds, include_area_only=True)
        )
        assert [message.event.event_id for message in with_area_only.items] == [
            "day5-area-only",
            "day5-polygon",
        ]

        fires = await store.query_current(
            SignalQuery(
                limit=10,
                source="firms",
                bounds=GeoBounds(west=-120, south=33, east=-117, north=36),
            )
        )
        assert [message.event.event_id for message in fires.items] == ["day5-fire"]

        conflicts = await store.query_current(
            SignalQuery(
                limit=10,
                source="gdelt",
                min_severity=3,
                bounds=GeoBounds(west=79, south=12, east=82, north=15),
            )
        )
        assert [message.event.event_id for message in conflicts.items] == ["day5-gdelt"]

        correlations = await store.query_correlations(
            CorrelationQuery(
                radius_km=500,
                time_window_minutes=60,
                lookback_hours=168,
                edge_limit=10,
                bounds=GeoBounds(west=70, south=5, east=85, north=20),
            )
        )
        assert correlations.truncated is False
        assert len(correlations.pairs) == 1
        india_pair = correlations.pairs[0]
        assert {india_pair.left.event.event_id, india_pair.right.event.event_id} == {
            "day5-quake",
            "day5-gdelt",
        }
        assert 250 < india_pair.distance_km < 400
        assert india_pair.time_delta_minutes == 0

        first_page = await store.query_current(SignalQuery(limit=2, active_only=False))
        second_page = await store.query_current(
            SignalQuery(limit=2, after=first_page.next_cursor, active_only=False)
        )
        assert first_page.has_more is True
        assert [message.stream_id for message in first_page.items] == list(_STREAM_IDS[6:4:-1])
        assert [message.stream_id for message in second_page.items] == list(_STREAM_IDS[4:2:-1])

        async with engine.connect() as connection:
            revision_count = await connection.scalar(
                text("SELECT count(*) FROM event_revisions WHERE stream_id LIKE '910000%'")
            )
        assert revision_count == 7

        assert await retrieval_store.checkpoint(_RETRIEVAL_PROJECTION) == _STREAM_IDS[-1]
        search = HybridSearchService(
            store=retrieval_store,
            embedder=IntegrationQueryEmbedder(),
        )
        thermal = await search.search(
            SearchQuery(
                text="satellite thermal anomaly",
                limit=3,
                candidate_limit=10,
                source="firms",
                bounds=GeoBounds(west=-120, south=33, east=-117, north=36),
                near=GeoRadius(longitude=-118.5, latitude=34.1, radius_km=50),
            )
        )
        assert [hit.message.event.event_id for hit in thermal.hits] == ["day5-fire"]
        assert thermal.hits[0].ranking.lexical_rank == 1
        assert thermal.hits[0].ranking.dense_rank == 1
        assert thermal.hits[0].distance_km is not None
        assert thermal.hits[0].distance_km < 10

        conflict = await search.search(
            SearchQuery(
                text="fight between opposing groups",
                limit=3,
                candidate_limit=10,
                source="gdelt",
                bounds=GeoBounds(west=79, south=12, east=82, north=15),
            )
        )
        assert [hit.message.event.event_id for hit in conflict.hits] == ["day5-gdelt"]
        assert conflict.hits[0].ranking.dense_rank == 1

        future = await search.search(
            SearchQuery(
                text="fight between opposing groups",
                limit=3,
                candidate_limit=10,
                occurred_after=datetime(2099, 1, 2, tzinfo=UTC),
            )
        )
        assert future.hits == ()
    finally:
        await engine.dispose()
        await _clean(DATABASE_URL)

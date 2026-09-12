"""Real PostGIS proof for durable current-state projection behavior."""

import os
from datetime import UTC, datetime

import pytest
from agent_rag_core import Event, GeoPoint
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from atlas_pulse.projections import GeoBounds, PostgresSignalStore, SignalQuery
from atlas_pulse.streams import StreamMessage

DATABASE_URL = os.getenv("ATLAS_TEST_DATABASE_URL")
pytestmark = pytest.mark.integration

_STREAM_IDS = ("9100000-0", "9100001-0", "9100002-0", "9100003-0", "9100004-0")
_EVENT_IDS = ("day5-quake", "day5-polygon", "day5-area-only", "day5-expired")
_PROJECTION = "day5-integration"


async def _clean(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM current_signals WHERE event_id LIKE 'day5-%'"))
        await connection.execute(text("DELETE FROM event_revisions WHERE stream_id LIKE '910000%'"))
        await connection.execute(
            text("DELETE FROM projection_checkpoints WHERE projection_name = :projection_name"),
            {"projection_name": _PROJECTION},
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
        event_type="weather.alert" if source == "nws" else "seismic.earthquake",
        source=source,
        occurred_at=occurred_at,
        ingested_at=occurred_at,
        location=location,
        payload=payload,
    )


@pytest.mark.skipif(DATABASE_URL is None, reason="ATLAS_TEST_DATABASE_URL is not set")
async def test_postgis_projection_is_durable_current_and_spatial() -> None:
    assert DATABASE_URL is not None
    await _clean(DATABASE_URL)
    engine = create_async_engine(DATABASE_URL)
    store = PostgresSignalStore(database_url=DATABASE_URL, engine=engine)
    occurred = datetime(2026, 9, 12, 12, tzinfo=UTC)
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
    )

    try:
        await store.project(projection_name=_PROJECTION, messages=messages)
        await store.project(projection_name=_PROJECTION, messages=messages)

        assert await store.checkpoint(_PROJECTION) == _STREAM_IDS[-1]
        all_current = await store.query_current(SignalQuery(limit=10, active_only=False))
        assert len(all_current.items) == 4
        assert all_current.items[-1].event.payload["title"] == "Revised quake"

        active = await store.query_current(SignalQuery(limit=10))
        assert {message.event.event_id for message in active.items} == {
            "day5-quake",
            "day5-polygon",
            "day5-area-only",
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

        first_page = await store.query_current(SignalQuery(limit=2, active_only=False))
        second_page = await store.query_current(
            SignalQuery(limit=2, after=first_page.next_cursor, active_only=False)
        )
        assert first_page.has_more is True
        assert [message.stream_id for message in first_page.items] == list(_STREAM_IDS[4:2:-1])
        assert [message.stream_id for message in second_page.items] == list(_STREAM_IDS[2:0:-1])

        async with engine.connect() as connection:
            revision_count = await connection.scalar(
                text("SELECT count(*) FROM event_revisions WHERE stream_id LIKE '910000%'")
            )
        assert revision_count == 5
    finally:
        await engine.dispose()
        await _clean(DATABASE_URL)

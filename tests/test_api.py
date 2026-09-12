"""HTTP API behavior tests."""

from datetime import UTC, datetime

import httpx
import pytest
from agent_rag_core import Event

from atlas_pulse.api import create_app
from atlas_pulse.projections import SignalPage, SignalQuery
from atlas_pulse.streams import InMemoryEventBus
from atlas_pulse.streams.base import StreamMessage


class UnreadyBus(InMemoryEventBus):
    async def is_ready(self) -> bool:
        return False


class CloseTrackingBus(InMemoryEventBus):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FailingCloseBus(CloseTrackingBus):
    async def close(self) -> None:
        await super().close()
        raise RuntimeError("stream close failed")


class StubSignalStore:
    def __init__(
        self,
        *,
        page: SignalPage | None = None,
        ready: bool = True,
    ) -> None:
        self.page = page or SignalPage(items=(), next_cursor=None, has_more=False)
        self.ready = ready
        self.closed = False
        self.query: SignalQuery | None = None

    async def query_current(self, query: SignalQuery) -> SignalPage:
        self.query = query
        return self.page

    async def is_ready(self) -> bool:
        return self.ready

    async def close(self) -> None:
        self.closed = True


async def test_health_readiness_and_recent_events() -> None:
    bus = InMemoryEventBus()
    await bus.publish(
        Event(
            event_id="us7000demo",
            event_type="seismic.earthquake",
            source="usgs",
            occurred_at=datetime(2024, 7, 10, tzinfo=UTC),
        )
    )
    transport = httpx.ASGITransport(app=create_app(bus))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/healthz")
        ready = await client.get("/readyz")
        events = await client.get("/v1/events", params={"limit": 1})

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "version": "0.3.0"}
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert events.status_code == 200
    assert events.json()["count"] == 1
    assert events.json()["items"][0]["event"]["event_id"] == "us7000demo"


async def test_readiness_reports_dependency_failure() -> None:
    transport = httpx.ASGITransport(app=create_app(UnreadyBus()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"detail": "event stream unavailable"}


async def test_events_limit_is_validated() -> None:
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/events", params={"limit": 501})

    assert response.status_code == 422


async def test_application_lifespan_closes_event_bus() -> None:
    bus = CloseTrackingBus()
    store = StubSignalStore()
    app = create_app(bus, store)

    async with app.router.lifespan_context(app):
        assert bus.closed is False
        assert store.closed is False

    assert bus.closed is True
    assert store.closed is True


async def test_application_lifespan_still_closes_store_when_stream_close_fails() -> None:
    bus = FailingCloseBus()
    store = StubSignalStore()
    app = create_app(bus, store)

    with pytest.raises(RuntimeError, match="stream close failed"):
        async with app.router.lifespan_context(app):
            pass

    assert bus.closed is True
    assert store.closed is True


async def test_replay_is_oldest_first_and_cursor_paginated() -> None:
    bus = InMemoryEventBus()
    for event_id in ("one", "two", "three"):
        await bus.publish(
            Event(
                event_id=event_id,
                event_type="seismic.earthquake",
                source="usgs",
                occurred_at=datetime(2024, 7, 10, tzinfo=UTC),
            )
        )
    transport = httpx.ASGITransport(app=create_app(bus))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_page = await client.get("/v1/events/replay", params={"limit": 2})
        second_page = await client.get(
            "/v1/events/replay",
            params={"limit": 2, "after": first_page.json()["next_cursor"]},
        )

    assert first_page.status_code == 200
    assert [item["event"]["event_id"] for item in first_page.json()["items"]] == [
        "one",
        "two",
    ]
    assert first_page.json()["next_cursor"] == "0-2"
    assert first_page.json()["has_more"] is True
    assert first_page.json()["order"] == "oldest_first"
    assert [item["event"]["event_id"] for item in second_page.json()["items"]] == ["three"]
    assert second_page.json()["next_cursor"] == "0-3"
    assert second_page.json()["has_more"] is False


async def test_replay_rejects_an_invalid_stream_cursor() -> None:
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/events/replay", params={"after": "not-a-cursor"})

    assert response.status_code == 422


async def test_current_signals_maps_all_indexed_filters() -> None:
    event = Event(
        event_id="nws-demo",
        event_type="weather.alert",
        source="nws",
        occurred_at=datetime(2024, 7, 10, tzinfo=UTC),
    )
    store = StubSignalStore(
        page=SignalPage(
            items=(StreamMessage(stream_id="2000-4", event=event),),
            next_cursor="2000-4",
            has_more=True,
        )
    )
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus(), store))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/signals",
            params={
                "limit": 25,
                "after": "3000-0",
                "source": "nws",
                "min_severity": 3,
                "occurred_after": "2024-07-01T00:00:00Z",
                "occurred_before": "2024-07-31T00:00:00Z",
                "bbox": "-100,20,-80,40",
                "include_area_only": "true",
                "active_only": "false",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "count": 1,
        "items": [{"stream_id": "2000-4", "event": event.model_dump(mode="json")}],
        "next_cursor": "2000-4",
        "has_more": True,
        "order": "newest_revision_first",
    }
    assert store.query is not None
    assert store.query.limit == 25
    assert store.query.after == "3000-0"
    assert store.query.source == "nws"
    assert store.query.min_severity == 3
    assert store.query.bounds is not None
    assert store.query.bounds.west == -100
    assert store.query.include_area_only is True
    assert store.query.active_only is False


async def test_current_signals_requires_an_injected_projection() -> None:
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/signals")

    assert response.status_code == 503
    assert response.json() == {"detail": "signal projection unavailable"}


async def test_readiness_reports_projection_failure() -> None:
    transport = httpx.ASGITransport(
        app=create_app(InMemoryEventBus(), StubSignalStore(ready=False))
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"detail": "signal projection unavailable"}


async def test_current_signals_rejects_invalid_spatial_and_time_filters() -> None:
    app = create_app(InMemoryEventBus(), StubSignalStore())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        malformed = await client.get("/v1/signals", params={"bbox": "west,1,2,3"})
        incomplete = await client.get("/v1/signals", params={"bbox": "1,2,3"})
        wrapping = await client.get("/v1/signals", params={"bbox": "20,-5,-10,30"})
        naive_time = await client.get(
            "/v1/signals", params={"occurred_after": "2024-07-01T00:00:00"}
        )
        reversed_time = await client.get(
            "/v1/signals",
            params={
                "occurred_after": "2024-08-01T00:00:00Z",
                "occurred_before": "2024-07-01T00:00:00Z",
            },
        )

    assert malformed.status_code == 422
    assert incomplete.status_code == 422
    assert wrapping.status_code == 422
    assert naive_time.status_code == 422
    assert reversed_time.status_code == 422

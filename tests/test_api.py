"""HTTP API behavior tests."""

from datetime import UTC, datetime

import httpx
import pytest
from agent_rag_core import Event, GeoPoint

from atlas_pulse.api import create_app
from atlas_pulse.correlation import CORRELATION_CAVEAT, CorrelationBatch, CorrelationPair
from atlas_pulse.projections import CorrelationQuery, SignalPage, SignalQuery
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
        correlation_batch: CorrelationBatch | None = None,
    ) -> None:
        self.page = page or SignalPage(items=(), next_cursor=None, has_more=False)
        self.ready = ready
        self.closed = False
        self.query: SignalQuery | None = None
        self.correlation_batch = correlation_batch or CorrelationBatch(())
        self.correlation_query: CorrelationQuery | None = None

    async def query_current(self, query: SignalQuery) -> SignalPage:
        self.query = query
        return self.page

    async def query_correlations(self, query: CorrelationQuery) -> CorrelationBatch:
        self.correlation_query = query
        return self.correlation_batch

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
    assert health.json() == {"status": "ok", "version": "0.6.0"}
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


@pytest.mark.parametrize("source", ["firms", "gdelt"])
async def test_current_signals_accepts_new_source_filters(source: str) -> None:
    store = StubSignalStore()
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus(), store))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/signals", params={"source": source})

    assert response.status_code == 200
    assert store.query is not None
    assert store.query.source == source


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


async def test_incidents_returns_a_typed_auditable_evidence_graph() -> None:
    occurred = datetime(2026, 9, 12, 12, tzinfo=UTC)
    fire = Event(
        event_id="fire-1",
        event_type="fire.thermal_anomaly",
        source="firms",
        occurred_at=occurred,
        ingested_at=occurred,
        location=GeoPoint(latitude=12, longitude=77, altitude_km=None),
        payload={"place": "Test City", "source_url": "https://example.test/fire"},
    )
    conflict = Event(
        event_id="conflict-1",
        event_type="geopolitical.gdelt_event",
        source="gdelt",
        occurred_at=occurred,
        ingested_at=occurred,
        location=GeoPoint(latitude=12.1, longitude=77.1, altitude_km=None),
        payload={"place": "Test City", "source_url": "https://example.test/report"},
    )
    store = StubSignalStore(
        correlation_batch=CorrelationBatch(
            (
                CorrelationPair(
                    left=StreamMessage(stream_id="10-0", event=fire),
                    right=StreamMessage(stream_id="11-0", event=conflict),
                    distance_km=4.125,
                    time_delta_minutes=0,
                    left_geometry_basis="point",
                    right_geometry_basis="point",
                ),
            )
        )
    )
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus(), store))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/incidents",
            params={
                "limit": 10,
                "radius_km": 25,
                "time_window_minutes": 90,
                "lookback_hours": 48,
                "candidate_edge_limit": 250,
                "bbox": "70,5,85,20",
                "active_only": "false",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["total_incidents"] == 1
    assert body["rule_version"] == "spatiotemporal-v1"
    assert body["caveat"] == CORRELATION_CAVEAT
    assert body["items"][0]["sources"] == ["firms", "gdelt"]
    assert body["items"][0]["node_count"] == 2
    assert body["items"][0]["edge_count"] == 1
    assert body["items"][0]["edges"][0]["distance_km"] == 4.125
    assert body["items"][0]["edges"][0]["relation"] == "spatiotemporal_cooccurrence"
    assert body["parameters"] == {
        "radius_km": 25,
        "time_window_minutes": 90,
        "lookback_hours": 48,
        "candidate_edge_limit": 250,
        "incident_limit": 10,
        "active_only": False,
        "bbox": [70, 5, 85, 20],
    }
    assert store.correlation_query is not None
    assert store.correlation_query.edge_limit == 250
    assert store.correlation_query.bounds is not None


async def test_incidents_requires_projection_and_validates_bounds() -> None:
    without_store = httpx.ASGITransport(app=create_app(InMemoryEventBus()))
    async with httpx.AsyncClient(transport=without_store, base_url="http://test") as client:
        unavailable = await client.get("/v1/incidents")
    assert unavailable.status_code == 503

    with_store = httpx.ASGITransport(app=create_app(InMemoryEventBus(), StubSignalStore()))
    async with httpx.AsyncClient(transport=with_store, base_url="http://test") as client:
        invalid = await client.get("/v1/incidents", params={"bbox": "20,-5,-10,30"})
        unbounded = await client.get("/v1/incidents", params={"candidate_edge_limit": 5_001})
    assert invalid.status_code == 422
    assert unbounded.status_code == 422

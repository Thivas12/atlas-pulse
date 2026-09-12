"""HTTP API behavior tests."""

from datetime import UTC, datetime

import httpx
from agent_rag_core import Event

from atlas_pulse.api import create_app
from atlas_pulse.streams import InMemoryEventBus


class UnreadyBus(InMemoryEventBus):
    async def is_ready(self) -> bool:
        return False


class CloseTrackingBus(InMemoryEventBus):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

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
    assert health.json() == {"status": "ok", "version": "0.2.0"}
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
    app = create_app(bus)

    async with app.router.lifespan_context(app):
        assert bus.closed is False

    assert bus.closed is True


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

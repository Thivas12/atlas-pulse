"""Projection orchestration and geospatial contract tests."""

from datetime import UTC, datetime

import pytest
from agent_rag_core import Event

from atlas_pulse.projections import (
    GeoBounds,
    ProjectionService,
    SignalPage,
    SignalQuery,
)
from atlas_pulse.streams import InMemoryEventBus
from atlas_pulse.streams.base import StreamMessage


class RecordingProjectionStore:
    def __init__(self, *, checkpoint: str | None = None, fail: bool = False) -> None:
        self.checkpoint_value = checkpoint
        self.fail = fail
        self.projected: list[tuple[StreamMessage, ...]] = []

    async def checkpoint(self, projection_name: str) -> str | None:
        assert projection_name == "test-projection"
        return self.checkpoint_value

    async def project(
        self,
        *,
        projection_name: str,
        messages: tuple[StreamMessage, ...],
    ) -> None:
        assert projection_name == "test-projection"
        if self.fail:
            raise RuntimeError("transaction rolled back")
        self.projected.append(messages)
        self.checkpoint_value = messages[-1].stream_id

    async def query_current(self, query: SignalQuery) -> SignalPage:
        return SignalPage(items=(), next_cursor=None, has_more=False)

    async def is_ready(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def event(event_id: str) -> Event:
    return Event(
        event_id=event_id,
        event_type="seismic.earthquake",
        source="usgs",
        occurred_at=datetime(2024, 7, 10, tzinfo=UTC),
    )


async def test_projection_commits_one_bounded_page_after_checkpoint() -> None:
    bus = InMemoryEventBus()
    for event_id in ("one", "two", "three"):
        await bus.publish(event(event_id))
    store = RecordingProjectionStore(checkpoint="0-1")
    service = ProjectionService(
        event_bus=bus,
        store=store,
        projection_name="test-projection",
        batch_size=2,
    )

    result = await service.project_once()

    assert result.previous_checkpoint == "0-1"
    assert result.checkpoint == "0-3"
    assert result.projected_events == 2
    assert [[message.event.event_id for message in batch] for batch in store.projected] == [
        ["two", "three"]
    ]


async def test_projection_idle_cycle_preserves_checkpoint() -> None:
    service = ProjectionService(
        event_bus=InMemoryEventBus(),
        store=RecordingProjectionStore(checkpoint="10-2"),
        projection_name="test-projection",
        batch_size=10,
    )

    result = await service.project_once()

    assert result.checkpoint == "10-2"
    assert result.projected_events == 0


async def test_projection_failure_never_advances_checkpoint() -> None:
    bus = InMemoryEventBus()
    await bus.publish(event("one"))
    store = RecordingProjectionStore(fail=True)
    service = ProjectionService(
        event_bus=bus,
        store=store,
        projection_name="test-projection",
        batch_size=10,
    )

    with pytest.raises(RuntimeError, match="rolled back"):
        await service.project_once()

    assert store.checkpoint_value is None
    assert store.projected == []


def test_geo_bounds_accepts_a_non_wrapping_wgs84_viewport() -> None:
    assert GeoBounds(west=-180, south=-90, east=180, north=90).east == 180


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"west": -181, "south": 0, "east": 1, "north": 1}, "longitude"),
        ({"west": 0, "south": -91, "east": 1, "north": 1}, "latitude"),
        ({"west": 2, "south": 0, "east": 1, "north": 1}, "west"),
        ({"west": 0, "south": 2, "east": 1, "north": 1}, "south"),
        ({"west": float("nan"), "south": 0, "east": 1, "north": 1}, "finite"),
    ],
)
def test_geo_bounds_rejects_invalid_viewports(
    kwargs: dict[str, float],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        GeoBounds(**kwargs)

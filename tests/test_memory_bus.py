"""Offline event bus contract tests."""

from datetime import UTC, datetime

from agent_rag_core import Event

from atlas_pulse.streams.memory import InMemoryEventBus


def make_event(event_id: str) -> Event:
    return Event(
        event_id=event_id,
        event_type="seismic.earthquake",
        source="usgs",
        occurred_at=datetime(2024, 7, 10, tzinfo=UTC),
    )


async def test_memory_bus_is_idempotent_and_newest_first() -> None:
    bus = InMemoryEventBus()

    first = await bus.publish(make_event("one"))
    second = await bus.publish(make_event("two"))
    duplicate = await bus.publish(make_event("one"))

    assert first.deduplicated is False
    assert second.stream_id == "0-2"
    assert duplicate.deduplicated is True
    assert duplicate.stream_id == first.stream_id
    assert [message.event.event_id for message in await bus.latest(2)] == ["two", "one"]
    assert await bus.is_ready() is True
    await bus.close()


async def test_memory_bus_preserves_a_revised_source_event() -> None:
    bus = InMemoryEventBus()
    original = make_event("one")
    revised = original.model_copy(update={"payload": {"status": "reviewed"}})

    first = await bus.publish(original)
    update = await bus.publish(revised)

    assert first.deduplicated is False
    assert update.deduplicated is False
    assert len(await bus.latest(10)) == 2


async def test_memory_replay_uses_an_exclusive_numeric_cursor() -> None:
    bus = InMemoryEventBus()
    for index in range(12):
        await bus.publish(make_event(str(index)))

    page = await bus.replay(after="0-9", limit=2)
    exhausted = await bus.replay(after="0-99", limit=2)

    assert [message.stream_id for message in page] == ["0-10", "0-11"]
    assert exhausted == ()

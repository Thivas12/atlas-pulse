"""Real Valkey Streams contract test, enabled in CI and Docker workstations."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from agent_rag_core import Event

from atlas_pulse.streams import ValkeyEventBus

VALKEY_URL = os.getenv("ATLAS_TEST_VALKEY_URL")


@pytest.mark.integration
@pytest.mark.skipif(VALKEY_URL is None, reason="ATLAS_TEST_VALKEY_URL is not set")
async def test_real_valkey_atomic_duplicate_delivery() -> None:
    assert VALKEY_URL is not None
    bus = ValkeyEventBus(
        url=VALKEY_URL,
        stream="{atlas}:integration-events",
        max_length=100,
        dedupe_ttl_seconds=60,
    )
    event = Event(
        event_id=f"integration-{uuid4()}",
        event_type="seismic.earthquake",
        source="usgs",
        occurred_at=datetime.now(UTC),
    )
    later_event = Event(
        event_id=f"integration-{uuid4()}",
        event_type="seismic.earthquake",
        source="usgs",
        occurred_at=datetime.now(UTC),
    )

    try:
        first = await bus.publish(event)
        duplicate = await bus.publish(event)
        later = await bus.publish(later_event)
        latest = await bus.latest(100)
        replay = await bus.replay(after=first.stream_id, limit=100)
    finally:
        await bus.close()

    assert first.deduplicated is False
    assert duplicate.deduplicated is True
    assert duplicate.stream_id == first.stream_id
    assert sum(message.event.event_id == event.event_id for message in latest) == 1
    assert any(message.stream_id == later.stream_id for message in replay)
    assert all(message.stream_id != first.stream_id for message in replay)

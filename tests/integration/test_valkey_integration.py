"""Real Valkey Streams contract test, enabled in CI and Docker workstations."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from agent_rag_core import Event
from valkey.asyncio import Valkey

from atlas_pulse.projections.base import SourceName
from atlas_pulse.rate_limit import RateLimitPolicy, ValkeyRateLimiter
from atlas_pulse.source_poll_store import StaleSourcePollTransition, ValkeySourcePollStore
from atlas_pulse.source_polling import SourcePollAttempt, SourcePollPolicy, new_source_poll_attempt
from atlas_pulse.streams import ValkeyEventBus

VALKEY_URL = os.getenv("ATLAS_TEST_VALKEY_URL")


@pytest.mark.integration
@pytest.mark.skipif(VALKEY_URL is None, reason="ATLAS_TEST_VALKEY_URL is not set")
async def test_real_valkey_rate_limit_is_atomic_and_expires() -> None:
    assert VALKEY_URL is not None
    limiter = ValkeyRateLimiter(url=VALKEY_URL)
    client_key = sha256(uuid4().bytes).hexdigest()
    policy = RateLimitPolicy("integration", 2, 1)

    try:
        first = await limiter.consume(client_key, policy)
        second = await limiter.consume(client_key, policy)
        denied = await limiter.consume(client_key, policy)
        await asyncio.sleep(1.1)
        reset = await limiter.consume(client_key, policy)
    finally:
        await limiter.close()

    assert first.allowed is True
    assert first.remaining == 1
    assert second.allowed is True
    assert second.remaining == 0
    assert denied.allowed is False
    assert reset.allowed is True
    assert reset.remaining == 1


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
        first, duplicate, later = await bus.publish_many((event, event, later_event))
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


@pytest.mark.integration
@pytest.mark.skipif(VALKEY_URL is None, reason="ATLAS_TEST_VALKEY_URL is not set")
async def test_real_valkey_source_poll_state_rejects_superseded_completion() -> None:
    assert VALKEY_URL is not None
    source: SourceName = "usgs"
    state_key = "{atlas}:source-poll:usgs"
    history_stream = f"{{atlas}}:integration-source-polls-{uuid4().hex}"
    client = Valkey.from_url(VALKEY_URL, decode_responses=False)
    store = ValkeySourcePollStore(
        url=VALKEY_URL,
        history_stream=history_stream,
        history_max_length=100,
    )
    policy = SourcePollPolicy(
        source=source,
        interval_seconds=60,
        poll_stale_after_seconds=180,
        source_stale_after_seconds=600,
    )
    now = datetime.now(UTC)
    first = new_source_poll_attempt(source, started_at=now)
    success = SourcePollAttempt(
        attempt_id=first.attempt_id,
        source=source,
        started_at=first.started_at,
        completed_at=now,
        outcome="succeeded",
        stage="complete",
        transport_attempts=1,
        source_generated_at=now,
        timestamp_basis="source_metadata",
        fetched_events=1,
        published_events=1,
        deduplicated_events=0,
    )
    second = new_source_poll_attempt(source, started_at=now + timedelta(microseconds=1))

    try:
        await client.delete(state_key, history_stream)
        await store.record_started(first, policy)
        await store.record_completed(success)
        await store.record_started(second, policy)
        with pytest.raises(StaleSourcePollTransition, match="stale or duplicated"):
            await store.record_completed(success)
        state = (await store.load_states((source,)))[0]
        first_page = await store.load_recent_transitions(before=None, limit=2)
        second_page = await store.load_recent_transitions(
            before=first_page[-1].stream_id,
            limit=2,
        )
    finally:
        await client.delete(state_key, history_stream)
        await client.aclose()
        await store.close()

    assert state.current_attempt == second
    assert state.last_success == success
    assert state.consecutive_failures == 0
    assert [item.transition for item in first_page] == ["started", "succeeded"]
    assert [item.transition for item in second_page] == ["started"]

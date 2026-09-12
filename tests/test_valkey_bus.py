"""Valkey adapter unit tests without an external service."""

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from agent_rag_core import Event

from atlas_pulse.streams.valkey_bus import ValkeyEventBus, _as_text


def make_event(event_id: str = "us-test") -> Event:
    return Event(
        event_id=event_id,
        event_type="seismic.earthquake",
        source="usgs",
        occurred_at=datetime(2024, 7, 10, tzinfo=UTC),
    )


class FakeValkey:
    def __init__(self) -> None:
        self.dedupe: dict[str, bytes] = {}
        self.messages: list[tuple[bytes, Mapping[bytes, bytes]]] = []
        self.closed = False
        self.ready = True
        self.pipeline_batches: list[int] = []

    async def eval(self, _script: str, _numkeys: int, *keys_and_args: str | bytes | int) -> object:
        dedupe_key = str(keys_and_args[0])
        existing = self.dedupe.get(dedupe_key)
        if existing is not None:
            return [0, existing]
        stream_id = f"1-{len(self.messages)}".encode()
        event_json = str(keys_and_args[3]).encode()
        self.dedupe[dedupe_key] = stream_id
        self.messages.append((stream_id, {b"event": event_json}))
        return [1, stream_id]

    async def xrevrange(
        self, _name: str, max: str = "+", min: str = "-", count: int | None = None
    ) -> object:
        del max, min
        messages = list(reversed(self.messages))
        return messages[:count]

    async def xrange(
        self, _name: str, min: str = "-", max: str = "+", count: int | None = None
    ) -> object:
        del max
        messages = self.messages
        if min.startswith("("):
            cursor = min[1:].encode()
            messages = [message for message in messages if message[0] > cursor]
        return messages[:count]

    async def ping(self) -> object:
        if not self.ready:
            raise ConnectionError("offline")
        return True

    async def aclose(self) -> None:
        self.closed = True

    def pipeline(self, transaction: bool = True) -> "FakePipeline":
        assert transaction is False
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, client: FakeValkey) -> None:
        self.client = client
        self.commands: list[tuple[str, int, tuple[str | bytes | int, ...]]] = []

    def eval(self, script: str, numkeys: int, *keys_and_args: str | bytes | int) -> object:
        self.commands.append((script, numkeys, keys_and_args))
        return self

    async def execute(self) -> object:
        self.client.pipeline_batches.append(len(self.commands))
        return [
            await self.client.eval(script, numkeys, *keys_and_args)
            for script, numkeys, keys_and_args in self.commands
        ]

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


async def test_valkey_bus_publishes_once_and_decodes_latest() -> None:
    client = FakeValkey()
    bus = ValkeyEventBus(
        url="valkey://unused",
        stream="{atlas}:events",
        max_length=100,
        dedupe_ttl_seconds=60,
        client=client,
    )

    first = await bus.publish(make_event())
    duplicate = await bus.publish(make_event())
    messages = await bus.latest(10)

    assert first.stream_id == "1-0"
    assert first.deduplicated is False
    assert duplicate == type(first)(stream_id="1-0", deduplicated=True)
    assert messages[0].event.event_id == "us-test"
    assert messages[0].stream_id == "1-0"
    assert await bus.is_ready() is True
    await bus.close()
    assert client.closed is False


async def test_valkey_bus_pipelines_large_source_batches_with_atomic_dedupe() -> None:
    client = FakeValkey()
    bus = ValkeyEventBus(
        url="valkey://unused",
        stream="{atlas}:events",
        max_length=1_000,
        dedupe_ttl_seconds=60,
        client=client,
    )
    events = tuple(make_event(str(index)) for index in range(501))

    first = await bus.publish_many(events)
    repeated = await bus.publish_many(events[:2])

    assert len(first) == 501
    assert all(not result.deduplicated for result in first)
    assert all(result.deduplicated for result in repeated)
    assert client.pipeline_batches == [500, 1, 2]
    assert await bus.publish_many(()) == ()


async def test_valkey_replay_uses_exclusive_xrange_cursor() -> None:
    client = FakeValkey()
    bus = ValkeyEventBus(
        url="valkey://unused",
        stream="{atlas}:events",
        max_length=100,
        dedupe_ttl_seconds=60,
        client=client,
    )
    first = await bus.publish(make_event("one"))
    second = await bus.publish(make_event("two"))

    page = await bus.replay(after=first.stream_id, limit=10)
    all_messages = await bus.replay(after=None, limit=10)

    assert [message.stream_id for message in page] == [second.stream_id]
    assert [message.event.event_id for message in all_messages] == ["one", "two"]


async def test_valkey_readiness_absorbs_connection_failure() -> None:
    client = FakeValkey()
    client.ready = False
    bus = ValkeyEventBus(
        url="valkey://unused",
        stream="{atlas}:events",
        max_length=100,
        dedupe_ttl_seconds=60,
        client=client,
    )

    assert await bus.is_ready() is False


def test_text_decoder_accepts_protocol_two_and_three_values() -> None:
    assert _as_text(b"1-0") == "1-0"
    assert _as_text("1-0") == "1-0"
    with pytest.raises(TypeError, match="received int"):
        _as_text(1)


@pytest.mark.parametrize("reply", [None, [1], [1, b"id", b"extra"]])
async def test_publish_rejects_malformed_script_replies(reply: object) -> None:
    class MalformedValkey(FakeValkey):
        async def eval(
            self, _script: str, _numkeys: int, *keys_and_args: str | bytes | int
        ) -> object:
            del keys_and_args
            return reply

    bus = ValkeyEventBus(
        url="valkey://unused",
        stream="{atlas}:events",
        max_length=100,
        dedupe_ttl_seconds=60,
        client=MalformedValkey(),
    )
    with pytest.raises((TypeError, ValueError)):
        await bus.publish(make_event())


@pytest.mark.parametrize("reply", [None, [object()], [[1, b"1-0"], [1, b"2-0"]]])
async def test_publish_many_rejects_malformed_pipeline_replies(reply: object) -> None:
    class MalformedPipeline(FakePipeline):
        async def execute(self) -> object:
            return reply

    class MalformedValkey(FakeValkey):
        def pipeline(self, transaction: bool = True) -> FakePipeline:
            assert transaction is False
            return MalformedPipeline(self)

    bus = ValkeyEventBus(
        url="valkey://unused",
        stream="{atlas}:events",
        max_length=100,
        dedupe_ttl_seconds=60,
        client=MalformedValkey(),
    )
    with pytest.raises((TypeError, ValueError)):
        await bus.publish_many((make_event(),))


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (None, "invalid response"),
        ([b"only-id"], "invalid shape"),
        ([(b"1-0", b"not-a-map")], "invalid shape"),
        ([(b"1-0", {b"other": b"value"})], "missing the event"),
    ],
)
async def test_latest_rejects_malformed_stream_entries(reply: object, message: str) -> None:
    class MalformedValkey(FakeValkey):
        async def xrevrange(
            self, _name: str, max: str = "+", min: str = "-", count: int | None = None
        ) -> object:
            del max, min, count
            return reply

    bus = ValkeyEventBus(
        url="valkey://unused",
        stream="{atlas}:events",
        max_length=100,
        dedupe_ttl_seconds=60,
        client=MalformedValkey(),
    )
    with pytest.raises((TypeError, ValueError), match=message):
        await bus.latest(10)


async def test_owned_valkey_client_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeValkey()
    monkeypatch.setattr(
        "atlas_pulse.streams.valkey_bus.Valkey.from_url",
        lambda *_args, **_kwargs: client,
    )
    bus = ValkeyEventBus(
        url="valkey://unused",
        stream="{atlas}:events",
        max_length=100,
        dedupe_ttl_seconds=60,
    )

    await bus.close()

    assert client.closed is True

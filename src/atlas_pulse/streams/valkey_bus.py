"""Valkey Streams event bus with atomic idempotent publication."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, Self, cast

from agent_rag_core import Event
from valkey.asyncio import Valkey

from atlas_pulse.streams.base import PublishResult, StreamMessage, event_fingerprint

_PUBLISH_ONCE = """
local existing = redis.call('GET', KEYS[1])
if existing then
  return {0, existing}
end
local stream_id = redis.call(
  'XADD', KEYS[2], 'MAXLEN', '~', ARGV[1], '*', 'event', ARGV[2]
)
redis.call('SET', KEYS[1], stream_id, 'EX', ARGV[3])
return {1, stream_id}
"""
_PUBLISH_PIPELINE_SIZE = 500


class AsyncValkeyPipeline(Protocol):
    """Commands needed from a non-transactional async Valkey pipeline."""

    def eval(self, script: str, numkeys: int, *keys_and_args: str | bytes | int) -> object: ...

    async def execute(self) -> object: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, *_exc: object) -> None: ...


class AsyncValkeyClient(Protocol):
    """Narrow structural type used by the adapter and its unit tests."""

    async def eval(
        self, script: str, numkeys: int, *keys_and_args: str | bytes | int
    ) -> object: ...

    async def xrevrange(
        self, name: str, max: str = "+", min: str = "-", count: int | None = None
    ) -> object: ...

    async def xrange(
        self, name: str, min: str = "-", max: str = "+", count: int | None = None
    ) -> object: ...

    async def ping(self) -> object: ...

    async def aclose(self) -> None: ...

    def pipeline(self, transaction: bool = True) -> AsyncValkeyPipeline: ...


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise TypeError(f"expected bytes or str, received {type(value).__name__}")


class ValkeyEventBus:
    """Valkey-backed stream with atomic dedupe-key and XADD writes."""

    def __init__(
        self,
        *,
        url: str,
        stream: str,
        max_length: int,
        dedupe_ttl_seconds: int,
        client: AsyncValkeyClient | None = None,
    ) -> None:
        self._stream = stream
        self._max_length = max_length
        self._dedupe_ttl_seconds = dedupe_ttl_seconds
        self._owns_client = client is None
        self._client = client or cast(
            AsyncValkeyClient,
            Valkey.from_url(url, decode_responses=False),
        )

    def _dedupe_key(self, event: Event) -> str:
        return f"{{atlas}}:dedupe:{event_fingerprint(event)}"

    def _queue_publish(self, target: AsyncValkeyPipeline, event: Event) -> None:
        target.eval(
            _PUBLISH_ONCE,
            2,
            self._dedupe_key(event),
            self._stream,
            self._max_length,
            event.model_dump_json(),
            self._dedupe_ttl_seconds,
        )

    @staticmethod
    def _decode_publish_result(result: object) -> PublishResult:
        if not isinstance(result, Sequence) or isinstance(result, (str, bytes)):
            raise TypeError("Valkey publish script returned an invalid response")
        if len(result) != 2:
            raise ValueError("Valkey publish script returned an unexpected response length")
        created = int(_as_text(result[0])) if isinstance(result[0], bytes | str) else int(result[0])
        return PublishResult(
            stream_id=_as_text(result[1]),
            deduplicated=not bool(created),
        )

    async def publish(self, event: Event) -> PublishResult:
        result = await self._client.eval(
            _PUBLISH_ONCE,
            2,
            self._dedupe_key(event),
            self._stream,
            self._max_length,
            event.model_dump_json(),
            self._dedupe_ttl_seconds,
        )
        return self._decode_publish_result(result)

    async def publish_many(self, events: tuple[Event, ...]) -> tuple[PublishResult, ...]:
        """Pipeline bounded groups while retaining one atomic Lua decision per event."""
        outcomes: list[PublishResult] = []
        for start in range(0, len(events), _PUBLISH_PIPELINE_SIZE):
            batch = events[start : start + _PUBLISH_PIPELINE_SIZE]
            async with self._client.pipeline(transaction=False) as pipeline:
                for event in batch:
                    self._queue_publish(pipeline, event)
                raw_results = await pipeline.execute()
            if not isinstance(raw_results, Sequence) or isinstance(raw_results, (str, bytes)):
                raise TypeError("Valkey publish pipeline returned an invalid response")
            if len(raw_results) != len(batch):
                raise ValueError("Valkey publish pipeline returned an unexpected response length")
            outcomes.extend(self._decode_publish_result(result) for result in raw_results)
        return tuple(outcomes)

    async def latest(self, limit: int) -> tuple[StreamMessage, ...]:
        raw_messages = await self._client.xrevrange(self._stream, count=limit)
        return self._decode_messages(raw_messages)

    async def replay(self, *, after: str | None, limit: int) -> tuple[StreamMessage, ...]:
        start = f"({after}" if after is not None else "-"
        raw_messages = await self._client.xrange(self._stream, min=start, count=limit)
        return self._decode_messages(raw_messages)

    def _decode_messages(self, raw_messages: object) -> tuple[StreamMessage, ...]:
        if not isinstance(raw_messages, Sequence) or isinstance(raw_messages, (str, bytes)):
            raise TypeError("Valkey stream range returned an invalid response")

        messages: list[StreamMessage] = []
        for raw_message in raw_messages:
            if not isinstance(raw_message, Sequence) or len(raw_message) != 2:
                raise TypeError("Valkey stream entry has an invalid shape")
            stream_id, raw_fields = raw_message
            if not isinstance(raw_fields, Mapping):
                raise TypeError("Valkey stream fields have an invalid shape")
            event_json = raw_fields.get(b"event") or raw_fields.get("event")
            if event_json is None:
                raise ValueError("Valkey stream entry is missing the event field")
            messages.append(
                StreamMessage(
                    stream_id=_as_text(stream_id),
                    event=Event.model_validate_json(event_json),
                )
            )
        return tuple(messages)

    async def is_ready(self) -> bool:
        try:
            return bool(await self._client.ping())
        except (ConnectionError, OSError, TimeoutError):
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

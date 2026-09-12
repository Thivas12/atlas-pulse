"""Deterministic in-memory bus for tests and offline demonstrations."""

from agent_rag_core import Event

from atlas_pulse.streams.base import PublishResult, StreamMessage, event_fingerprint


class InMemoryEventBus:
    """Small event bus preserving the production idempotency contract."""

    def __init__(self) -> None:
        self._messages: list[StreamMessage] = []
        self._dedupe: dict[str, str] = {}

    async def publish(self, event: Event) -> PublishResult:
        fingerprint = event_fingerprint(event)
        existing_id = self._dedupe.get(fingerprint)
        if existing_id is not None:
            return PublishResult(stream_id=existing_id, deduplicated=True)

        stream_id = f"0-{len(self._messages) + 1}"
        self._messages.append(StreamMessage(stream_id=stream_id, event=event))
        self._dedupe[fingerprint] = stream_id
        return PublishResult(stream_id=stream_id, deduplicated=False)

    async def publish_many(self, events: tuple[Event, ...]) -> tuple[PublishResult, ...]:
        outcomes: list[PublishResult] = []
        for event in events:
            outcomes.append(await self.publish(event))
        return tuple(outcomes)

    async def latest(self, limit: int) -> tuple[StreamMessage, ...]:
        return tuple(reversed(self._messages[-limit:]))

    async def replay(self, *, after: str | None, limit: int) -> tuple[StreamMessage, ...]:
        start_index = 0
        if after is not None:
            cursor = tuple(int(part) for part in after.split("-", maxsplit=1))
            start_index = len(self._messages)
            for index, message in enumerate(self._messages):
                message_id = tuple(int(part) for part in message.stream_id.split("-", maxsplit=1))
                if message_id > cursor:
                    start_index = index
                    break
        return tuple(self._messages[start_index : start_index + limit])

    async def is_ready(self) -> bool:
        return True

    async def close(self) -> None:
        return None

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

    async def latest(self, limit: int) -> tuple[StreamMessage, ...]:
        return tuple(reversed(self._messages[-limit:]))

    async def is_ready(self) -> bool:
        return True

    async def close(self) -> None:
        return None

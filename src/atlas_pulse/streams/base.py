"""Source-neutral event bus contract."""

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from agent_rag_core import Event


def event_fingerprint(event: Event) -> str:
    """Hash semantic event content while ignoring poll-specific ingestion time."""
    canonical = json.dumps(
        event.model_dump(mode="json", exclude={"ingested_at"}),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class PublishResult:
    """Outcome of one idempotent publication attempt."""

    stream_id: str
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class StreamMessage:
    """Event plus the stream position needed for replay."""

    stream_id: str
    event: Event


class EventBus(Protocol):
    """Minimal capability required by ingestion and the read API."""

    async def publish(self, event: Event) -> PublishResult:
        """Publish each distinct event revision once and return its stable stream id."""
        ...

    async def publish_many(self, events: tuple[Event, ...]) -> tuple[PublishResult, ...]:
        """Publish a source batch without one network round trip per event."""
        ...

    async def latest(self, limit: int) -> tuple[StreamMessage, ...]:
        """Return newest messages first."""
        ...

    async def replay(self, *, after: str | None, limit: int) -> tuple[StreamMessage, ...]:
        """Return a stable oldest-first page strictly after an optional cursor."""
        ...

    async def is_ready(self) -> bool:
        """Return whether the backing stream is available."""
        ...

    async def close(self) -> None:
        """Release owned resources."""
        ...

"""Source-neutral ingestion contracts."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from agent_rag_core import Event


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """Unmodified source bytes and transport metadata."""

    raw: bytes
    fetched_at: datetime
    source_url: str
    content_type: str | None


@dataclass(frozen=True, slots=True)
class NormalizedBatch:
    """Validated source records normalized to the shared event contract."""

    generated_at: datetime
    events: tuple[Event, ...]


class SourceAdapter(Protocol):
    """Complete capability required by the source-neutral ingestion service."""

    source_name: str
    snapshot_extension: str

    async def fetch(self) -> FetchedDocument:
        """Fetch one immutable source document."""
        ...

    def normalize(self, raw: bytes, *, ingested_at: datetime) -> NormalizedBatch:
        """Validate source bytes and convert every record to a shared event."""
        ...

    async def close(self) -> None:
        """Release resources owned by the adapter."""
        ...

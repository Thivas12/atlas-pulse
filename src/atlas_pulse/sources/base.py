"""Source-neutral ingestion contracts."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from agent_rag_core import Event

from atlas_pulse.projections.base import SourceName


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """Unmodified source bytes and transport metadata."""

    raw: bytes
    fetched_at: datetime
    source_url: str
    content_type: str | None
    transport_attempts: int = 1


@dataclass(frozen=True, slots=True)
class NormalizedBatch:
    """Validated source records normalized to the shared event contract."""

    generated_at: datetime
    events: tuple[Event, ...]
    timestamp_basis: Literal["source_metadata", "latest_record", "fetch_fallback"] = (
        "source_metadata"
    )


class SourceAdapter(Protocol):
    """Complete capability required by the source-neutral ingestion service."""

    @property
    def source_name(self) -> SourceName:
        """Canonical source identifier."""
        ...

    @property
    def snapshot_extension(self) -> str:
        """Credential-free immutable snapshot suffix."""
        ...

    async def fetch(self) -> FetchedDocument:
        """Fetch one immutable source document."""
        ...

    def normalize(self, raw: bytes, *, ingested_at: datetime) -> NormalizedBatch:
        """Validate source bytes and convert every record to a shared event."""
        ...

    async def close(self) -> None:
        """Release resources owned by the adapter."""
        ...

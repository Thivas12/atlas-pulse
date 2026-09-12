"""Contracts shared by projection workers and read APIs."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Literal, Protocol

from atlas_pulse.streams import StreamMessage

SourceName = Literal["usgs", "nws"]


@dataclass(frozen=True, slots=True)
class GeoBounds:
    """Non-wrapping WGS84 viewport bounds."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        if not all(isfinite(value) for value in (self.west, self.south, self.east, self.north)):
            raise ValueError("viewport coordinates must be finite")
        if not -180 <= self.west <= 180 or not -180 <= self.east <= 180:
            raise ValueError("longitude must be within [-180, 180]")
        if not -90 <= self.south <= 90 or not -90 <= self.north <= 90:
            raise ValueError("latitude must be within [-90, 90]")
        if self.west >= self.east:
            raise ValueError("west must be smaller than east")
        if self.south >= self.north:
            raise ValueError("south must be smaller than north")


@dataclass(frozen=True, slots=True)
class SignalQuery:
    """Validated current-state query translated to indexed SQL."""

    limit: int = 100
    after: str | None = None
    source: SourceName | None = None
    min_severity: int | None = None
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None
    bounds: GeoBounds | None = None
    include_area_only: bool = False
    active_only: bool = True


@dataclass(frozen=True, slots=True)
class SignalPage:
    """Newest-revision-first current signals with an exclusive cursor."""

    items: tuple[StreamMessage, ...]
    next_cursor: str | None
    has_more: bool


class SignalStore(Protocol):
    """Current-state capability required by the HTTP API."""

    async def query_current(self, query: SignalQuery) -> SignalPage:
        """Query one keyset-paginated current-state page."""
        ...

    async def is_ready(self) -> bool:
        """Return whether the durable store accepts queries."""
        ...

    async def close(self) -> None:
        """Release owned resources."""
        ...


class ProjectionStore(SignalStore, Protocol):
    """Transactional checkpoint and projection capability."""

    async def checkpoint(self, projection_name: str) -> str | None:
        """Return the last atomically committed stream position."""
        ...

    async def project(
        self,
        *,
        projection_name: str,
        messages: tuple[StreamMessage, ...],
    ) -> None:
        """Persist revisions, current rows, and checkpoint in one transaction."""
        ...

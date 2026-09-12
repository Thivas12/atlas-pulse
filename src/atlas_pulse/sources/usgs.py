"""USGS earthquake GeoJSON source adapter."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Self

import httpx
from agent_rag_core import Event, GeoPoint
from pydantic import BaseModel, Field, HttpUrl, model_validator
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential


class USGSProperties(BaseModel):
    """Fields used from a USGS earthquake feature."""

    mag: float | None
    place: str | None
    time: int
    updated: int
    url: HttpUrl
    detail: HttpUrl
    status: str
    tsunami: int
    sig: int
    net: str
    code: str
    magType: str | None = None
    type: str
    title: str


class USGSGeometry(BaseModel):
    """GeoJSON point emitted by the earthquake feed."""

    type: Literal["Point"]
    coordinates: tuple[float, float, float]


class USGSFeature(BaseModel):
    """One validated USGS earthquake feature."""

    type: Literal["Feature"]
    properties: USGSProperties
    geometry: USGSGeometry
    id: str = Field(min_length=1)

    def to_event(self, *, ingested_at: datetime) -> Event:
        """Convert source-specific GeoJSON into the shared event contract."""
        longitude, latitude, depth_km = self.geometry.coordinates
        properties = self.properties
        return Event(
            event_id=self.id,
            event_type=f"seismic.{properties.type}",
            source="usgs",
            occurred_at=datetime.fromtimestamp(properties.time / 1000, tz=UTC),
            ingested_at=ingested_at,
            location=GeoPoint(
                latitude=latitude,
                longitude=longitude,
                altitude_km=-depth_km,
            ),
            payload={
                "magnitude": properties.mag,
                "magnitude_type": properties.magType,
                "place": properties.place,
                "depth_km": depth_km,
                "updated_at": datetime.fromtimestamp(properties.updated / 1000, tz=UTC).isoformat(),
                "status": properties.status,
                "tsunami": bool(properties.tsunami),
                "significance": properties.sig,
                "network": properties.net,
                "code": properties.code,
                "title": properties.title,
                "source_url": str(properties.url),
                "detail_url": str(properties.detail),
            },
        )


class USGSMetadata(BaseModel):
    """Metadata attached to the feed response."""

    generated: int
    url: HttpUrl
    title: str
    status: int
    api: str
    count: int = Field(ge=0)


class USGSFeed(BaseModel):
    """Validated top-level USGS FeatureCollection."""

    type: Literal["FeatureCollection"]
    metadata: USGSMetadata
    features: tuple[USGSFeature, ...]

    @model_validator(mode="after")
    def validate_feature_count(self) -> Self:
        """Reject truncated or internally inconsistent source payloads."""
        if self.metadata.count != len(self.features):
            raise ValueError(
                f"metadata.count={self.metadata.count} does not match features={len(self.features)}"
            )
        return self

    @classmethod
    def from_bytes(cls, raw: bytes) -> Self:
        """Validate a raw JSON response without mutating it."""
        return cls.model_validate_json(raw)

    def to_events(self, *, ingested_at: datetime) -> tuple[Event, ...]:
        """Convert every feature to the shared contract."""
        return tuple(feature.to_event(ingested_at=ingested_at) for feature in self.features)


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """Unmodified source bytes and transport metadata."""

    raw: bytes
    fetched_at: datetime
    source_url: str
    content_type: str | None


class RetryableSourceError(RuntimeError):
    """A transient source failure that may succeed on another attempt."""


class USGSClient:
    """Bounded, retrying asynchronous client for the public USGS feed."""

    def __init__(
        self,
        *,
        feed_url: str,
        timeout_seconds: float,
        max_attempts: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._feed_url = feed_url
        self._max_attempts = max_attempts
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": "AtlasPulse/0.1 (+https://github.com/Thivas12/atlas-pulse)"},
        )

    async def fetch(self) -> FetchedDocument:
        """Fetch the feed, retrying only transport, rate-limit, and server failures."""
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
            retry=retry_if_exception_type((httpx.TransportError, RetryableSourceError)),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                response = await self._client.get(self._feed_url)
                if response.status_code == 429 or response.status_code >= 500:
                    raise RetryableSourceError(
                        f"USGS returned retryable HTTP {response.status_code}"
                    )
                response.raise_for_status()
                return FetchedDocument(
                    raw=response.content,
                    fetched_at=datetime.now(UTC),
                    source_url=str(response.url),
                    content_type=response.headers.get("content-type"),
                )
        raise AssertionError("retry loop completed without a response")  # pragma: no cover

    async def close(self) -> None:
        """Close only clients created by this adapter."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

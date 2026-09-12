"""USGS earthquake GeoJSON source adapter."""

from datetime import UTC, datetime
from typing import Literal, Self

import httpx
from agent_rag_core import Event, GeoPoint
from pydantic import BaseModel, Field, HttpUrl, model_validator

from atlas_pulse.sources.base import NormalizedBatch
from atlas_pulse.sources.http import RetryingHttpClient


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


class USGSClient(RetryingHttpClient):
    """Bounded, retrying asynchronous client for the public USGS feed."""

    source_name = "usgs"
    snapshot_extension = "geojson"

    def __init__(
        self,
        *,
        feed_url: str,
        timeout_seconds: float,
        max_attempts: int,
        user_agent: str = "AtlasPulse/0.2 (+https://github.com/Thivas12/atlas-pulse)",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            source_name=self.source_name,
            url=feed_url,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            user_agent=user_agent,
            accept="application/geo+json",
            client=client,
        )

    def normalize(self, raw: bytes, *, ingested_at: datetime) -> NormalizedBatch:
        """Validate and normalize a complete USGS feed."""
        feed = USGSFeed.from_bytes(raw)
        return NormalizedBatch(
            generated_at=datetime.fromtimestamp(feed.metadata.generated / 1000, tz=UTC),
            events=feed.to_events(ingested_at=ingested_at),
        )

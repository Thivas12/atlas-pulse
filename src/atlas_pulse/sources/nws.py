"""National Weather Service active-alert GeoJSON source adapter."""

from datetime import datetime
from typing import Annotated, Literal, Self

import httpx
from agent_rag_core import Event, GeoPoint
from pydantic import BaseModel, ConfigDict, Field, field_validator

from atlas_pulse.sources.base import NormalizedBatch
from atlas_pulse.sources.http import RetryingHttpClient

type Position = tuple[float, float]
type LinearRing = tuple[Position, ...]
type PolygonCoordinates = tuple[LinearRing, ...]
type MultiPolygonCoordinates = tuple[PolygonCoordinates, ...]


def _validate_polygon(coordinates: PolygonCoordinates) -> PolygonCoordinates:
    if not coordinates:
        raise ValueError("polygon must contain at least one linear ring")
    for ring in coordinates:
        if len(ring) < 4:
            raise ValueError("linear ring must contain at least four positions")
        if ring[0] != ring[-1]:
            raise ValueError("linear ring must be closed")
        for longitude, latitude in ring:
            if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                raise ValueError("geometry coordinate is outside WGS84 bounds")
    return coordinates


class NWSPolygonGeometry(BaseModel):
    """A directly mappable NWS alert polygon."""

    type: Literal["Polygon"]
    coordinates: PolygonCoordinates

    @field_validator("coordinates")
    @classmethod
    def validate_coordinates(cls, value: PolygonCoordinates) -> PolygonCoordinates:
        return _validate_polygon(value)


class NWSMultiPolygonGeometry(BaseModel):
    """A directly mappable NWS alert multipolygon."""

    type: Literal["MultiPolygon"]
    coordinates: MultiPolygonCoordinates

    @field_validator("coordinates")
    @classmethod
    def validate_coordinates(cls, value: MultiPolygonCoordinates) -> MultiPolygonCoordinates:
        if not value:
            raise ValueError("multipolygon must contain at least one polygon")
        for polygon in value:
            _validate_polygon(polygon)
        return value


type NWSGeometry = Annotated[
    NWSPolygonGeometry | NWSMultiPolygonGeometry,
    Field(discriminator="type"),
]


class NWSAlertProperties(BaseModel):
    """CAP fields used from an official NWS alert."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(min_length=1)
    area_desc: str = Field(alias="areaDesc", min_length=1)
    geocode: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    affected_zones: tuple[str, ...] = Field(default_factory=tuple, alias="affectedZones")
    sent: datetime
    effective: datetime
    onset: datetime | None = None
    expires: datetime
    ends: datetime | None = None
    status: Literal["Actual", "Exercise", "System", "Test", "Draft"]
    message_type: Literal["Alert", "Update", "Cancel", "Ack", "Error"] = Field(alias="messageType")
    category: Literal[
        "Met",
        "Geo",
        "Safety",
        "Security",
        "Rescue",
        "Fire",
        "Health",
        "Env",
        "Transport",
        "Infra",
        "CBRNE",
        "Other",
    ]
    severity: Literal["Extreme", "Severe", "Moderate", "Minor", "Unknown"]
    certainty: Literal["Observed", "Likely", "Possible", "Unlikely", "Unknown"]
    urgency: Literal["Immediate", "Expected", "Future", "Past", "Unknown"]
    event: str = Field(min_length=1)
    sender: str
    sender_name: str = Field(alias="senderName")
    headline: str | None = None
    description: str
    instruction: str | None = None
    response: str | None = None
    parameters: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    event_code: dict[str, tuple[str, ...]] = Field(default_factory=dict, alias="eventCode")
    scope: str
    code: str
    language: str


class NWSAlertFeature(BaseModel):
    """One official alert, including optional direct polygon geometry."""

    type: Literal["Feature"]
    id: str = Field(min_length=1)
    geometry: NWSGeometry | None
    properties: NWSAlertProperties

    def to_event(self, *, ingested_at: datetime) -> Event:
        """Convert an NWS CAP alert to the shared event contract."""
        properties = self.properties
        geometry = self.geometry.model_dump(mode="json") if self.geometry else None
        return Event(
            event_id=properties.id,
            event_type="weather.alert",
            source="nws",
            occurred_at=properties.onset or properties.effective,
            ingested_at=ingested_at,
            location=self._representative_location(),
            payload={
                "alert_type": properties.event,
                "place": properties.area_desc,
                "severity": properties.severity,
                "severity_rank": _SEVERITY_RANK[properties.severity],
                "certainty": properties.certainty,
                "urgency": properties.urgency,
                "status": properties.status,
                "message_type": properties.message_type,
                "category": properties.category,
                "response": properties.response,
                "headline": properties.headline,
                "title": properties.headline or f"{properties.event} for {properties.area_desc}",
                "description": properties.description,
                "instruction": properties.instruction,
                "sent_at": properties.sent.isoformat(),
                "effective_at": properties.effective.isoformat(),
                "onset_at": properties.onset.isoformat() if properties.onset else None,
                "expires_at": properties.expires.isoformat(),
                "ends_at": properties.ends.isoformat() if properties.ends else None,
                "updated_at": properties.sent.isoformat(),
                "sender": properties.sender,
                "sender_name": properties.sender_name,
                "affected_zones": list(properties.affected_zones),
                "geocode": {key: list(value) for key, value in properties.geocode.items()},
                "parameters": {key: list(value) for key, value in properties.parameters.items()},
                "event_code": {key: list(value) for key, value in properties.event_code.items()},
                "scope": properties.scope,
                "code": properties.code,
                "language": properties.language,
                "geometry": geometry,
                "source_url": self.id,
            },
        )

    def _representative_location(self) -> GeoPoint | None:
        """Return a stable bbox center for map focus without replacing source geometry."""
        if self.geometry is None:
            return None
        if isinstance(self.geometry, NWSPolygonGeometry):
            positions = [position for ring in self.geometry.coordinates for position in ring]
        else:
            positions = [
                position
                for polygon in self.geometry.coordinates
                for ring in polygon
                for position in ring
            ]
        longitudes = [position[0] for position in positions]
        latitudes = [position[1] for position in positions]
        return GeoPoint(
            latitude=(min(latitudes) + max(latitudes)) / 2,
            longitude=(min(longitudes) + max(longitudes)) / 2,
        )


class NWSAlertCollection(BaseModel):
    """Validated NWS active-alert FeatureCollection."""

    type: Literal["FeatureCollection"]
    title: str
    updated: datetime
    features: tuple[NWSAlertFeature, ...]

    @classmethod
    def from_bytes(cls, raw: bytes) -> Self:
        return cls.model_validate_json(raw)

    def to_events(self, *, ingested_at: datetime) -> tuple[Event, ...]:
        return tuple(feature.to_event(ingested_at=ingested_at) for feature in self.features)


_SEVERITY_RANK = {
    "Unknown": 0,
    "Minor": 1,
    "Moderate": 2,
    "Severe": 3,
    "Extreme": 4,
}


class NWSClient(RetryingHttpClient):
    """Official NWS active-alert adapter with the required identifiable User-Agent."""

    source_name = "nws"
    snapshot_extension = "geojson"

    def __init__(
        self,
        *,
        alerts_url: str,
        timeout_seconds: float,
        max_attempts: int,
        user_agent: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            source_name=self.source_name,
            url=alerts_url,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            user_agent=user_agent,
            accept="application/geo+json",
            client=client,
        )

    def normalize(self, raw: bytes, *, ingested_at: datetime) -> NormalizedBatch:
        """Validate and normalize a complete NWS active-alert collection."""
        collection = NWSAlertCollection.from_bytes(raw)
        return NormalizedBatch(
            generated_at=collection.updated,
            events=collection.to_events(ingested_at=ingested_at),
        )

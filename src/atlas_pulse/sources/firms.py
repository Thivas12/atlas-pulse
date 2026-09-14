"""NASA FIRMS VIIRS near-real-time thermal-anomaly CSV adapter."""

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, Self
from urllib.parse import quote

import httpx
from agent_rag_core import Event, GeoPoint
from pydantic import BaseModel, ConfigDict, Field, field_validator

from atlas_pulse.sources.base import NormalizedBatch
from atlas_pulse.sources.http import RetryingHttpClient

type FIRMSProduct = Literal[
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
    "VIIRS_SNPP_NRT",
]

_PUBLIC_MAP_URL = "https://firms.modaps.eosdis.nasa.gov/map/"
_REQUIRED_COLUMNS = {
    "latitude",
    "longitude",
    "bright_ti4",
    "scan",
    "track",
    "acq_date",
    "acq_time",
    "satellite",
    "instrument",
    "confidence",
    "version",
    "bright_ti5",
    "frp",
    "daynight",
}
_CONFIDENCE_LABEL = {"l": "Low", "n": "Nominal", "h": "High"}
_CONFIDENCE_RANK = {"l": 1, "n": 2, "h": 3}


class FIRMSDetection(BaseModel):
    """Validated fields shared by NASA FIRMS VIIRS NRT products."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    bright_ti4: float = Field(gt=0)
    scan: float = Field(gt=0)
    track: float = Field(gt=0)
    acq_date: date
    acq_time: str
    satellite: str = Field(min_length=1)
    instrument: Literal["VIIRS"]
    confidence: Literal["l", "n", "h"]
    version: str = Field(min_length=1)
    bright_ti5: float | None
    frp: float = Field(ge=0)
    daynight: Literal["D", "N"]

    @field_validator("acq_time", mode="before")
    @classmethod
    def validate_acquisition_time(cls, value: object) -> str:
        normalized = str(value).strip().zfill(4)
        if len(normalized) != 4 or not normalized.isdigit():
            raise ValueError("acq_time must be an HHMM value")
        hour, minute = int(normalized[:2]), int(normalized[2:])
        if hour > 23 or minute > 59:
            raise ValueError("acq_time must contain a valid UTC time")
        return normalized

    @field_validator("bright_ti5", mode="before")
    @classmethod
    def empty_brightness_is_missing(cls, value: object) -> object:
        return None if value == "" else value

    @property
    def acquired_at(self) -> datetime:
        """Combine FIRMS' separate UTC date and HHMM columns."""
        acquired_time = time(int(self.acq_time[:2]), int(self.acq_time[2:]), tzinfo=UTC)
        return datetime.combine(self.acq_date, acquired_time)

    def to_event(
        self,
        *,
        product: FIRMSProduct,
        ingested_at: datetime,
        active_window_hours: int,
    ) -> Event:
        """Convert one satellite detection into a stable shared event."""
        acquired_at = self.acquired_at
        identity = "|".join(
            (
                product,
                self.satellite,
                self.instrument,
                acquired_at.isoformat(),
                f"{self.latitude:.5f}",
                f"{self.longitude:.5f}",
            )
        )
        event_id = f"viirs-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
        confidence = _CONFIDENCE_LABEL[self.confidence]
        expires_at = acquired_at + timedelta(hours=active_window_hours)
        return Event(
            event_id=event_id,
            event_type="fire.thermal_anomaly",
            source="firms",
            occurred_at=acquired_at,
            ingested_at=ingested_at,
            location=GeoPoint(latitude=self.latitude, longitude=self.longitude),
            payload={
                "title": f"{confidence}-confidence VIIRS thermal anomaly",
                "place": f"{self.latitude:.4f}, {self.longitude:.4f}",
                "satellite": self.satellite,
                "instrument": self.instrument,
                "product": product,
                "confidence": confidence,
                "confidence_code": self.confidence,
                "confidence_rank": _CONFIDENCE_RANK[self.confidence],
                "brightness_ti4_k": self.bright_ti4,
                "brightness_ti5_k": self.bright_ti5,
                "fire_radiative_power_mw": self.frp,
                "scan_km": self.scan,
                "track_km": self.track,
                "day_night": "day" if self.daynight == "D" else "night",
                "version": self.version,
                "observed_at": acquired_at.isoformat(),
                "updated_at": acquired_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "expiry_basis": f"AtlasPulse {active_window_hours}-hour operational window",
                "source_url": _PUBLIC_MAP_URL,
            },
        )


@dataclass(frozen=True, slots=True)
class FIRMSFeed:
    """A strictly validated FIRMS CSV response."""

    detections: tuple[FIRMSDetection, ...]

    @classmethod
    def from_bytes(cls, raw: bytes) -> Self:
        """Parse UTF-8 CSV while rejecting missing or malformed schema."""
        try:
            decoded = raw.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("FIRMS response must be UTF-8 CSV") from error
        reader = csv.DictReader(io.StringIO(decoded, newline=""))
        if reader.fieldnames is None:
            raise ValueError("FIRMS response is missing a CSV header")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("FIRMS response contains duplicate CSV columns")
        missing = sorted(_REQUIRED_COLUMNS.difference(reader.fieldnames))
        if missing:
            raise ValueError(f"FIRMS response is missing columns: {', '.join(missing)}")

        detections: list[FIRMSDetection] = []
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"FIRMS row {row_number} contains unexpected extra values")
            try:
                detections.append(FIRMSDetection.model_validate(row))
            except ValueError as error:
                raise ValueError(f"FIRMS row {row_number} is invalid: {error}") from error
        return cls(detections=tuple(detections))

    def to_events(
        self,
        *,
        product: FIRMSProduct,
        ingested_at: datetime,
        active_window_hours: int,
    ) -> tuple[Event, ...]:
        return tuple(
            detection.to_event(
                product=product,
                ingested_at=ingested_at,
                active_window_hours=active_window_hours,
            )
            for detection in self.detections
        )


class FIRMSClient(RetryingHttpClient):
    """Credential-safe client for NASA FIRMS VIIRS near-real-time detections."""

    source_name: Literal["firms"] = "firms"
    snapshot_extension = "csv"

    def __init__(
        self,
        *,
        api_base_url: str,
        map_key: str,
        product: FIRMSProduct,
        area: str,
        day_range: int,
        active_window_hours: int,
        timeout_seconds: float,
        max_attempts: int,
        user_agent: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not map_key or any(character in map_key for character in "/?#"):
            raise ValueError("FIRMS MAP_KEY must be a non-empty path-safe value")
        if not 1 <= day_range <= 5:
            raise ValueError("FIRMS day_range must be between 1 and 5")
        self._product = product
        self._active_window_hours = active_window_hours
        request_url = "/".join(
            (
                api_base_url.rstrip("/"),
                quote(map_key, safe=""),
                product,
                quote(area, safe=",.-"),
                str(day_range),
            )
        )
        super().__init__(
            source_name=self.source_name,
            url=request_url,
            public_source_url=_PUBLIC_MAP_URL,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            user_agent=user_agent,
            accept="text/csv",
            client=client,
        )

    def normalize(self, raw: bytes, *, ingested_at: datetime) -> NormalizedBatch:
        """Validate a complete CSV response and normalize each detection."""
        feed = FIRMSFeed.from_bytes(raw)
        events = feed.to_events(
            product=self._product,
            ingested_at=ingested_at,
            active_window_hours=self._active_window_hours,
        )
        generated_at = max((event.occurred_at for event in events), default=ingested_at)
        return NormalizedBatch(
            generated_at=generated_at,
            events=events,
            timestamp_basis="latest_record" if events else "fetch_fallback",
        )

"""GDELT 2.0 near-real-time material-conflict Event adapter."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Self
from urllib.parse import urlsplit, urlunsplit

import httpx
from agent_rag_core import Event, GeoPoint
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atlas_pulse.sources.base import FetchedDocument, NormalizedBatch
from atlas_pulse.sources.http import (
    PermanentSourceError,
    RetryableSourceError,
    RetryingHttpClient,
)

_GDELT_HOST = "data.gdeltproject.org"
_GDELT_DATA_URL = "https://www.gdeltproject.org/data.html"
_EXPORT_PATH = re.compile(r"^/gdeltv2/(?P<timestamp>\d{14})\.export\.CSV\.zip$")
_EXPORT_MEMBER = re.compile(r"^(?P<timestamp>\d{14})\.export\.CSV$")
_MD5 = re.compile(r"^[0-9a-fA-F]{32}$")
_EVENT_CODE = re.compile(r"^\d{2,4}$")
_ROOT_CODES = frozenset(f"{value:02d}" for value in range(1, 21))
_COLUMN_COUNT = 61

_CAMEO_ROOT_LABELS = {
    "01": "Public statement",
    "02": "Appeal",
    "03": "Intent to cooperate",
    "04": "Consultation",
    "05": "Diplomatic cooperation",
    "06": "Material cooperation",
    "07": "Aid provision",
    "08": "Yield",
    "09": "Investigation",
    "10": "Demand",
    "11": "Disapproval",
    "12": "Rejection",
    "13": "Threat",
    "14": "Protest",
    "15": "Force posture",
    "16": "Reduced relations",
    "17": "Coercion",
    "18": "Assault",
    "19": "Fight",
    "20": "Mass violence",
}
_GEO_PRECISION = {
    1: "country centroid",
    2: "US state centroid",
    3: "US city or landmark centroid",
    4: "world city or landmark centroid",
    5: "world administrative-area centroid",
}
_PRIORITY_LABEL = {2: "Elevated", 3: "High", 4: "Critical"}


def _parse_gdelt_timestamp(value: str, *, field: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError(f"{field} must be a valid YYYYMMDDHHMMSS UTC timestamp") from error


def _safe_article_url(value: str) -> str | None:
    """Return only absolute HTTP(S) evidence URLs without embedded credentials."""
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
    ):
        return None
    return candidate


@dataclass(frozen=True, slots=True)
class GDELTExportPointer:
    """One integrity-bound Event export advertised by ``lastupdate.txt``."""

    expected_size: int
    expected_md5: str
    url: str
    exported_at: datetime

    @classmethod
    def from_bytes(cls, raw: bytes) -> Self:
        try:
            text = raw.decode("ascii", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("GDELT last-update manifest must be ASCII") from error

        candidates: list[tuple[str, str, str, re.Match[str]]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) != 3:
                raise ValueError(f"GDELT manifest line {line_number} must contain three fields")
            size, checksum, raw_url = parts
            parsed = urlsplit(raw_url)
            match = _EXPORT_PATH.fullmatch(parsed.path)
            if match:
                candidates.append((size, checksum, raw_url, match))

        if len(candidates) != 1:
            raise ValueError("GDELT manifest must advertise exactly one Event export")
        size, checksum, raw_url, match = candidates[0]
        try:
            expected_size = int(size)
        except ValueError as error:
            raise ValueError("GDELT Event export size must be an integer") from error
        if expected_size < 1:
            raise ValueError("GDELT Event export size must be positive")
        if not _MD5.fullmatch(checksum):
            raise ValueError("GDELT Event export checksum must be a 32-character MD5")

        parsed = urlsplit(raw_url)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("GDELT Event export URL contains an invalid port") from error
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname != _GDELT_HOST
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 80, 443}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("GDELT Event export URL must use the official data host")
        timestamp = match.group("timestamp")
        return cls(
            expected_size=expected_size,
            expected_md5=checksum.lower(),
            url=urlunsplit(("https", _GDELT_HOST, parsed.path, "", "")),
            exported_at=_parse_gdelt_timestamp(timestamp, field="export filename"),
        )


class GDELTEventRecord(BaseModel):
    """Validated fields selected from the 61-column GDELT Event table."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    global_event_id: int = Field(gt=0)
    event_date: date
    actor1_code: str | None
    actor1_name: str | None
    actor1_country_code: str | None
    actor2_code: str | None
    actor2_name: str | None
    actor2_country_code: str | None
    is_root_event: int = Field(ge=0, le=1)
    event_code: str
    event_base_code: str
    event_root_code: str
    quad_class: int = Field(ge=1, le=4)
    goldstein_scale: float = Field(ge=-10, le=10)
    num_mentions: int = Field(ge=0)
    num_sources: int = Field(ge=0)
    num_articles: int = Field(ge=0)
    average_tone: float = Field(ge=-100, le=100)
    action_geo_type: int = Field(ge=0, le=5)
    action_geo_name: str | None
    action_geo_country_code: str | None
    action_geo_adm1_code: str | None
    action_geo_adm2_code: str | None
    action_geo_latitude: float | None
    action_geo_longitude: float | None
    action_geo_feature_id: str | None
    detected_at: datetime
    source_report_url: str

    @field_validator(
        "actor1_code",
        "actor1_name",
        "actor1_country_code",
        "actor2_code",
        "actor2_name",
        "actor2_country_code",
        "action_geo_name",
        "action_geo_country_code",
        "action_geo_adm1_code",
        "action_geo_adm2_code",
        "action_geo_feature_id",
        mode="before",
    )
    @classmethod
    def empty_text_is_missing(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("action_geo_latitude", "action_geo_longitude", mode="before")
    @classmethod
    def empty_coordinate_is_missing(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("action_geo_type", mode="before")
    @classmethod
    def empty_geo_type_is_zero(cls, value: object) -> object:
        return 0 if value == "" else value

    @field_validator("event_date", mode="before")
    @classmethod
    def parse_event_date(cls, value: object) -> date:
        try:
            return datetime.strptime(str(value), "%Y%m%d").date()
        except ValueError as error:
            raise ValueError("event_date must be a valid YYYYMMDD date") from error

    @field_validator("detected_at", mode="before")
    @classmethod
    def parse_detected_at(cls, value: object) -> datetime:
        return _parse_gdelt_timestamp(str(value), field="DATEADDED")

    @field_validator("event_code", "event_base_code")
    @classmethod
    def validate_event_code(cls, value: str) -> str:
        if not _EVENT_CODE.fullmatch(value):
            raise ValueError("CAMEO event codes must contain two to four digits")
        return value

    @field_validator("event_root_code")
    @classmethod
    def validate_root_code(cls, value: str) -> str:
        if value not in _ROOT_CODES:
            raise ValueError("CAMEO root code must be between 01 and 20")
        return value

    @model_validator(mode="after")
    def validate_action_geography(self) -> Self:
        latitude = self.action_geo_latitude
        longitude = self.action_geo_longitude
        if (latitude is None) != (longitude is None):
            raise ValueError("GDELT action geography must contain both latitude and longitude")
        if self.action_geo_type == 0 and latitude is not None:
            raise ValueError("GDELT action geography type is required when coordinates exist")
        if self.action_geo_type > 0 and latitude is None:
            raise ValueError("GDELT action geography coordinates are required for a typed location")
        if latitude is not None and not -90 <= latitude <= 90:
            raise ValueError("GDELT action latitude is outside WGS84 bounds")
        if longitude is not None and not -180 <= longitude <= 180:
            raise ValueError("GDELT action longitude is outside WGS84 bounds")
        return self

    @classmethod
    def from_row(cls, row: list[str], *, row_number: int) -> Self:
        if len(row) != _COLUMN_COUNT:
            raise ValueError(
                f"GDELT row {row_number} has {len(row)} columns; expected {_COLUMN_COUNT}"
            )
        try:
            return cls.model_validate(
                {
                    "global_event_id": row[0],
                    "event_date": row[1],
                    "actor1_code": row[5],
                    "actor1_name": row[6],
                    "actor1_country_code": row[7],
                    "actor2_code": row[15],
                    "actor2_name": row[16],
                    "actor2_country_code": row[17],
                    "is_root_event": row[25],
                    "event_code": row[26],
                    "event_base_code": row[27],
                    "event_root_code": row[28],
                    "quad_class": row[29],
                    "goldstein_scale": row[30],
                    "num_mentions": row[31],
                    "num_sources": row[32],
                    "num_articles": row[33],
                    "average_tone": row[34],
                    "action_geo_type": row[51],
                    "action_geo_name": row[52],
                    "action_geo_country_code": row[53],
                    "action_geo_adm1_code": row[54],
                    "action_geo_adm2_code": row[55],
                    "action_geo_latitude": row[56],
                    "action_geo_longitude": row[57],
                    "action_geo_feature_id": row[58],
                    "detected_at": row[59],
                    "source_report_url": row[60],
                }
            )
        except ValueError as error:
            raise ValueError(f"GDELT row {row_number} is invalid: {error}") from error

    def to_event(self, *, ingested_at: datetime, active_window_hours: int) -> Event:
        if self.action_geo_latitude is None or self.action_geo_longitude is None:
            raise ValueError("cannot map a GDELT record without action coordinates")
        priority_rank = (
            4 if self.event_root_code == "20" else 3 if self.event_root_code in {"18", "19"} else 2
        )
        category = _CAMEO_ROOT_LABELS[self.event_root_code]
        actors = " → ".join(
            value for value in (self.actor1_name, self.actor2_name) if value is not None
        )
        title = f"{category}: {actors}" if actors else category
        source_report_url = _safe_article_url(self.source_report_url)
        expires_at = self.detected_at + timedelta(hours=active_window_hours)
        return Event(
            event_id=str(self.global_event_id),
            event_type="geopolitical.gdelt_event",
            source="gdelt",
            occurred_at=self.detected_at,
            ingested_at=ingested_at,
            location=GeoPoint(
                latitude=self.action_geo_latitude,
                longitude=self.action_geo_longitude,
            ),
            payload={
                "title": title,
                "place": self.action_geo_name or "Unknown action location",
                "category": category,
                "priority": _PRIORITY_LABEL[priority_rank],
                "severity_rank": priority_rank,
                "cameo_event_code": self.event_code,
                "cameo_base_code": self.event_base_code,
                "cameo_root_code": self.event_root_code,
                "quad_class": self.quad_class,
                "quad_class_label": "Material conflict",
                "goldstein_scale": self.goldstein_scale,
                "is_root_event": bool(self.is_root_event),
                "actor1": self.actor1_name,
                "actor1_code": self.actor1_code,
                "actor1_country_code": self.actor1_country_code,
                "actor2": self.actor2_name,
                "actor2_code": self.actor2_code,
                "actor2_country_code": self.actor2_country_code,
                "mentions": self.num_mentions,
                "sources": self.num_sources,
                "articles": self.num_articles,
                "average_tone": self.average_tone,
                "reported_event_date": self.event_date.isoformat(),
                "detected_at": self.detected_at.isoformat(),
                "updated_at": self.detected_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "expiry_basis": f"AtlasPulse {active_window_hours}-hour operational window",
                "geo_precision": _GEO_PRECISION[self.action_geo_type],
                "geo_type": self.action_geo_type,
                "geo_country_code": self.action_geo_country_code,
                "geo_adm1_code": self.action_geo_adm1_code,
                "geo_adm2_code": self.action_geo_adm2_code,
                "geo_feature_id": self.action_geo_feature_id,
                "source_url": source_report_url or _GDELT_DATA_URL,
                "source_report_url": source_report_url,
                "dataset_url": _GDELT_DATA_URL,
                "verification_status": "machine-coded media observation; not independently verified",
            },
        )


@dataclass(frozen=True, slots=True)
class GDELTFeed:
    """A safely expanded, fully validated 15-minute GDELT Event export."""

    exported_at: datetime
    records: tuple[GDELTEventRecord, ...]

    @classmethod
    def from_zip(
        cls,
        raw: bytes,
        *,
        max_uncompressed_bytes: int,
        max_rows: int,
    ) -> Self:
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = archive.infolist()
                if len(members) != 1:
                    raise ValueError("GDELT export ZIP must contain exactly one member")
                member = members[0]
                match = _EXPORT_MEMBER.fullmatch(member.filename)
                if member.is_dir() or match is None:
                    raise ValueError("GDELT export ZIP member has an unexpected name")
                if member.flag_bits & 0x1:
                    raise ValueError("GDELT export ZIP member must not be encrypted")
                if member.file_size > max_uncompressed_bytes:
                    raise ValueError("GDELT export exceeds the uncompressed byte limit")
                with archive.open(member) as stream:
                    content = stream.read(max_uncompressed_bytes + 1)
        except zipfile.BadZipFile as error:
            raise ValueError("GDELT export must be a valid ZIP archive") from error
        if len(content) > max_uncompressed_bytes:
            raise ValueError("GDELT export exceeds the uncompressed byte limit")
        if len(content) != member.file_size:
            raise ValueError("GDELT export ZIP member size is inconsistent")
        try:
            decoded = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("GDELT Event export must be UTF-8") from error

        records: list[GDELTEventRecord] = []
        try:
            reader = csv.reader(io.StringIO(decoded, newline=""), delimiter="\t", strict=True)
            for row_number, row in enumerate(reader, start=1):
                if row_number > max_rows:
                    raise ValueError("GDELT export exceeds the configured row limit")
                records.append(GDELTEventRecord.from_row(row, row_number=row_number))
        except csv.Error as error:
            raise ValueError(f"GDELT Event export contains malformed TSV: {error}") from error
        assert match is not None
        exported_at = _parse_gdelt_timestamp(match.group("timestamp"), field="ZIP member name")
        return cls(exported_at=exported_at, records=tuple(records))

    def to_events(
        self,
        *,
        ingested_at: datetime,
        active_window_hours: int,
        only_root_events: bool,
        minimum_geo_precision: int,
        minimum_mentions: int,
        max_events: int,
    ) -> tuple[Event, ...]:
        selected = [
            record
            for record in self.records
            if record.quad_class == 4
            and (not only_root_events or record.is_root_event == 1)
            and record.action_geo_type >= minimum_geo_precision
            and record.num_mentions >= minimum_mentions
        ]
        if len(selected) > max_events:
            raise ValueError(
                f"GDELT export produced {len(selected)} selected events; limit is {max_events}"
            )
        return tuple(
            record.to_event(
                ingested_at=ingested_at,
                active_window_hours=active_window_hours,
            )
            for record in selected
        )


class GDELTClient:
    """Resolve, integrity-check, and normalize the latest GDELT Event export."""

    source_name: Literal["gdelt"] = "gdelt"
    snapshot_extension = "zip"

    def __init__(
        self,
        *,
        last_update_url: str,
        poll_timeout_seconds: float,
        max_attempts: int,
        user_agent: str,
        max_compressed_bytes: int,
        max_uncompressed_bytes: int,
        max_rows: int,
        max_events: int,
        active_window_hours: int,
        only_root_events: bool,
        minimum_geo_precision: int,
        minimum_mentions: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(poll_timeout_seconds),
            headers={"User-Agent": user_agent},
        )
        self._manifest = RetryingHttpClient(
            source_name=self.source_name,
            url=last_update_url,
            timeout_seconds=poll_timeout_seconds,
            max_attempts=max_attempts,
            user_agent=user_agent,
            accept="text/plain",
            max_response_bytes=4096,
            client=self._client,
        )
        self._timeout_seconds = poll_timeout_seconds
        self._max_attempts = max_attempts
        self._user_agent = user_agent
        self._max_compressed_bytes = max_compressed_bytes
        self._max_uncompressed_bytes = max_uncompressed_bytes
        self._max_rows = max_rows
        self._max_events = max_events
        self._active_window_hours = active_window_hours
        self._only_root_events = only_root_events
        self._minimum_geo_precision = minimum_geo_precision
        self._minimum_mentions = minimum_mentions

    async def fetch(self) -> FetchedDocument:
        manifest = await self._manifest.fetch()
        try:
            pointer = GDELTExportPointer.from_bytes(manifest.raw)
        except ValueError:
            raise PermanentSourceError(
                "gdelt last-update manifest is invalid",
                attempt_count=manifest.transport_attempts,
            ) from None
        if pointer.expected_size > self._max_compressed_bytes:
            raise PermanentSourceError(
                "gdelt export exceeds the configured compressed byte limit",
                attempt_count=manifest.transport_attempts,
            )
        export_client = RetryingHttpClient(
            source_name=self.source_name,
            url=pointer.url,
            timeout_seconds=self._timeout_seconds,
            max_attempts=self._max_attempts,
            user_agent=self._user_agent,
            accept="application/zip, application/octet-stream",
            max_response_bytes=self._max_compressed_bytes,
            client=self._client,
        )
        try:
            document = await export_client.fetch()
        except RetryableSourceError as error:
            raise RetryableSourceError(
                str(error),
                attempt_count=manifest.transport_attempts + error.attempt_count,
            ) from None
        except PermanentSourceError as error:
            raise PermanentSourceError(
                str(error),
                attempt_count=manifest.transport_attempts + error.attempt_count,
            ) from None
        transport_attempts = manifest.transport_attempts + document.transport_attempts
        if len(document.raw) != pointer.expected_size:
            raise PermanentSourceError(
                "gdelt export size does not match the advertised manifest",
                attempt_count=transport_attempts,
            )
        checksum = hashlib.md5(document.raw, usedforsecurity=False).hexdigest()
        if checksum != pointer.expected_md5:
            raise PermanentSourceError(
                "gdelt export checksum does not match the advertised manifest",
                attempt_count=transport_attempts,
            )
        return FetchedDocument(
            raw=document.raw,
            fetched_at=document.fetched_at,
            source_url=document.source_url,
            content_type=document.content_type,
            transport_attempts=transport_attempts,
        )

    def normalize(self, raw: bytes, *, ingested_at: datetime) -> NormalizedBatch:
        feed = GDELTFeed.from_zip(
            raw,
            max_uncompressed_bytes=self._max_uncompressed_bytes,
            max_rows=self._max_rows,
        )
        return NormalizedBatch(
            generated_at=feed.exported_at,
            events=feed.to_events(
                ingested_at=ingested_at,
                active_window_hours=self._active_window_hours,
                only_root_events=self._only_root_events,
                minimum_geo_precision=self._minimum_geo_precision,
                minimum_mentions=self._minimum_mentions,
                max_events=self._max_events,
            ),
            timestamp_basis="source_metadata",
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

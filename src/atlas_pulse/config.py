"""Typed application configuration loaded from environment variables."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from atlas_pulse.source_polling import SourcePollPolicy


class Settings(BaseSettings):
    """Runtime settings with safe local-development defaults."""

    model_config = SettingsConfigDict(
        env_prefix="ATLAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    build_commit_sha: str = Field(default="unknown", pattern=r"^(unknown|[0-9a-f]{40})$")
    log_level: str = "INFO"
    usgs_feed_url: HttpUrl = HttpUrl(
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson"
    )
    usgs_poll_seconds: float = Field(default=60.0, gt=0)
    usgs_source_stale_seconds: float = Field(default=600.0, gt=0)
    nws_alerts_url: HttpUrl = HttpUrl("https://api.weather.gov/alerts/active?status=actual")
    nws_poll_seconds: float = Field(default=120.0, gt=0)
    nws_source_stale_seconds: float = Field(default=900.0, gt=0)
    firms_enabled: bool = False
    firms_api_base_url: HttpUrl = HttpUrl("https://firms.modaps.eosdis.nasa.gov/api/area/csv")
    firms_map_key: SecretStr | None = None
    firms_product: Literal[
        "VIIRS_NOAA20_NRT",
        "VIIRS_NOAA21_NRT",
        "VIIRS_SNPP_NRT",
    ] = "VIIRS_NOAA20_NRT"
    firms_area: str = "world"
    firms_day_range: int = Field(default=1, ge=1, le=5)
    firms_poll_seconds: float = Field(default=900.0, ge=300)
    firms_source_stale_seconds: float = Field(default=129_600.0, gt=0)
    firms_active_window_hours: int = Field(default=24, ge=1, le=120)
    gdelt_enabled: bool = True
    gdelt_last_update_url: HttpUrl = HttpUrl("https://data.gdeltproject.org/gdeltv2/lastupdate.txt")
    gdelt_poll_seconds: float = Field(default=900.0, ge=300)
    gdelt_source_stale_seconds: float = Field(default=3_600.0, gt=0)
    gdelt_active_window_hours: int = Field(default=24, ge=1, le=168)
    gdelt_only_root_events: bool = True
    gdelt_minimum_geo_precision: int = Field(default=3, ge=1, le=5)
    gdelt_minimum_mentions: int = Field(default=1, ge=1, le=1_000_000)
    gdelt_max_compressed_bytes: int = Field(default=25_000_000, ge=1_000, le=100_000_000)
    gdelt_max_uncompressed_bytes: int = Field(default=100_000_000, ge=1_000, le=500_000_000)
    gdelt_max_rows: int = Field(default=100_000, ge=1, le=1_000_000)
    gdelt_max_events: int = Field(default=5_000, ge=1, le=100_000)
    source_user_agent: str = Field(
        default="AtlasPulse/0.12 (+https://github.com/Thivas12/atlas-pulse)",
        min_length=10,
    )
    source_timeout_seconds: float = Field(default=15.0, gt=0)
    source_max_attempts: int = Field(default=3, ge=1, le=10)
    source_poll_stale_multiplier: float = Field(default=3.0, gt=1, le=10)
    source_poll_history_stream: str = Field(default="{atlas}:source-polls", min_length=1)
    source_poll_history_max_length: int = Field(default=50_000, ge=100)
    raw_data_dir: Path = Path("data/raw")
    valkey_url: str = "valkey://localhost:6379/0"
    api_rate_limit_client_secret: SecretStr = SecretStr("local-development-only-rate-limit-secret")
    api_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3_600)
    api_rate_limit_requests: int = Field(default=120, ge=1, le=100_000)
    api_expensive_rate_limit_requests: int = Field(default=20, ge=1, le=100_000)
    event_stream: str = "{atlas}:events"
    stream_max_length: int = Field(default=100_000, ge=100)
    dedupe_ttl_seconds: int = Field(default=604_800, ge=60)
    database_url: str = "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas"
    projection_name: str = Field(default="current-signals-v1", min_length=1)
    projection_batch_size: int = Field(default=500, ge=1, le=5_000)
    projection_poll_seconds: float = Field(default=1.0, gt=0)
    retrieval_projection_name: str = Field(default="hybrid-retrieval-v1", min_length=1)
    retrieval_batch_size: int = Field(default=64, ge=1, le=512)
    retrieval_poll_seconds: float = Field(default=1.0, gt=0)
    agent_approval_trusted_key_ids: tuple[str, ...] = ()
    agent_release_assessment_path: Path | None = None
    embedding_model: str = Field(default="BAAI/bge-small-en-v1.5", min_length=1)
    embedding_dimensions: int = Field(default=384, ge=1, le=4_096)
    embedding_cache_dir: Path = Path(".cache/fastembed")
    embedding_model_path: Path | None = None
    embedding_local_files_only: bool = False
    embedding_threads: int = Field(default=2, ge=1, le=32)
    otel_service_name: str = "atlas-pulse"
    otel_exporter_otlp_endpoint: str | None = None

    @field_validator("firms_area")
    @classmethod
    def validate_firms_area(cls, value: str) -> str:
        """Accept NASA's global token or a valid non-wrapping WGS84 bbox."""
        normalized = value.strip()
        if normalized == "world":
            return normalized
        try:
            west, south, east, north = (float(part.strip()) for part in normalized.split(","))
        except (TypeError, ValueError) as error:
            raise ValueError("firms_area must be 'world' or west,south,east,north") from error
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("firms_area must be a valid non-wrapping WGS84 bbox")
        return ",".join(format(part, "g") for part in (west, south, east, north))

    @field_validator("agent_approval_trusted_key_ids")
    @classmethod
    def validate_agent_approval_trusted_key_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require unique content-derived Ed25519 key identities."""
        if len(value) != len(set(value)):
            raise ValueError("agent approval trusted key IDs must be unique")
        for key_id in value:
            if len(key_id) != 72 or not key_id.startswith("ed25519-"):
                raise ValueError("agent approval key IDs must use ed25519-<sha256>")
            if any(character not in "0123456789abcdef" for character in key_id[8:]):
                raise ValueError("agent approval key IDs must use ed25519-<sha256>")
        return value

    @field_validator("api_rate_limit_client_secret")
    @classmethod
    def validate_api_rate_limit_client_secret(cls, value: SecretStr) -> SecretStr:
        """Keep the per-deployment HMAC key bounded and safe for environment files."""
        secret = value.get_secret_value()
        if re.fullmatch(r"[A-Za-z0-9_-]{32,128}", secret) is None:
            raise ValueError(
                "API rate-limit client secret must be 32-128 URL-safe ASCII characters"
            )
        return value

    @model_validator(mode="after")
    def validate_cross_field_constraints(self) -> "Settings":
        """Validate constraints shared by every Atlas Pulse process."""
        if self.gdelt_max_uncompressed_bytes < self.gdelt_max_compressed_bytes:
            raise ValueError(
                "ATLAS_GDELT_MAX_UNCOMPRESSED_BYTES must be at least the compressed byte limit"
            )
        if self.gdelt_max_events > self.gdelt_max_rows:
            raise ValueError("ATLAS_GDELT_MAX_EVENTS must not exceed ATLAS_GDELT_MAX_ROWS")
        if self.api_expensive_rate_limit_requests > self.api_rate_limit_requests:
            raise ValueError(
                "ATLAS_API_EXPENSIVE_RATE_LIMIT_REQUESTS must not exceed "
                "ATLAS_API_RATE_LIMIT_REQUESTS"
            )
        self.source_poll_policies()
        return self

    def require_firms_map_key(self) -> str:
        """Return the FIRMS credential at its ingestor-only use site or fail closed."""
        key = self.firms_map_key.get_secret_value().strip() if self.firms_map_key else ""
        if not key:
            raise ValueError(
                "ATLAS_FIRMS_MAP_KEY is required by the ingestion worker when "
                "ATLAS_FIRMS_ENABLED=true"
            )
        return key

    def source_poll_policies(self) -> tuple[SourcePollPolicy, ...]:
        """Return canonical freshness policies for exactly the enabled pollers."""
        policies = [
            SourcePollPolicy(
                source="usgs",
                interval_seconds=self.usgs_poll_seconds,
                poll_stale_after_seconds=(
                    self.usgs_poll_seconds * self.source_poll_stale_multiplier
                ),
                source_stale_after_seconds=self.usgs_source_stale_seconds,
            ),
            SourcePollPolicy(
                source="nws",
                interval_seconds=self.nws_poll_seconds,
                poll_stale_after_seconds=(
                    self.nws_poll_seconds * self.source_poll_stale_multiplier
                ),
                source_stale_after_seconds=self.nws_source_stale_seconds,
            ),
        ]
        if self.firms_enabled:
            policies.append(
                SourcePollPolicy(
                    source="firms",
                    interval_seconds=self.firms_poll_seconds,
                    poll_stale_after_seconds=(
                        self.firms_poll_seconds * self.source_poll_stale_multiplier
                    ),
                    source_stale_after_seconds=self.firms_source_stale_seconds,
                )
            )
        if self.gdelt_enabled:
            policies.append(
                SourcePollPolicy(
                    source="gdelt",
                    interval_seconds=self.gdelt_poll_seconds,
                    poll_stale_after_seconds=(
                        self.gdelt_poll_seconds * self.source_poll_stale_multiplier
                    ),
                    source_stale_after_seconds=self.gdelt_source_stale_seconds,
                )
            )
        return tuple(sorted(policies, key=lambda policy: policy.source))


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings instance per process."""
    return Settings()

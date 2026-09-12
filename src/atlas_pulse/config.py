"""Typed application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings with safe local-development defaults."""

    model_config = SettingsConfigDict(
        env_prefix="ATLAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    log_level: str = "INFO"
    usgs_feed_url: HttpUrl = HttpUrl(
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson"
    )
    usgs_poll_seconds: float = Field(default=60.0, gt=0)
    nws_alerts_url: HttpUrl = HttpUrl("https://api.weather.gov/alerts/active?status=actual")
    nws_poll_seconds: float = Field(default=120.0, gt=0)
    source_user_agent: str = Field(
        default="AtlasPulse/0.3 (+https://github.com/Thivas12/atlas-pulse)",
        min_length=10,
    )
    source_timeout_seconds: float = Field(default=15.0, gt=0)
    source_max_attempts: int = Field(default=3, ge=1, le=10)
    raw_data_dir: Path = Path("data/raw")
    valkey_url: str = "valkey://localhost:6379/0"
    event_stream: str = "{atlas}:events"
    stream_max_length: int = Field(default=100_000, ge=100)
    dedupe_ttl_seconds: int = Field(default=604_800, ge=60)
    database_url: str = "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas"
    projection_name: str = Field(default="current-signals-v1", min_length=1)
    projection_batch_size: int = Field(default=500, ge=1, le=5_000)
    projection_poll_seconds: float = Field(default=1.0, gt=0)
    otel_service_name: str = "atlas-pulse"
    otel_exporter_otlp_endpoint: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings instance per process."""
    return Settings()

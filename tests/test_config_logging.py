"""Runtime configuration and logging bootstrap tests."""

import logging

import pytest
from pydantic import ValidationError

from atlas_pulse.config import Settings, get_settings
from atlas_pulse.logging import configure_logging
from atlas_pulse.telemetry import redact_source_url


def test_settings_load_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_USGS_POLL_SECONDS", "12.5")
    monkeypatch.setenv("ATLAS_NWS_POLL_SECONDS", "90")
    monkeypatch.setenv("ATLAS_STREAM_MAX_LENGTH", "250")
    monkeypatch.setenv("ATLAS_FIRMS_ENABLED", "true")
    monkeypatch.setenv("ATLAS_FIRMS_MAP_KEY", "top-secret")
    monkeypatch.setenv("ATLAS_FIRMS_AREA", "-125,24,-66,50")

    settings = Settings()

    assert settings.usgs_poll_seconds == 12.5
    assert settings.nws_poll_seconds == 90
    assert settings.stream_max_length == 250
    assert settings.event_stream == "{atlas}:events"
    assert settings.firms_enabled is True
    assert settings.firms_area == "-125,24,-66,50"
    assert settings.firms_map_key is not None
    assert "top-secret" not in repr(settings)


def test_firms_requires_a_key_only_when_enabled() -> None:
    assert Settings(firms_enabled=False).firms_enabled is False
    with pytest.raises(ValidationError, match="ATLAS_FIRMS_MAP_KEY is required"):
        Settings(firms_enabled=True)


@pytest.mark.parametrize("area", ["north", "1,2,3", "10,0,-10,5", "0,-91,1,2"])
def test_settings_reject_invalid_firms_area(area: str) -> None:
    with pytest.raises(ValidationError, match="firms_area"):
        Settings(firms_area=area)


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()


def test_structured_logging_accepts_known_and_unknown_levels() -> None:
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    configure_logging("not-a-level")
    assert logging.getLogger().level == logging.INFO


def test_firms_map_key_is_redacted_from_telemetry_urls() -> None:
    raw = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/secret-key/VIIRS_NOAA20_NRT/world/1"
    redacted = redact_source_url(raw)
    assert "secret-key" not in redacted
    assert "/api/area/csv/[REDACTED]/VIIRS_NOAA20_NRT/world/1" in redacted
    assert redact_source_url("https://api.weather.gov/alerts/active") == (
        "https://api.weather.gov/alerts/active"
    )

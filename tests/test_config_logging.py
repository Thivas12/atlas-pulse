"""Runtime configuration and logging bootstrap tests."""

import logging

import pytest

from atlas_pulse.config import Settings, get_settings
from atlas_pulse.logging import configure_logging


def test_settings_load_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_USGS_POLL_SECONDS", "12.5")
    monkeypatch.setenv("ATLAS_NWS_POLL_SECONDS", "90")
    monkeypatch.setenv("ATLAS_STREAM_MAX_LENGTH", "250")

    settings = Settings()

    assert settings.usgs_poll_seconds == 12.5
    assert settings.nws_poll_seconds == 90
    assert settings.stream_max_length == 250
    assert settings.event_stream == "{atlas}:events"


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()


def test_structured_logging_accepts_known_and_unknown_levels() -> None:
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    configure_logging("not-a-level")
    assert logging.getLogger().level == logging.INFO

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
    key_id = f"ed25519-{'a' * 64}"
    monkeypatch.setenv("ATLAS_AGENT_APPROVAL_TRUSTED_KEY_IDS", f'["{key_id}"]')
    monkeypatch.setenv("ATLAS_BUILD_COMMIT_SHA", "a" * 40)
    monkeypatch.setenv("ATLAS_API_RATE_LIMIT_CLIENT_SECRET", "b" * 64)
    monkeypatch.setenv("ATLAS_API_EXPENSIVE_RATE_LIMIT_REQUESTS", "10")

    settings = Settings()

    assert settings.usgs_poll_seconds == 12.5
    assert settings.nws_poll_seconds == 90
    assert settings.stream_max_length == 250
    assert settings.event_stream == "{atlas}:events"
    assert settings.firms_enabled is True
    assert settings.firms_area == "-125,24,-66,50"
    assert settings.firms_map_key is not None
    assert settings.gdelt_enabled is True
    assert settings.gdelt_minimum_geo_precision == 3
    assert settings.agent_approval_trusted_key_ids == (key_id,)
    assert settings.build_commit_sha == "a" * 40
    assert settings.api_expensive_rate_limit_requests == 10
    assert tuple(policy.source for policy in settings.source_poll_policies()) == (
        "firms",
        "gdelt",
        "nws",
        "usgs",
    )
    assert settings.source_poll_history_stream == "{atlas}:source-polls"
    assert "top-secret" not in repr(settings)
    assert "b" * 64 not in repr(settings)


def test_firms_policy_does_not_expose_credential_to_non_ingestor_processes() -> None:
    settings = Settings(firms_enabled=True)

    assert settings.firms_map_key is None
    assert "firms" in {policy.source for policy in settings.source_poll_policies()}


def test_firms_ingestor_requires_a_key_when_enabled() -> None:
    settings = Settings(firms_enabled=True)

    with pytest.raises(ValueError, match="required by the ingestion worker"):
        settings.require_firms_map_key()

    configured = Settings(firms_enabled=True, firms_map_key="top-secret")
    assert configured.require_firms_map_key() == "top-secret"


def test_settings_reject_an_unpinned_build_commit() -> None:
    with pytest.raises(ValidationError, match="build_commit_sha"):
        Settings(build_commit_sha="main")


def test_settings_rejects_source_freshness_threshold_at_or_below_poll_interval() -> None:
    with pytest.raises(ValidationError, match="source stale threshold must exceed"):
        Settings(usgs_poll_seconds=600, usgs_source_stale_seconds=600)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"gdelt_max_compressed_bytes": 2_000, "gdelt_max_uncompressed_bytes": 1_000},
            "MAX_UNCOMPRESSED_BYTES",
        ),
        ({"gdelt_max_rows": 10, "gdelt_max_events": 11}, "MAX_EVENTS"),
    ],
)
def test_settings_reject_inconsistent_gdelt_resource_limits(
    overrides: dict[str, int], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings.model_validate(overrides)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"api_rate_limit_client_secret": "short"}, "client secret"),
        (
            {"api_rate_limit_requests": 5, "api_expensive_rate_limit_requests": 6},
            "EXPENSIVE_RATE_LIMIT_REQUESTS",
        ),
    ],
)
def test_settings_reject_invalid_api_rate_limit_policy(
    overrides: dict[str, str | int], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings.model_validate(overrides)


@pytest.mark.parametrize("area", ["north", "1,2,3", "10,0,-10,5", "0,-91,1,2"])
def test_settings_reject_invalid_firms_area(area: str) -> None:
    with pytest.raises(ValidationError, match="firms_area"):
        Settings(firms_area=area)


@pytest.mark.parametrize(
    "key_ids",
    [
        ("not-a-key",),
        (f"ed25519-{'g' * 64}",),
        (f"ed25519-{'a' * 64}", f"ed25519-{'a' * 64}"),
    ],
)
def test_settings_reject_invalid_or_duplicate_governance_keys(key_ids: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="approval"):
        Settings(agent_approval_trusted_key_ids=key_ids)


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

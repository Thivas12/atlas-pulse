"""Shared deterministic test data."""

from pathlib import Path

import pytest


@pytest.fixture
def usgs_payload() -> bytes:
    """Return a frozen public-source-shaped response."""
    return (Path(__file__).parent / "fixtures" / "usgs_all_hour.geojson").read_bytes()


@pytest.fixture
def nws_payload() -> bytes:
    """Return a frozen, synthetic NWS-source-shaped response."""
    return (Path(__file__).parent / "fixtures" / "nws_active_alerts.geojson").read_bytes()

"""Shared deterministic test data."""

from pathlib import Path

import pytest


@pytest.fixture
def usgs_payload() -> bytes:
    """Return a frozen public-source-shaped response."""
    return (Path(__file__).parent / "fixtures" / "usgs_all_hour.geojson").read_bytes()

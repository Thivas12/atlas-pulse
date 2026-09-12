"""NASA FIRMS thermal-anomaly adapter contract tests."""

from datetime import UTC, datetime

import httpx
import pytest

from atlas_pulse.sources.firms import FIRMSClient, FIRMSFeed
from atlas_pulse.streams.base import event_fingerprint


def make_client(*, client: httpx.AsyncClient | None = None) -> FIRMSClient:
    return FIRMSClient(
        api_base_url="https://firms.modaps.eosdis.nasa.gov/api/area/csv",
        map_key="secretABC123",
        product="VIIRS_NOAA20_NRT",
        area="world",
        day_range=1,
        active_window_hours=24,
        timeout_seconds=1,
        max_attempts=1,
        user_agent="AtlasPulse tests",
        client=client,
    )


def test_feed_normalizes_stable_expiring_thermal_anomalies(firms_payload: bytes) -> None:
    feed = FIRMSFeed.from_bytes(firms_payload)
    ingested_at = datetime(2024, 7, 10, 10, tzinfo=UTC)

    events = feed.to_events(
        product="VIIRS_NOAA20_NRT",
        ingested_at=ingested_at,
        active_window_hours=24,
    )

    first = events[0]
    assert first.event_id == "viirs-d033143b4f2b734d0474eb55"
    assert first.event_type == "fire.thermal_anomaly"
    assert first.source == "firms"
    assert first.deduplication_key == f"firms:{first.event_id}"
    assert first.occurred_at == datetime(2024, 7, 10, 9, 37, tzinfo=UTC)
    assert first.ingested_at == ingested_at
    assert first.location is not None
    assert first.location.latitude == 34.12345
    assert first.location.longitude == -118.54321
    assert first.payload["confidence"] == "High"
    assert first.payload["confidence_rank"] == 3
    assert first.payload["fire_radiative_power_mw"] == 18.45
    assert first.payload["day_night"] == "day"
    assert first.payload["expires_at"] == "2024-07-11T09:37:00+00:00"
    assert "secretABC123" not in str(first.payload)

    second = events[1]
    assert second.occurred_at == datetime(2024, 7, 10, 0, 15, tzinfo=UTC)
    assert second.payload["brightness_ti5_k"] is None
    assert second.payload["confidence"] == "Nominal"
    assert second.payload["day_night"] == "night"


def test_detection_identity_ignores_revised_measurements(firms_payload: bytes) -> None:
    original = FIRMSFeed.from_bytes(firms_payload)
    revised_payload = firms_payload.replace(b"18.45,D,0", b"21.75,D,0")
    revised = FIRMSFeed.from_bytes(revised_payload)
    ingested_at = datetime(2024, 7, 10, 10, tzinfo=UTC)

    first = original.to_events(
        product="VIIRS_NOAA20_NRT", ingested_at=ingested_at, active_window_hours=24
    )[0]
    second = revised.to_events(
        product="VIIRS_NOAA20_NRT", ingested_at=ingested_at, active_window_hours=24
    )[0]

    assert first.event_id == second.event_id
    assert first.payload["fire_radiative_power_mw"] == 18.45
    assert second.payload["fire_radiative_power_mw"] == 21.75
    assert event_fingerprint(first) != event_fingerprint(second)


@pytest.mark.asyncio
async def test_client_normalizes_latest_detection_time_and_empty_feed(
    firms_payload: bytes,
) -> None:
    ingested_at = datetime(2024, 7, 10, 10, tzinfo=UTC)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))
    ) as http_client:
        client = make_client(client=http_client)
        populated = client.normalize(firms_payload, ingested_at=ingested_at)
        header = firms_payload.splitlines(keepends=True)[0]
        empty = client.normalize(header, ingested_at=ingested_at)

    assert populated.generated_at == datetime(2024, 7, 10, 9, 37, tzinfo=UTC)
    assert len(populated.events) == 2
    assert empty.generated_at == ingested_at
    assert empty.events == ()


@pytest.mark.asyncio
async def test_client_keeps_map_key_out_of_document_metadata(firms_payload: bytes) -> None:
    request: httpx.Request | None = None

    def handler(value: httpx.Request) -> httpx.Response:
        nonlocal request
        request = value
        return httpx.Response(
            200,
            content=firms_payload,
            headers={"content-type": "text/csv"},
            request=value,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = make_client(client=http_client)
        fetched = await source.fetch()
        await source.close()

    assert request is not None
    assert "/secretABC123/VIIRS_NOAA20_NRT/world/1" in str(request.url)
    assert request.headers["accept"] == "text/csv"
    assert request.headers["user-agent"] == "AtlasPulse tests"
    assert fetched.source_url == "https://firms.modaps.eosdis.nasa.gov/map/"
    assert "secretABC123" not in fetched.source_url
    assert fetched.content_type == "text/csv"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "missing a CSV header"),
        (b"latitude,latitude\n1,2\n", "duplicate CSV columns"),
        (b"latitude,longitude\n1,2\n", "missing columns"),
        (b"\xff\xfe", "must be UTF-8"),
    ],
)
def test_feed_rejects_broken_csv_contract(payload: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        FIRMSFeed.from_bytes(payload)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (b"0937,N20", b"2460,N20", "valid UTC time"),
        (b"34.12345,-118.54321", b"94.12345,-118.54321", "less than or equal to 90"),
        (b",h,2.0NRT", b",x,2.0NRT", "Input should be"),
    ],
)
def test_feed_reports_invalid_row_number(
    firms_payload: bytes, old: bytes, new: bytes, message: str
) -> None:
    with pytest.raises(ValueError) as caught:
        FIRMSFeed.from_bytes(firms_payload.replace(old, new, 1))
    assert "row 2" in str(caught.value)
    assert message in str(caught.value)


def test_feed_rejects_unexpected_extra_csv_values(firms_payload: bytes) -> None:
    first_line, first_row, *remaining = firms_payload.splitlines()
    malformed = b"\n".join((first_line, first_row + b",extra", *remaining))
    with pytest.raises(ValueError, match="row 2 contains unexpected extra values"):
        FIRMSFeed.from_bytes(malformed)


@pytest.mark.parametrize(
    ("map_key", "day_range", "message"),
    [
        ("", 1, "non-empty path-safe"),
        ("bad/key", 1, "non-empty path-safe"),
        ("valid-key", 0, "between 1 and 5"),
        ("valid-key", 6, "between 1 and 5"),
    ],
)
def test_client_rejects_unsafe_configuration(map_key: str, day_range: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        FIRMSClient(
            api_base_url="https://example.test/api/area/csv",
            map_key=map_key,
            product="VIIRS_NOAA20_NRT",
            area="world",
            day_range=day_range,
            active_window_hours=24,
            timeout_seconds=1,
            max_attempts=1,
            user_agent="AtlasPulse tests",
        )

"""NWS active-alert adapter contract tests."""

import json
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
from pydantic import ValidationError

from atlas_pulse.sources.nws import NWSAlertCollection, NWSClient


def test_collection_normalizes_polygon_and_area_only_alerts(nws_payload: bytes) -> None:
    collection = NWSAlertCollection.from_bytes(nws_payload)
    ingested_at = datetime(2024, 7, 10, 12, 6, tzinfo=UTC)

    events = collection.to_events(ingested_at=ingested_at)

    polygon = events[0]
    assert polygon.event_id == "urn:oid:2.49.0.1.840.0.test001"
    assert polygon.event_type == "weather.alert"
    assert polygon.source == "nws"
    assert polygon.deduplication_key == "nws:urn:oid:2.49.0.1.840.0.test001"
    assert polygon.location is not None
    assert polygon.location.latitude == 35
    assert polygon.location.longitude == -97
    assert polygon.payload["severity"] == "Severe"
    assert polygon.payload["severity_rank"] == 3
    assert polygon.payload["message_type"] == "Update"
    polygon_geometry = cast(dict[str, object], polygon.payload["geometry"])
    assert polygon_geometry["type"] == "Polygon"
    assert polygon.payload["geocode"] == {"SAME": ["040001"], "UGC": ["OKC001"]}
    assert polygon.occurred_at == datetime(2024, 7, 10, 12, tzinfo=UTC)
    assert polygon.ingested_at == ingested_at

    area_only = events[1]
    assert area_only.location is None
    assert area_only.occurred_at == datetime(2024, 7, 10, 12, 10, tzinfo=UTC)
    assert area_only.payload["geometry"] is None
    assert area_only.payload["title"] == "Small Craft Advisory for Coastal Waters"
    assert area_only.payload["onset_at"] is None
    assert area_only.payload["ends_at"] is None


@pytest.mark.asyncio
async def test_client_normalizes_collection_metadata(nws_payload: bytes) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as http_client:
        client = NWSClient(
            alerts_url="https://api.weather.gov/alerts/active?status=actual",
            timeout_seconds=1,
            max_attempts=1,
            user_agent="AtlasPulse test@example.test",
            client=http_client,
        )
        batch = client.normalize(
            nws_payload,
            ingested_at=datetime(2024, 7, 10, 12, 6, tzinfo=UTC),
        )

    assert batch.generated_at == datetime(2024, 7, 10, 12, 5, tzinfo=UTC)
    assert len(batch.events) == 2


@pytest.mark.asyncio
async def test_client_sends_required_nws_headers(nws_payload: bytes) -> None:
    request_headers: httpx.Headers | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_headers
        request_headers = request.headers
        return httpx.Response(
            200,
            content=nws_payload,
            headers={"content-type": "application/geo+json"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = NWSClient(
            alerts_url="https://api.weather.gov/alerts/active?status=actual",
            timeout_seconds=1,
            max_attempts=1,
            user_agent="AtlasPulse test@example.test",
            client=http_client,
        )
        fetched = await source.fetch()

    assert fetched.raw == nws_payload
    assert request_headers is not None
    assert request_headers["user-agent"] == "AtlasPulse test@example.test"
    assert request_headers["accept"] == "application/geo+json"


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("features", 0, "properties", "severity"), "Catastrophic", "Input should be"),
        (("features", 0, "properties", "status"), "Expired", "Input should be"),
        (("features", 0, "properties", "messageType"), "Revision", "Input should be"),
    ],
)
def test_collection_rejects_unknown_cap_enums(
    nws_payload: bytes,
    path: tuple[str | int, ...],
    value: str,
    message: str,
) -> None:
    document = json.loads(nws_payload)
    target = document
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(ValidationError, match=message):
        NWSAlertCollection.model_validate(document)


@pytest.mark.parametrize(
    ("coordinates", "message"),
    [
        ([], "at least one linear ring"),
        ([[[-98.0, 34.0], [-96.0, 34.0], [-98.0, 34.0]]], "at least four"),
        (
            [[[-98.0, 34.0], [-96.0, 34.0], [-96.0, 36.0], [-98.0, 36.0]]],
            "must be closed",
        ),
        (
            [[[-198.0, 34.0], [-96.0, 34.0], [-96.0, 36.0], [-198.0, 34.0]]],
            "outside WGS84",
        ),
    ],
)
def test_collection_rejects_invalid_polygon(
    nws_payload: bytes, coordinates: list[object], message: str
) -> None:
    document = json.loads(nws_payload)
    document["features"][0]["geometry"]["coordinates"] = coordinates

    with pytest.raises(ValidationError, match=message):
        NWSAlertCollection.model_validate(document)


def test_collection_accepts_multipolygon(nws_payload: bytes) -> None:
    document = json.loads(nws_payload)
    polygon = document["features"][0]["geometry"]["coordinates"]
    document["features"][0]["geometry"] = {
        "type": "MultiPolygon",
        "coordinates": [polygon, [[[-90.0, 30.0], [-89.0, 30.0], [-89.0, 31.0], [-90.0, 30.0]]]],
    }

    event = NWSAlertCollection.model_validate(document).to_events(
        ingested_at=datetime(2024, 7, 10, 12, 6, tzinfo=UTC)
    )[0]

    assert event.location is not None
    assert event.location.longitude == -93.5
    assert event.location.latitude == 33
    event_geometry = cast(dict[str, object], event.payload["geometry"])
    assert event_geometry["type"] == "MultiPolygon"


def test_collection_rejects_empty_multipolygon(nws_payload: bytes) -> None:
    document = json.loads(nws_payload)
    document["features"][0]["geometry"] = {"type": "MultiPolygon", "coordinates": []}

    with pytest.raises(ValidationError, match="at least one polygon"):
        NWSAlertCollection.model_validate(document)

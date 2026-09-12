"""USGS adapter contract tests."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from atlas_pulse.sources import PermanentSourceError, RetryableSourceError
from atlas_pulse.sources.http import RetryingHttpClient
from atlas_pulse.sources.usgs import USGSClient, USGSFeed


def test_feed_normalizes_geojson_to_shared_events(usgs_payload: bytes) -> None:
    feed = USGSFeed.from_bytes(usgs_payload)
    ingested_at = datetime(2024, 7, 10, 12, tzinfo=UTC)

    events = feed.to_events(ingested_at=ingested_at)

    first = events[0]
    assert first.event_id == "test001"
    assert first.event_type == "seismic.earthquake"
    assert first.source == "usgs"
    assert first.deduplication_key == "usgs:test001"
    assert first.location is not None
    assert first.location.latitude == 37.75
    assert first.location.longitude == -122.25
    assert first.location.altitude_km == -8.4
    assert first.payload["depth_km"] == 8.4
    assert first.payload["tsunami"] is False
    assert first.ingested_at == ingested_at

    second = events[1]
    assert second.payload["magnitude"] is None
    assert second.payload["tsunami"] is True
    assert second.location is not None
    assert second.location.altitude_km == 1.5


def test_feed_rejects_count_mismatch(usgs_payload: bytes) -> None:
    document = json.loads(usgs_payload)
    document["metadata"]["count"] = 99

    with pytest.raises(ValidationError, match="does not match"):
        USGSFeed.model_validate(document)


def test_http_transport_rejects_a_nonpositive_response_limit() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        RetryingHttpClient(
            source_name="test",
            url="https://example.test",
            timeout_seconds=1,
            max_attempts=1,
            user_agent="AtlasPulse tests",
            accept="application/json",
            max_response_bytes=0,
        )


@pytest.mark.asyncio
async def test_client_normalizes_feed_metadata(usgs_payload: bytes) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as http_client:
        client = USGSClient(
            feed_url="https://example.test/all_hour.geojson",
            timeout_seconds=1,
            max_attempts=1,
            client=http_client,
        )
        batch = client.normalize(
            usgs_payload,
            ingested_at=datetime(2024, 7, 10, 12, tzinfo=UTC),
        )

    assert batch.generated_at == datetime(2024, 7, 10, 12, 5, tzinfo=UTC)
    assert len(batch.events) == 2


@pytest.mark.asyncio
async def test_client_retries_transient_server_failure(usgs_payload: bytes) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            content=usgs_payload,
            headers={"content-type": "application/geo+json"},
            request=request,
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source = USGSClient(
        feed_url="https://example.test/all_hour.geojson",
        timeout_seconds=1,
        max_attempts=2,
        client=http_client,
    )

    fetched = await source.fetch()

    assert fetched.raw == usgs_payload
    assert fetched.content_type == "application/geo+json"
    assert calls == 2
    await source.close()
    assert not http_client.is_closed
    await http_client.aclose()


@pytest.mark.asyncio
async def test_client_does_not_retry_permanent_client_failure() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = USGSClient(
            feed_url="https://example.test/missing.geojson",
            timeout_seconds=1,
            max_attempts=3,
            client=http_client,
        )
        with pytest.raises(PermanentSourceError, match="permanent HTTP 404"):
            await source.fetch()
    assert calls == 1


@pytest.mark.asyncio
async def test_client_exhausts_retryable_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = USGSClient(
            feed_url="https://example.test/rate-limited.geojson",
            timeout_seconds=1,
            max_attempts=1,
            client=http_client,
        )
        with pytest.raises(RetryableSourceError, match="429"):
            await source.fetch()


@pytest.mark.asyncio
async def test_client_sanitizes_transport_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed for https://example.test/secret-value", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = USGSClient(
            feed_url="https://example.test/secret-value",
            timeout_seconds=1,
            max_attempts=1,
            client=http_client,
        )
        with pytest.raises(RetryableSourceError) as caught:
            await source.fetch()

    assert "secret-value" not in str(caught.value)


@pytest.mark.asyncio
async def test_client_context_manager_and_owned_client_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def make_client(*, timeout: httpx.Timeout, headers: dict[str, str]) -> httpx.AsyncClient:
        del timeout, headers
        return http_client

    monkeypatch.setattr("atlas_pulse.sources.http.httpx.AsyncClient", make_client)
    source = USGSClient(
        feed_url="https://example.test/feed.geojson",
        timeout_seconds=1,
        max_attempts=1,
    )

    async with source as entered:
        assert entered is source

    assert http_client.is_closed

"""GDELT 2.0 Event export contract and resource-safety tests."""

import hashlib
import io
import zipfile
from datetime import UTC, datetime

import httpx
import pytest

from atlas_pulse.sources import PermanentSourceError
from atlas_pulse.sources.gdelt import GDELTClient, GDELTEventRecord, GDELTExportPointer, GDELTFeed

_EXPORT_NAME = "20260912143000.export.CSV"
_EXPORT_URL = f"https://data.gdeltproject.org/gdeltv2/{_EXPORT_NAME}.zip"


def gdelt_row(
    *,
    event_id: str = "1234567890",
    event_date: str = "20260912",
    actor1: str = "GOVERNMENT",
    actor2: str = "REBELS",
    is_root: str = "1",
    event_code: str = "190",
    base_code: str = "190",
    root_code: str = "19",
    quad_class: str = "4",
    goldstein: str = "-10.0",
    mentions: str = "7",
    geo_type: str = "4",
    place: str = "Chennai, Tamil Nadu, India",
    latitude: str = "13.0827",
    longitude: str = "80.2707",
    detected_at: str = "20260912143000",
    source_url: str = "https://news.example.org/reports/123",
) -> str:
    fields = [""] * 61
    values = {
        0: event_id,
        1: event_date,
        2: event_date[:6],
        3: event_date[:4],
        4: "2026.6986",
        5: "INDGOV",
        6: actor1,
        7: "IND",
        15: "REB",
        16: actor2,
        17: "IND",
        25: is_root,
        26: event_code,
        27: base_code,
        28: root_code,
        29: quad_class,
        30: goldstein,
        31: mentions,
        32: "3",
        33: "4",
        34: "-7.25",
        51: geo_type,
        52: place,
        53: "IN",
        54: "IN25",
        55: "",
        56: latitude,
        57: longitude,
        58: "-2103041",
        59: detected_at,
        60: source_url,
    }
    for index, value in values.items():
        fields[index] = value
    return "\t".join(fields)


def gdelt_rows() -> list[str]:
    return [
        gdelt_row(),
        gdelt_row(
            event_id="1234567891",
            actor1="",
            actor2="",
            event_code="200",
            base_code="200",
            root_code="20",
            goldstein="-10",
            mentions="3",
            geo_type="3",
            place="Houston, Texas, United States",
            latitude="29.7604",
            longitude="-95.3698",
            source_url="https://user:password@unsafe.example/report",
        ),
        gdelt_row(
            event_id="1234567892",
            event_code="130",
            base_code="130",
            root_code="13",
            quad_class="3",
        ),
        gdelt_row(
            event_id="1234567893",
            is_root="0",
            event_code="180",
            base_code="180",
            root_code="18",
        ),
        gdelt_row(
            event_id="1234567894",
            geo_type="1",
            place="India",
            latitude="20.0",
            longitude="77.0",
        ),
        gdelt_row(event_id="1234567895", mentions="1"),
    ]


def make_export(
    rows: list[str] | None = None,
    *,
    member_name: str = _EXPORT_NAME,
    content: bytes | None = None,
) -> bytes:
    payload = content if content is not None else ("\n".join(rows or gdelt_rows()) + "\n").encode()
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member_name, payload)
    return target.getvalue()


def make_manifest(export: bytes, *, checksum: str | None = None, size: int | None = None) -> bytes:
    digest = checksum or hashlib.md5(export, usedforsecurity=False).hexdigest()
    advertised_size = len(export) if size is None else size
    return (
        f"{advertised_size} {digest} http://data.gdeltproject.org/gdeltv2/{_EXPORT_NAME}.zip\n"
        "50 00000000000000000000000000000000 "
        "http://data.gdeltproject.org/gdeltv2/20260912143000.mentions.CSV.zip\n"
    ).encode()


def make_client(*, client: httpx.AsyncClient | None = None, **overrides: object) -> GDELTClient:
    arguments: dict[str, object] = {
        "last_update_url": "https://data.gdeltproject.org/gdeltv2/lastupdate.txt",
        "poll_timeout_seconds": 1.0,
        "max_attempts": 1,
        "user_agent": "AtlasPulse tests",
        "max_compressed_bytes": 1_000_000,
        "max_uncompressed_bytes": 2_000_000,
        "max_rows": 100,
        "max_events": 20,
        "active_window_hours": 24,
        "only_root_events": True,
        "minimum_geo_precision": 3,
        "minimum_mentions": 2,
        "client": client,
    }
    arguments.update(overrides)
    return GDELTClient(**arguments)  # type: ignore[arg-type]


def test_manifest_selects_one_official_export_and_upgrades_https() -> None:
    pointer = GDELTExportPointer.from_bytes(
        b"43079 1c4249788de4dad23dc694cd5466721f "
        b"http://data.gdeltproject.org/gdeltv2/20260912143000.export.CSV.zip\n"
        b"86039 363360a3d2dce725b99ca4ad35555c91 "
        b"http://data.gdeltproject.org/gdeltv2/20260912143000.mentions.CSV.zip\n"
    )

    assert pointer.expected_size == 43_079
    assert pointer.expected_md5 == "1c4249788de4dad23dc694cd5466721f"
    assert pointer.url == _EXPORT_URL
    assert pointer.exported_at == datetime(2026, 9, 12, 14, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        (b"\xff", "must be ASCII"),
        (b"two fields\n", "must contain three fields"),
        (b"", "exactly one Event export"),
        (
            b"x 00000000000000000000000000000000 "
            b"https://data.gdeltproject.org/gdeltv2/20260912143000.export.CSV.zip",
            "size must be an integer",
        ),
        (
            b"0 00000000000000000000000000000000 "
            b"https://data.gdeltproject.org/gdeltv2/20260912143000.export.CSV.zip",
            "size must be positive",
        ),
        (
            b"1 invalid https://data.gdeltproject.org/gdeltv2/20260912143000.export.CSV.zip",
            "checksum must be",
        ),
        (
            b"1 00000000000000000000000000000000 "
            b"https://evil.example/gdeltv2/20260912143000.export.CSV.zip",
            "official data host",
        ),
        (
            b"1 00000000000000000000000000000000 "
            b"https://data.gdeltproject.org/gdeltv2/20260912143000.export.CSV.zip?x=1",
            "official data host",
        ),
        (
            b"1 00000000000000000000000000000000 "
            b"https://data.gdeltproject.org:bad/gdeltv2/20260912143000.export.CSV.zip",
            "invalid port",
        ),
        (
            b"1 00000000000000000000000000000000 "
            b"https://data.gdeltproject.org/gdeltv2/20261312143000.export.CSV.zip",
            "valid YYYYMMDDHHMMSS",
        ),
    ],
)
def test_manifest_rejects_untrusted_or_malformed_entries(manifest: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        GDELTExportPointer.from_bytes(manifest)


def test_manifest_rejects_duplicate_event_exports() -> None:
    line = (
        b"1 00000000000000000000000000000000 "
        b"https://data.gdeltproject.org/gdeltv2/20260912143000.export.CSV.zip\n"
    )
    with pytest.raises(ValueError, match="exactly one Event export"):
        GDELTExportPointer.from_bytes(line + line)


def test_feed_selects_precise_root_material_conflicts_and_preserves_uncertainty() -> None:
    feed = GDELTFeed.from_zip(make_export(), max_uncompressed_bytes=1_000_000, max_rows=20)
    ingested_at = datetime(2026, 9, 12, 14, 31, tzinfo=UTC)

    events = feed.to_events(
        ingested_at=ingested_at,
        active_window_hours=24,
        only_root_events=True,
        minimum_geo_precision=3,
        minimum_mentions=2,
        max_events=10,
    )

    assert feed.exported_at == datetime(2026, 9, 12, 14, 30, tzinfo=UTC)
    assert len(feed.records) == 6
    assert [event.event_id for event in events] == ["1234567890", "1234567891"]
    first = events[0]
    assert first.source == "gdelt"
    assert first.event_type == "geopolitical.gdelt_event"
    assert first.occurred_at == datetime(2026, 9, 12, 14, 30, tzinfo=UTC)
    assert first.ingested_at == ingested_at
    assert first.location is not None
    assert first.location.latitude == 13.0827
    assert first.payload["category"] == "Fight"
    assert first.payload["priority"] == "High"
    assert first.payload["severity_rank"] == 3
    assert first.payload["goldstein_scale"] == -10.0
    assert first.payload["mentions"] == 7
    assert first.payload["geo_precision"] == "world city or landmark centroid"
    assert first.payload["reported_event_date"] == "2026-09-12"
    assert first.payload["expires_at"] == "2026-09-13T14:30:00+00:00"
    assert first.payload["source_url"] == "https://news.example.org/reports/123"
    assert "not independently verified" in str(first.payload["verification_status"])

    second = events[1]
    assert second.payload["title"] == "Mass violence"
    assert second.payload["priority"] == "Critical"
    assert second.payload["severity_rank"] == 4
    assert second.payload["source_report_url"] is None
    assert second.payload["source_url"] == "https://www.gdeltproject.org/data.html"


def test_feed_filters_are_configurable_but_event_cap_fails_loudly() -> None:
    feed = GDELTFeed.from_zip(make_export(), max_uncompressed_bytes=1_000_000, max_rows=20)
    ingested_at = datetime(2026, 9, 12, 14, 31, tzinfo=UTC)
    events = feed.to_events(
        ingested_at=ingested_at,
        active_window_hours=48,
        only_root_events=False,
        minimum_geo_precision=1,
        minimum_mentions=1,
        max_events=10,
    )
    assert [event.event_id for event in events] == [
        "1234567890",
        "1234567891",
        "1234567893",
        "1234567894",
        "1234567895",
    ]
    with pytest.raises(ValueError, match="selected events; limit is 1"):
        feed.to_events(
            ingested_at=ingested_at,
            active_window_hours=48,
            only_root_events=False,
            minimum_geo_precision=1,
            minimum_mentions=1,
            max_events=1,
        )


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("\t".join(["value"] * 60), "60 columns; expected 61"),
        (gdelt_row(event_date="20260230"), "event_date must be"),
        (gdelt_row(event_code="attack"), "event codes must contain"),
        (gdelt_row(root_code="99"), "root code must be"),
        (gdelt_row(latitude="91"), "latitude is outside"),
        (gdelt_row(longitude="181"), "longitude is outside"),
        (gdelt_row(longitude=""), "must contain both"),
        (gdelt_row(geo_type="0"), "type is required"),
        (gdelt_row(geo_type="4", latitude="", longitude=""), "coordinates are required"),
        (gdelt_row(detected_at="20260912149900"), "DATEADDED must be"),
    ],
)
def test_feed_reports_invalid_rows(row: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        GDELTFeed.from_zip(
            make_export([row]),
            max_uncompressed_bytes=1_000_000,
            max_rows=10,
        )


def test_unlocated_record_cannot_be_mapped_directly() -> None:
    row = gdelt_row(geo_type="", latitude="", longitude="")
    record = GDELTEventRecord.from_row(row.split("\t"), row_number=1)
    with pytest.raises(ValueError, match="without action coordinates"):
        record.to_event(
            ingested_at=datetime(2026, 9, 12, 14, 31, tzinfo=UTC),
            active_window_hours=24,
        )


def test_feed_rejects_unsafe_zip_shapes_and_resource_overruns() -> None:
    with pytest.raises(ValueError, match="valid ZIP"):
        GDELTFeed.from_zip(b"not a zip", max_uncompressed_bytes=100, max_rows=10)

    multiple = io.BytesIO()
    with zipfile.ZipFile(multiple, "w") as archive:
        archive.writestr(_EXPORT_NAME, gdelt_row())
        archive.writestr("extra.txt", "extra")
    with pytest.raises(ValueError, match="exactly one member"):
        GDELTFeed.from_zip(multiple.getvalue(), max_uncompressed_bytes=10_000, max_rows=10)

    with pytest.raises(ValueError, match="unexpected name"):
        GDELTFeed.from_zip(
            make_export(member_name="../escape.CSV"),
            max_uncompressed_bytes=10_000,
            max_rows=10,
        )
    with pytest.raises(ValueError, match="uncompressed byte limit"):
        GDELTFeed.from_zip(make_export(), max_uncompressed_bytes=10, max_rows=10)
    with pytest.raises(ValueError, match="row limit"):
        GDELTFeed.from_zip(make_export(), max_uncompressed_bytes=1_000_000, max_rows=1)
    with pytest.raises(ValueError, match="must be UTF-8"):
        GDELTFeed.from_zip(
            make_export(content=b"\xff"),
            max_uncompressed_bytes=100,
            max_rows=10,
        )
    with pytest.raises(ValueError, match="malformed TSV"):
        GDELTFeed.from_zip(
            make_export(content=b'"unterminated'),
            max_uncompressed_bytes=100,
            max_rows=10,
        )


@pytest.mark.asyncio
async def test_client_resolves_downloads_and_integrity_checks_latest_export() -> None:
    export = make_export()
    manifest = make_manifest(export)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        content = manifest if request.url.path.endswith("lastupdate.txt") else export
        return httpx.Response(200, content=content, request=request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source = make_client(client=http_client)
    document = await source.fetch()
    batch = source.normalize(
        document.raw,
        ingested_at=datetime(2026, 9, 12, 14, 31, tzinfo=UTC),
    )
    await source.close()

    assert [request.url.path for request in requests] == [
        "/gdeltv2/lastupdate.txt",
        f"/gdeltv2/{_EXPORT_NAME}.zip",
    ]
    assert requests[1].url.scheme == "https"
    assert document.raw == export
    assert document.source_url == _EXPORT_URL
    assert batch.generated_at == datetime(2026, 9, 12, 14, 30, tzinfo=UTC)
    assert len(batch.events) == 2
    assert not http_client.is_closed
    await http_client.aclose()


@pytest.mark.parametrize(
    ("manifest_factory", "export_factory", "message", "expected_calls"),
    [
        (
            lambda export: make_manifest(export, size=1_000_001),
            lambda export: export,
            "compressed byte limit",
            1,
        ),
        (
            lambda export: make_manifest(export, size=len(export) + 1),
            lambda export: export,
            "size does not match",
            2,
        ),
        (
            lambda export: make_manifest(export, checksum="0" * 32),
            lambda export: export,
            "checksum does not match",
            2,
        ),
    ],
)
@pytest.mark.asyncio
async def test_client_rejects_manifest_integrity_failures(
    manifest_factory: object,
    export_factory: object,
    message: str,
    expected_calls: int,
) -> None:
    export = make_export()
    manifest = manifest_factory(export)  # type: ignore[operator]
    response_export = export_factory(export)  # type: ignore[operator]
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = manifest if request.url.path.endswith("lastupdate.txt") else response_export
        return httpx.Response(200, content=content, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = make_client(client=http_client)
        with pytest.raises(PermanentSourceError, match=message):
            await source.fetch()
    assert calls == expected_calls


@pytest.mark.asyncio
async def test_client_bounds_manifest_response_before_parsing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5_000, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = make_client(client=http_client)
        with pytest.raises(PermanentSourceError, match="configured byte limit"):
            await source.fetch()


@pytest.mark.asyncio
async def test_client_closes_only_an_owned_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )

    def make_http_client(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
        return http_client

    monkeypatch.setattr("atlas_pulse.sources.gdelt.httpx.AsyncClient", make_http_client)
    source = make_client(client=None)
    async with source as entered:
        assert entered is source
    assert http_client.is_closed

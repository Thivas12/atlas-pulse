"""End-to-end in-process ingestion orchestration tests."""

from datetime import UTC, datetime
from pathlib import Path

from atlas_pulse.ingestion import IngestionService, RawSnapshotStore
from atlas_pulse.sources import FetchedDocument, NormalizedBatch, USGSFeed
from atlas_pulse.streams import InMemoryEventBus


class StaticSource:
    source_name = "usgs"
    snapshot_extension = "geojson"

    def __init__(self, payload: bytes, fetched_at: datetime) -> None:
        self._document = FetchedDocument(
            raw=payload,
            fetched_at=fetched_at,
            source_url="https://example.test/usgs.geojson",
            content_type="application/geo+json",
        )

    async def fetch(self) -> FetchedDocument:
        return self._document

    def normalize(self, raw: bytes, *, ingested_at: datetime) -> NormalizedBatch:
        feed = USGSFeed.from_bytes(raw)
        return NormalizedBatch(
            generated_at=datetime.fromtimestamp(feed.metadata.generated / 1000, tz=UTC),
            events=feed.to_events(ingested_at=ingested_at),
        )

    async def close(self) -> None:
        return None


async def test_ingestion_snapshots_before_idempotent_publish(
    tmp_path: Path, usgs_payload: bytes
) -> None:
    fetched_at = datetime(2024, 7, 10, 12, 6, tzinfo=UTC)
    bus = InMemoryEventBus()
    service = IngestionService(
        source=StaticSource(usgs_payload, fetched_at),
        snapshots=RawSnapshotStore(tmp_path),
        event_bus=bus,
    )

    first = await service.ingest_once()
    second = await service.ingest_once()

    assert first.source == "usgs"
    assert first.fetched_events == 2
    assert first.published_events == 2
    assert first.deduplicated_events == 0
    assert first.snapshot_created is True
    assert Path(first.snapshot_path).read_bytes() == usgs_payload
    assert second.published_events == 0
    assert second.deduplicated_events == 2
    assert second.snapshot_created is False
    assert second.snapshot_sha256 == first.snapshot_sha256
    assert len(await bus.latest(10)) == 2

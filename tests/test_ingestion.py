"""End-to-end in-process ingestion orchestration tests."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from atlas_pulse.ingestion import IngestionService, RawSnapshotStore
from atlas_pulse.source_poll_store import InMemorySourcePollStore
from atlas_pulse.source_polling import SourcePollPolicy
from atlas_pulse.sources import (
    FetchedDocument,
    NormalizedBatch,
    RetryableSourceError,
    USGSFeed,
)
from atlas_pulse.streams import InMemoryEventBus


class StaticSource:
    source_name: Literal["usgs"] = "usgs"
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
            timestamp_basis="source_metadata",
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


async def test_ingestion_records_poll_success_with_transport_provenance(
    tmp_path: Path, usgs_payload: bytes
) -> None:
    fetched_at = datetime(2024, 7, 10, 12, 6, tzinfo=UTC)
    source = StaticSource(usgs_payload, fetched_at)
    source._document = FetchedDocument(
        raw=usgs_payload,
        fetched_at=fetched_at,
        source_url="https://example.test/usgs.geojson",
        content_type="application/geo+json",
        transport_attempts=2,
    )
    poll_store = InMemorySourcePollStore()
    times = iter(
        (
            datetime(2024, 7, 10, 12, 5, 55, tzinfo=UTC),
            datetime(2024, 7, 10, 12, 6, 1, tzinfo=UTC),
        )
    )
    service = IngestionService(
        source=source,
        snapshots=RawSnapshotStore(tmp_path),
        event_bus=InMemoryEventBus(),
        source_poll_store=poll_store,
        source_poll_policy=SourcePollPolicy(
            source="usgs",
            interval_seconds=60,
            poll_stale_after_seconds=180,
            source_stale_after_seconds=600,
        ),
        clock=lambda: next(times),
    )

    result = await service.ingest_once()
    state = (await poll_store.load_states(("usgs",)))[0]

    assert result.transport_attempts == 2
    assert result.timestamp_basis == "source_metadata"
    assert state.current_attempt.outcome == "succeeded"
    assert state.current_attempt.transport_attempts == 2
    assert state.current_attempt.fetched_events == 2
    assert state.last_success == state.current_attempt


class FetchFailureSource(StaticSource):
    async def fetch(self) -> FetchedDocument:
        raise RetryableSourceError("secret transport detail", attempt_count=3)


async def test_ingestion_records_bounded_failure_without_raw_exception(
    tmp_path: Path, usgs_payload: bytes
) -> None:
    poll_store = InMemorySourcePollStore()
    started = datetime(2024, 7, 10, 12, tzinfo=UTC)
    times = iter((started, started))
    service = IngestionService(
        source=FetchFailureSource(usgs_payload, started),
        snapshots=RawSnapshotStore(tmp_path),
        event_bus=InMemoryEventBus(),
        source_poll_store=poll_store,
        source_poll_policy=SourcePollPolicy(
            source="usgs",
            interval_seconds=60,
            poll_stale_after_seconds=180,
            source_stale_after_seconds=600,
        ),
        clock=lambda: next(times),
    )

    with pytest.raises(RetryableSourceError, match="secret transport detail"):
        await service.ingest_once()
    state = (await poll_store.load_states(("usgs",)))[0]

    assert state.current_attempt.failure_code == "transport_exhausted"
    assert state.current_attempt.transport_attempts == 3
    assert "secret" not in state.current_attempt.model_dump_json()
    assert state.consecutive_failures == 1


def test_ingestion_requires_a_matching_store_and_policy(
    tmp_path: Path, usgs_payload: bytes
) -> None:
    source = StaticSource(usgs_payload, datetime(2024, 7, 10, tzinfo=UTC))
    with pytest.raises(ValueError, match="configured together"):
        IngestionService(
            source=source,
            snapshots=RawSnapshotStore(tmp_path),
            event_bus=InMemoryEventBus(),
            source_poll_store=InMemorySourcePollStore(),
        )
    with pytest.raises(ValueError, match="must match"):
        IngestionService(
            source=source,
            snapshots=RawSnapshotStore(tmp_path),
            event_bus=InMemoryEventBus(),
            source_poll_store=InMemorySourcePollStore(),
            source_poll_policy=SourcePollPolicy(
                source="nws",
                interval_seconds=120,
                poll_stale_after_seconds=360,
                source_stale_after_seconds=900,
            ),
        )

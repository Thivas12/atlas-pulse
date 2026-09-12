"""Observable fetch, snapshot, validation, and publication orchestration."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol

import structlog
from opentelemetry import metrics, trace

from atlas_pulse.ingestion.snapshot import RawSnapshotStore
from atlas_pulse.sources.usgs import FetchedDocument, USGSFeed
from atlas_pulse.streams.base import EventBus

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)
published_counter = meter.create_counter(
    "atlas.events.published", description="Source events appended to the event stream"
)
deduplicated_counter = meter.create_counter(
    "atlas.events.deduplicated", description="Repeated source events suppressed"
)
source_lag = meter.create_histogram(
    "atlas.source.lag", unit="s", description="Age of the source feed when fetched"
)


class SourceClient(Protocol):
    """Source-fetching capability required by the service."""

    async def fetch(self) -> FetchedDocument: ...


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Auditable summary of one poll cycle."""

    fetched_at: datetime
    source_generated_at: datetime
    snapshot_path: str
    snapshot_sha256: str
    snapshot_created: bool
    fetched_events: int
    published_events: int
    deduplicated_events: int


class IngestionService:
    """Execute one complete source-to-stream transaction boundary."""

    def __init__(
        self,
        *,
        source: SourceClient,
        snapshots: RawSnapshotStore,
        event_bus: EventBus,
    ) -> None:
        self._source = source
        self._snapshots = snapshots
        self._event_bus = event_bus

    async def ingest_once(self) -> IngestionResult:
        """Fetch, preserve, validate, normalize, and idempotently publish one feed."""
        with tracer.start_as_current_span("usgs.ingest_once") as span:
            fetched = await self._source.fetch()
            snapshot = self._snapshots.write(
                source="usgs",
                fetched_at=fetched.fetched_at,
                raw=fetched.raw,
                extension="geojson",
            )
            feed = USGSFeed.from_bytes(fetched.raw)
            events = feed.to_events(ingested_at=fetched.fetched_at)

            published = 0
            deduplicated = 0
            for event in events:
                outcome = await self._event_bus.publish(event)
                if outcome.deduplicated:
                    deduplicated += 1
                else:
                    published += 1

            generated_at = datetime.fromtimestamp(feed.metadata.generated / 1000, tz=UTC)
            lag_seconds = max(0.0, (fetched.fetched_at - generated_at).total_seconds())
            attributes = {"source": "usgs"}
            published_counter.add(published, attributes)
            deduplicated_counter.add(deduplicated, attributes)
            source_lag.record(lag_seconds, attributes)

            result = IngestionResult(
                fetched_at=fetched.fetched_at,
                source_generated_at=generated_at,
                snapshot_path=str(snapshot.path),
                snapshot_sha256=snapshot.sha256,
                snapshot_created=snapshot.created,
                fetched_events=len(events),
                published_events=published,
                deduplicated_events=deduplicated,
            )
            span.set_attribute("atlas.events.fetched", len(events))
            span.set_attribute("atlas.events.published", published)
            span.set_attribute("atlas.events.deduplicated", deduplicated)
            span.set_attribute("atlas.snapshot.sha256", snapshot.sha256)
            await logger.ainfo("ingestion_cycle_completed", **asdict(result))
            return result

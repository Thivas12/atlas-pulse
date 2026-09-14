"""Observable fetch, snapshot, validation, and publication orchestration."""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import structlog
from opentelemetry import metrics, trace

from atlas_pulse.ingestion.snapshot import RawSnapshotStore
from atlas_pulse.source_poll_store import SourcePollStore
from atlas_pulse.source_polling import (
    SourcePollAttempt,
    SourcePollFailureCode,
    SourcePollPolicy,
    SourcePollStage,
    new_source_poll_attempt,
)
from atlas_pulse.sources import (
    PermanentSourceError,
    RetryableSourceError,
    SourceAdapter,
    SourceFetchError,
)
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


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Auditable summary of one poll cycle."""

    source: str
    fetched_at: datetime
    source_generated_at: datetime
    snapshot_path: str
    snapshot_sha256: str
    snapshot_created: bool
    fetched_events: int
    published_events: int
    deduplicated_events: int
    transport_attempts: int
    timestamp_basis: str


def _failure_code(error: Exception, stage: SourcePollStage) -> SourcePollFailureCode:
    if isinstance(error, RetryableSourceError):
        return "transport_exhausted"
    if isinstance(error, PermanentSourceError):
        return "source_rejected"
    if stage == "snapshot":
        return "snapshot_write_failed"
    if stage == "normalize":
        return "source_payload_invalid"
    if stage == "publish":
        return "publication_unavailable"
    return "unexpected_failure"


class IngestionService:
    """Execute one complete source-to-stream transaction boundary."""

    def __init__(
        self,
        *,
        source: SourceAdapter,
        snapshots: RawSnapshotStore,
        event_bus: EventBus,
        source_poll_store: SourcePollStore | None = None,
        source_poll_policy: SourcePollPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if (source_poll_store is None) != (source_poll_policy is None):
            raise ValueError("source poll store and policy must be configured together")
        if source_poll_policy is not None and source_poll_policy.source != source.source_name:
            raise ValueError("source poll policy must match the ingestion source")
        self._source = source
        self._snapshots = snapshots
        self._event_bus = event_bus
        self._source_poll_store = source_poll_store
        self._source_poll_policy = source_poll_policy
        self._clock = clock

    async def ingest_once(self) -> IngestionResult:
        """Fetch, preserve, validate, normalize, and idempotently publish one feed."""
        with tracer.start_as_current_span(f"{self._source.source_name}.ingest_once") as span:
            attempt = new_source_poll_attempt(
                self._source.source_name,
                started_at=self._clock(),
            )
            if self._source_poll_store is not None:
                assert self._source_poll_policy is not None
                await self._source_poll_store.record_started(attempt, self._source_poll_policy)

            stage: SourcePollStage = "fetch"
            transport_attempts = 0
            batch = None
            try:
                fetched = await self._source.fetch()
                transport_attempts = fetched.transport_attempts
                stage = "snapshot"
                snapshot = self._snapshots.write(
                    source=self._source.source_name,
                    fetched_at=fetched.fetched_at,
                    raw=fetched.raw,
                    extension=self._source.snapshot_extension,
                )
                stage = "normalize"
                batch = self._source.normalize(fetched.raw, ingested_at=fetched.fetched_at)
                events = batch.events
                stage = "publish"
                outcomes = await self._event_bus.publish_many(events)
                published = sum(not outcome.deduplicated for outcome in outcomes)
                deduplicated = len(outcomes) - published
            except Exception as error:
                if isinstance(error, SourceFetchError):
                    transport_attempts = error.attempt_count
                if self._source_poll_store is not None:
                    failed_attempt = SourcePollAttempt(
                        attempt_id=attempt.attempt_id,
                        source=attempt.source,
                        started_at=attempt.started_at,
                        completed_at=self._clock(),
                        outcome="failed",
                        stage=stage,
                        transport_attempts=transport_attempts,
                        source_generated_at=(batch.generated_at if batch is not None else None),
                        timestamp_basis=(batch.timestamp_basis if batch is not None else None),
                        failure_code=_failure_code(error, stage),
                    )
                    try:
                        await self._source_poll_store.record_completed(failed_attempt)
                    except Exception:
                        await logger.aexception(
                            "source_poll_failure_recording_failed",
                            source=self._source.source_name,
                            attempt_id=attempt.attempt_id,
                        )
                raise

            lag_seconds = max(0.0, (fetched.fetched_at - batch.generated_at).total_seconds())
            attributes = {"source": self._source.source_name}
            published_counter.add(published, attributes)
            deduplicated_counter.add(deduplicated, attributes)
            source_lag.record(lag_seconds, attributes)

            result = IngestionResult(
                source=self._source.source_name,
                fetched_at=fetched.fetched_at,
                source_generated_at=batch.generated_at,
                snapshot_path=str(snapshot.path),
                snapshot_sha256=snapshot.sha256,
                snapshot_created=snapshot.created,
                fetched_events=len(events),
                published_events=published,
                deduplicated_events=deduplicated,
                transport_attempts=transport_attempts,
                timestamp_basis=batch.timestamp_basis,
            )
            if self._source_poll_store is not None:
                await self._source_poll_store.record_completed(
                    SourcePollAttempt(
                        attempt_id=attempt.attempt_id,
                        source=attempt.source,
                        started_at=attempt.started_at,
                        completed_at=self._clock(),
                        outcome="succeeded",
                        stage="complete",
                        transport_attempts=transport_attempts,
                        source_generated_at=batch.generated_at,
                        timestamp_basis=batch.timestamp_basis,
                        fetched_events=len(events),
                        published_events=published,
                        deduplicated_events=deduplicated,
                    )
                )
            span.set_attribute("atlas.events.fetched", len(events))
            span.set_attribute("atlas.source", self._source.source_name)
            span.set_attribute("atlas.events.published", published)
            span.set_attribute("atlas.events.deduplicated", deduplicated)
            span.set_attribute("atlas.snapshot.sha256", snapshot.sha256)
            span.set_attribute("atlas.source.transport_attempts", transport_attempts)
            span.set_attribute("atlas.source.timestamp_basis", batch.timestamp_basis)
            await logger.ainfo("ingestion_cycle_completed", **asdict(result))
            return result

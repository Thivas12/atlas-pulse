"""Restart-safe Valkey-to-PostGIS projection orchestration."""

from dataclasses import asdict, dataclass

import structlog
from opentelemetry import metrics, trace

from atlas_pulse.projections.base import ProjectionStore
from atlas_pulse.streams import EventBus

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)
projected_counter = meter.create_counter(
    "atlas.projection.events", description="Event revisions committed to durable projections"
)


@dataclass(frozen=True, slots=True)
class ProjectionCycle:
    """Auditable outcome of one bounded projection cycle."""

    projection_name: str
    previous_checkpoint: str | None
    checkpoint: str | None
    projected_events: int


class ProjectionService:
    """Read strictly after the durable checkpoint and commit a bounded batch."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        store: ProjectionStore,
        projection_name: str,
        batch_size: int,
    ) -> None:
        self._event_bus = event_bus
        self._store = store
        self._projection_name = projection_name
        self._batch_size = batch_size

    async def project_once(self) -> ProjectionCycle:
        """Project one replay page; failed transactions leave the checkpoint unchanged."""
        previous = await self._store.checkpoint(self._projection_name)
        messages = await self._event_bus.replay(after=previous, limit=self._batch_size)
        if not messages:
            result = ProjectionCycle(
                projection_name=self._projection_name,
                previous_checkpoint=previous,
                checkpoint=previous,
                projected_events=0,
            )
            await logger.adebug("projection_cycle_idle", **asdict(result))
            return result

        with tracer.start_as_current_span("signals.project_once") as span:
            await self._store.project(
                projection_name=self._projection_name,
                messages=messages,
            )
            checkpoint = messages[-1].stream_id
            projected_counter.add(
                len(messages),
                {"projection": self._projection_name},
            )
            span.set_attribute("atlas.projection.events", len(messages))
            span.set_attribute("atlas.projection.checkpoint", checkpoint)
            result = ProjectionCycle(
                projection_name=self._projection_name,
                previous_checkpoint=previous,
                checkpoint=checkpoint,
                projected_events=len(messages),
            )
            await logger.ainfo("projection_cycle_completed", **asdict(result))
            return result

"""Long-running restart-safe Valkey-to-PostGIS projection worker."""

import asyncio
import signal
from contextlib import suppress

import structlog

from atlas_pulse.config import get_settings
from atlas_pulse.logging import configure_logging
from atlas_pulse.projections import PostgresSignalStore, ProjectionService
from atlas_pulse.streams import ValkeyEventBus
from atlas_pulse.telemetry import configure_telemetry

logger = structlog.get_logger(__name__)


async def run() -> None:
    """Project retained stream history, then follow new revisions until stopped."""
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_telemetry(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    event_bus = ValkeyEventBus(
        url=settings.valkey_url,
        stream=settings.event_stream,
        max_length=settings.stream_max_length,
        dedupe_ttl_seconds=settings.dedupe_ttl_seconds,
    )
    store = PostgresSignalStore(database_url=settings.database_url)
    service = ProjectionService(
        event_bus=event_bus,
        store=store,
        projection_name=settings.projection_name,
        batch_size=settings.projection_batch_size,
    )

    try:
        while not stop.is_set():
            projected_events = 0
            try:
                cycle = await service.project_once()
                projected_events = cycle.projected_events
            except asyncio.CancelledError:
                raise
            except Exception:
                await logger.aexception(
                    "projection_cycle_failed",
                    projection_name=settings.projection_name,
                )

            if projected_events < settings.projection_batch_size:
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop.wait(),
                        timeout=settings.projection_poll_seconds,
                    )
    finally:
        try:
            await event_bus.close()
        finally:
            await store.close()


def main() -> None:
    """Console-script wrapper."""
    asyncio.run(run())

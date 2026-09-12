"""Long-running USGS ingestion worker process."""

import asyncio
import signal
from contextlib import suppress

import structlog

from atlas_pulse.config import get_settings
from atlas_pulse.ingestion import IngestionService, RawSnapshotStore
from atlas_pulse.logging import configure_logging
from atlas_pulse.sources import USGSClient
from atlas_pulse.streams import ValkeyEventBus
from atlas_pulse.telemetry import configure_telemetry

logger = structlog.get_logger(__name__)


async def run() -> None:
    """Poll until SIGINT/SIGTERM, isolating transient cycle failures."""
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
    source = USGSClient(
        feed_url=str(settings.usgs_feed_url),
        timeout_seconds=settings.source_timeout_seconds,
        max_attempts=settings.source_max_attempts,
    )
    service = IngestionService(
        source=source,
        snapshots=RawSnapshotStore(settings.raw_data_dir),
        event_bus=event_bus,
    )

    try:
        while not stop.is_set():
            try:
                await service.ingest_once()
            except Exception:
                await logger.aexception("ingestion_cycle_failed", source="usgs")
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.usgs_poll_seconds)
    finally:
        await source.close()
        await event_bus.close()


def main() -> None:
    """Console-script wrapper."""
    asyncio.run(run())

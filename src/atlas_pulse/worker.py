"""Long-running, independently scheduled multi-source ingestion worker."""

import asyncio
import signal
from contextlib import suppress

import structlog

from atlas_pulse.config import get_settings
from atlas_pulse.ingestion import IngestionService, RawSnapshotStore
from atlas_pulse.logging import configure_logging
from atlas_pulse.sources import FIRMSClient, GDELTClient, NWSClient, SourceAdapter, USGSClient
from atlas_pulse.streams import ValkeyEventBus
from atlas_pulse.telemetry import configure_telemetry

logger = structlog.get_logger(__name__)


async def poll_source(
    *,
    source: SourceAdapter,
    service: IngestionService,
    interval_seconds: float,
    stop: asyncio.Event,
) -> None:
    """Poll one source without allowing its failures to stop another source."""
    while not stop.is_set():
        try:
            await service.ingest_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            await logger.aexception("ingestion_cycle_failed", source=source.source_name)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)


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
    usgs = USGSClient(
        feed_url=str(settings.usgs_feed_url),
        timeout_seconds=settings.source_timeout_seconds,
        max_attempts=settings.source_max_attempts,
        user_agent=settings.source_user_agent,
    )
    nws = NWSClient(
        alerts_url=str(settings.nws_alerts_url),
        timeout_seconds=settings.source_timeout_seconds,
        max_attempts=settings.source_max_attempts,
        user_agent=settings.source_user_agent,
    )
    snapshots = RawSnapshotStore(settings.raw_data_dir)
    sources: list[tuple[SourceAdapter, float]] = [
        (usgs, settings.usgs_poll_seconds),
        (nws, settings.nws_poll_seconds),
    ]
    if settings.firms_enabled:
        assert settings.firms_map_key is not None
        firms = FIRMSClient(
            api_base_url=str(settings.firms_api_base_url),
            map_key=settings.firms_map_key.get_secret_value(),
            product=settings.firms_product,
            area=settings.firms_area,
            day_range=settings.firms_day_range,
            active_window_hours=settings.firms_active_window_hours,
            timeout_seconds=settings.source_timeout_seconds,
            max_attempts=settings.source_max_attempts,
            user_agent=settings.source_user_agent,
        )
        sources.append((firms, settings.firms_poll_seconds))
    if settings.gdelt_enabled:
        gdelt = GDELTClient(
            last_update_url=str(settings.gdelt_last_update_url),
            poll_timeout_seconds=settings.source_timeout_seconds,
            max_attempts=settings.source_max_attempts,
            user_agent=settings.source_user_agent,
            max_compressed_bytes=settings.gdelt_max_compressed_bytes,
            max_uncompressed_bytes=settings.gdelt_max_uncompressed_bytes,
            max_rows=settings.gdelt_max_rows,
            max_events=settings.gdelt_max_events,
            active_window_hours=settings.gdelt_active_window_hours,
            only_root_events=settings.gdelt_only_root_events,
            minimum_geo_precision=settings.gdelt_minimum_geo_precision,
            minimum_mentions=settings.gdelt_minimum_mentions,
        )
        sources.append((gdelt, settings.gdelt_poll_seconds))
    tasks = [
        asyncio.create_task(
            poll_source(
                source=source,
                service=IngestionService(
                    source=source,
                    snapshots=snapshots,
                    event_bus=event_bus,
                ),
                interval_seconds=interval,
                stop=stop,
            ),
            name=f"{source.source_name}-poller",
        )
        for source, interval in sources
    ]

    try:
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(source.close() for source, _interval in sources))
        await event_bus.close()


def main() -> None:
    """Console-script wrapper."""
    asyncio.run(run())

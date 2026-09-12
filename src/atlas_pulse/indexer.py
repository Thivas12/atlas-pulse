"""Long-running, failure-isolated Valkey-to-hybrid-retrieval worker."""

import asyncio
import signal
from contextlib import suppress

import structlog

from atlas_pulse.config import get_settings
from atlas_pulse.logging import configure_logging
from atlas_pulse.retrieval import (
    FastEmbedProvider,
    PostgresRetrievalStore,
    RetrievalIndexerService,
)
from atlas_pulse.streams import ValkeyEventBus
from atlas_pulse.telemetry import configure_telemetry

logger = structlog.get_logger(__name__)


async def run() -> None:
    """Backfill retained history, then index new revisions until stopped."""
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
    store = PostgresRetrievalStore(database_url=settings.database_url)
    embedder = FastEmbedProvider(
        model_name=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        cache_dir=str(settings.embedding_cache_dir),
        threads=settings.embedding_threads,
        model_path=str(settings.embedding_model_path) if settings.embedding_model_path else None,
        local_files_only=settings.embedding_local_files_only,
    )
    service = RetrievalIndexerService(
        event_bus=event_bus,
        store=store,
        embedder=embedder,
        projection_name=settings.retrieval_projection_name,
        batch_size=settings.retrieval_batch_size,
    )

    try:
        while not stop.is_set():
            indexed_documents = 0
            try:
                cycle = await service.index_once()
                indexed_documents = cycle.indexed_documents
            except asyncio.CancelledError:
                raise
            except Exception:
                await logger.aexception(
                    "retrieval_index_cycle_failed",
                    projection_name=settings.retrieval_projection_name,
                )

            if indexed_documents < settings.retrieval_batch_size:
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop.wait(),
                        timeout=settings.retrieval_poll_seconds,
                    )
    finally:
        try:
            await event_bus.close()
        finally:
            await store.close()


def main() -> None:
    """Console-script wrapper."""
    asyncio.run(run())

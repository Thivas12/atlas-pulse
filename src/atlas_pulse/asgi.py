"""Production ASGI process entry point."""

from atlas_pulse.api import create_app
from atlas_pulse.config import get_settings
from atlas_pulse.logging import configure_logging
from atlas_pulse.projections import PostgresSignalStore
from atlas_pulse.retrieval import FastEmbedProvider, HybridSearchService, PostgresRetrievalStore
from atlas_pulse.streams import ValkeyEventBus
from atlas_pulse.telemetry import configure_telemetry, instrument_fastapi

settings = get_settings()
configure_logging(settings.log_level)
configure_telemetry(settings)

event_bus = ValkeyEventBus(
    url=settings.valkey_url,
    stream=settings.event_stream,
    max_length=settings.stream_max_length,
    dedupe_ttl_seconds=settings.dedupe_ttl_seconds,
)
signal_store = PostgresSignalStore(database_url=settings.database_url)
retrieval_store = PostgresRetrievalStore(database_url=settings.database_url)
embedder = FastEmbedProvider(
    model_name=settings.embedding_model,
    dimensions=settings.embedding_dimensions,
    cache_dir=str(settings.embedding_cache_dir),
    threads=settings.embedding_threads,
    model_path=str(settings.embedding_model_path) if settings.embedding_model_path else None,
    local_files_only=settings.embedding_local_files_only,
)
search_service = HybridSearchService(store=retrieval_store, embedder=embedder)
app = create_app(event_bus, signal_store, search_service)
instrument_fastapi(app)

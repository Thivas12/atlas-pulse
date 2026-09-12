"""Production ASGI process entry point."""

from atlas_pulse.api import create_app
from atlas_pulse.config import get_settings
from atlas_pulse.logging import configure_logging
from atlas_pulse.projections import PostgresSignalStore
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
app = create_app(event_bus, signal_store)
instrument_fastapi(app)

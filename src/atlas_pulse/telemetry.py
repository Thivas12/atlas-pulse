"""OpenTelemetry bootstrap kept at process boundaries."""

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from atlas_pulse.config import Settings


def configure_telemetry(settings: Settings) -> None:
    """Install a tracer provider with local-console or OTLP export."""
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.otel_service_name,
                "deployment.environment.name": settings.environment,
            }
        )
    )
    if settings.otel_exporter_otlp_endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
        )
    else:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()


def instrument_fastapi(app: FastAPI) -> None:
    """Instrument the production ASGI app without coupling tests to globals."""
    FastAPIInstrumentor.instrument_app(app)

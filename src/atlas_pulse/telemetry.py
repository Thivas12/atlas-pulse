"""OpenTelemetry bootstrap kept at process boundaries."""

import os
import re

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor, RequestInfo
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from atlas_pulse.config import Settings

_FIRMS_CREDENTIAL = re.compile(r"(/api/area/(?:csv|json|kml)/)[^/?#]+")
_FIRMS_HTTPX_EXCLUSION = r"/api/area/(csv|json|kml)/"


def redact_source_url(url: str) -> str:
    """Remove path credentials before a source URL enters telemetry."""
    return _FIRMS_CREDENTIAL.sub(r"\1[REDACTED]", url)


def _redact_httpx_request(span: trace.Span, request: RequestInfo) -> None:
    sanitized = redact_source_url(str(request.url))
    if sanitized != str(request.url):
        span.set_attribute("http.url", sanitized)
        span.set_attribute("url.full", sanitized)


async def _redact_async_httpx_request(span: trace.Span, request: RequestInfo) -> None:
    _redact_httpx_request(span, request)


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
    exclusion_variable = "OTEL_PYTHON_HTTPX_EXCLUDED_URLS"
    existing_exclusions = os.getenv(exclusion_variable, "")
    if _FIRMS_HTTPX_EXCLUSION not in existing_exclusions.split(","):
        os.environ[exclusion_variable] = ",".join(
            value for value in (existing_exclusions, _FIRMS_HTTPX_EXCLUSION) if value
        )
    HTTPXClientInstrumentor().instrument(
        request_hook=_redact_httpx_request,
        async_request_hook=_redact_async_httpx_request,
    )


def instrument_fastapi(app: FastAPI) -> None:
    """Instrument the production ASGI app without coupling tests to globals."""
    FastAPIInstrumentor.instrument_app(app)

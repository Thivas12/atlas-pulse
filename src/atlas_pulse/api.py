"""FastAPI read surface for health and recent normalized events."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agent_rag_core import Event
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel

from atlas_pulse import __version__
from atlas_pulse.streams.base import EventBus


class HealthResponse(BaseModel):
    """Liveness or readiness response."""

    status: str
    version: str


class EventEnvelope(BaseModel):
    """Normalized event paired with its replay position."""

    stream_id: str
    event: Event


class EventsResponse(BaseModel):
    """Bounded newest-first event page."""

    count: int
    items: tuple[EventEnvelope, ...]


def create_app(event_bus: EventBus) -> FastAPI:
    """Create an application with an injected stream implementation."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await event_bus.close()

    app = FastAPI(
        title="AtlasPulse API",
        summary="Real-time, evidence-grounded global disruption intelligence",
        version=__version__,
        lifespan=lifespan,
    )

    @app.get("/healthz", response_model=HealthResponse, tags=["operations"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @app.get("/readyz", response_model=HealthResponse, tags=["operations"])
    async def readiness() -> HealthResponse:
        if not await event_bus.is_ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="event stream unavailable",
            )
        return HealthResponse(status="ready", version=__version__)

    @app.get("/v1/events", response_model=EventsResponse, tags=["events"])
    async def latest_events(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> EventsResponse:
        messages = await event_bus.latest(limit)
        items = tuple(
            EventEnvelope(stream_id=message.stream_id, event=message.event) for message in messages
        )
        return EventsResponse(count=len(items), items=items)

    return app

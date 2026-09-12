"""FastAPI read surface for immutable history and projected current signals."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from agent_rag_core import Event
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel

from atlas_pulse import __version__
from atlas_pulse.correlation import (
    CORRELATION_CAVEAT,
    CORRELATION_RULE_VERSION,
    IncidentCandidate,
    build_incident_candidates,
)
from atlas_pulse.projections import CorrelationQuery, GeoBounds, SignalQuery, SignalStore
from atlas_pulse.projections.base import SourceName
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


class ReplayResponse(BaseModel):
    """Deterministic oldest-first page with an exclusive continuation cursor."""

    count: int
    items: tuple[EventEnvelope, ...]
    next_cursor: str | None
    has_more: bool
    order: Literal["oldest_first"] = "oldest_first"


class SignalsResponse(BaseModel):
    """Keyset-paginated newest-first current-state page."""

    count: int
    items: tuple[EventEnvelope, ...]
    next_cursor: str | None
    has_more: bool
    order: Literal["newest_revision_first"] = "newest_revision_first"


class IncidentCenterResponse(BaseModel):
    """Spherical mean of the participating source focus points."""

    latitude: float
    longitude: float


class EvidenceNodeResponse(BaseModel):
    """One current signal retained verbatim in an evidence graph."""

    node_id: str
    stream_id: str
    event: Event


class EvidenceEdgeResponse(BaseModel):
    """One measured, non-causal cross-source relationship."""

    edge_id: str
    from_node_id: str
    to_node_id: str
    relation: Literal["spatiotemporal_cooccurrence"]
    spatial_relation: Literal["intersects", "within_radius"]
    distance_km: float
    time_delta_minutes: float
    from_geometry_basis: Literal["point", "polygon"]
    to_geometry_basis: Literal["point", "polygon"]
    rule_version: str


class IncidentCandidateResponse(BaseModel):
    """A reproducible graph component that remains explicitly unverified."""

    incident_id: str
    title: str
    started_at: datetime
    latest_signal_at: datetime
    center: IncidentCenterResponse | None
    sources: tuple[str, ...]
    node_count: int
    edge_count: int
    max_distance_km: float
    time_span_minutes: float
    nodes: tuple[EvidenceNodeResponse, ...]
    edges: tuple[EvidenceEdgeResponse, ...]
    rule_version: str
    caveat: str


class CorrelationParametersResponse(BaseModel):
    """Exact bounded rule parameters used for this graph snapshot."""

    radius_km: float
    time_window_minutes: int
    lookback_hours: int
    candidate_edge_limit: int
    incident_limit: int
    active_only: bool
    bbox: tuple[float, float, float, float] | None


class IncidentsResponse(BaseModel):
    """Bounded deterministic cross-source incident candidates."""

    count: int
    total_incidents: int
    items: tuple[IncidentCandidateResponse, ...]
    incidents_truncated: bool
    candidate_edges_truncated: bool
    rule_version: str = CORRELATION_RULE_VERSION
    caveat: str = CORRELATION_CAVEAT
    parameters: CorrelationParametersResponse


def _incident_response(incident: IncidentCandidate) -> IncidentCandidateResponse:
    center = (
        IncidentCenterResponse(
            latitude=incident.center_latitude,
            longitude=incident.center_longitude,
        )
        if incident.center_latitude is not None and incident.center_longitude is not None
        else None
    )
    return IncidentCandidateResponse(
        incident_id=incident.incident_id,
        title=incident.title,
        started_at=incident.started_at,
        latest_signal_at=incident.latest_signal_at,
        center=center,
        sources=incident.sources,
        node_count=len(incident.nodes),
        edge_count=len(incident.edges),
        max_distance_km=incident.max_distance_km,
        time_span_minutes=incident.time_span_minutes,
        nodes=tuple(
            EvidenceNodeResponse(
                node_id=node.node_id,
                stream_id=node.message.stream_id,
                event=node.message.event,
            )
            for node in incident.nodes
        ),
        edges=tuple(
            EvidenceEdgeResponse(
                edge_id=edge.edge_id,
                from_node_id=edge.from_node_id,
                to_node_id=edge.to_node_id,
                relation=edge.relation,
                spatial_relation=edge.spatial_relation,
                distance_km=edge.distance_km,
                time_delta_minutes=edge.time_delta_minutes,
                from_geometry_basis=edge.from_geometry_basis,
                to_geometry_basis=edge.to_geometry_basis,
                rule_version=edge.rule_version,
            )
            for edge in incident.edges
        ),
        rule_version=incident.rule_version,
        caveat=incident.caveat,
    )


def _bounds_from_query(bbox: str | None) -> GeoBounds | None:
    if bbox is None:
        return None
    try:
        values = tuple(float(value.strip()) for value in bbox.split(","))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="bbox must contain four numbers: west,south,east,north",
        ) from error
    if len(values) != 4:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="bbox must contain four numbers: west,south,east,north",
        )
    try:
        return GeoBounds(west=values[0], south=values[1], east=values[2], north=values[3])
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


def _validate_time_window(
    occurred_after: datetime | None,
    occurred_before: datetime | None,
) -> None:
    for value in (occurred_after, occurred_before):
        if value is not None and value.tzinfo is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="time filters must include a UTC offset",
            )
    if (
        occurred_after is not None
        and occurred_before is not None
        and occurred_after > occurred_before
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="occurred_after must not be later than occurred_before",
        )


def create_app(event_bus: EventBus, signal_store: SignalStore | None = None) -> FastAPI:
    """Create an application with an injected stream implementation."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            try:
                await event_bus.close()
            finally:
                if signal_store is not None:
                    await signal_store.close()

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
        if signal_store is not None and not await signal_store.is_ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="signal projection unavailable",
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

    @app.get("/v1/events/replay", response_model=ReplayResponse, tags=["events"])
    async def replay_events(
        after: str | None = Query(default=None, pattern=r"^\d+-\d+$"),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> ReplayResponse:
        page = await event_bus.replay(after=after, limit=limit + 1)
        has_more = len(page) > limit
        visible = page[:limit]
        items = tuple(
            EventEnvelope(stream_id=message.stream_id, event=message.event) for message in visible
        )
        return ReplayResponse(
            count=len(items),
            items=items,
            next_cursor=items[-1].stream_id if items else None,
            has_more=has_more,
        )

    @app.get("/v1/signals", response_model=SignalsResponse, tags=["signals"])
    async def current_signals(
        limit: int = Query(default=100, ge=1, le=500),
        after: str | None = Query(default=None, pattern=r"^\d+-\d+$"),
        source: SourceName | None = None,
        min_severity: int | None = Query(default=None, ge=0, le=4),
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        bbox: str | None = Query(
            default=None,
            description="Non-wrapping WGS84 west,south,east,north viewport",
        ),
        include_area_only: bool = Query(default=False),
        active_only: bool = Query(default=True),
    ) -> SignalsResponse:
        if signal_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="signal projection unavailable",
            )
        _validate_time_window(occurred_after, occurred_before)
        page = await signal_store.query_current(
            SignalQuery(
                limit=limit,
                after=after,
                source=source,
                min_severity=min_severity,
                occurred_after=occurred_after,
                occurred_before=occurred_before,
                bounds=_bounds_from_query(bbox),
                include_area_only=include_area_only,
                active_only=active_only,
            )
        )
        items = tuple(
            EventEnvelope(stream_id=message.stream_id, event=message.event)
            for message in page.items
        )
        return SignalsResponse(
            count=len(items),
            items=items,
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )

    @app.get("/v1/incidents", response_model=IncidentsResponse, tags=["incidents"])
    async def incident_candidates(
        limit: int = Query(default=50, ge=1, le=100),
        radius_km: float = Query(default=50.0, gt=0, le=500),
        time_window_minutes: int = Query(default=360, ge=1, le=1_440),
        lookback_hours: int = Query(default=24, ge=1, le=168),
        candidate_edge_limit: int = Query(default=2_000, ge=1, le=5_000),
        bbox: str | None = Query(
            default=None,
            description="Optional non-wrapping WGS84 west,south,east,north viewport",
        ),
        active_only: bool = Query(default=True),
    ) -> IncidentsResponse:
        if signal_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="signal projection unavailable",
            )
        bounds = _bounds_from_query(bbox)
        query = CorrelationQuery(
            radius_km=radius_km,
            time_window_minutes=time_window_minutes,
            lookback_hours=lookback_hours,
            edge_limit=candidate_edge_limit,
            bounds=bounds,
            active_only=active_only,
        )
        result = build_incident_candidates(
            await signal_store.query_correlations(query),
            limit=limit,
        )
        return IncidentsResponse(
            count=len(result.incidents),
            total_incidents=result.total_incidents,
            items=tuple(_incident_response(incident) for incident in result.incidents),
            incidents_truncated=result.incidents_truncated,
            candidate_edges_truncated=result.candidate_edges_truncated,
            parameters=CorrelationParametersResponse(
                radius_km=query.radius_km,
                time_window_minutes=query.time_window_minutes,
                lookback_hours=query.lookback_hours,
                candidate_edge_limit=query.edge_limit,
                incident_limit=limit,
                active_only=query.active_only,
                bbox=(bounds.west, bounds.south, bounds.east, bounds.north) if bounds else None,
            ),
        )

    return app

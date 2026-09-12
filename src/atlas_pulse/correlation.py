"""Deterministic incident candidates built from explicit cross-source evidence edges."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from atlas_pulse.streams import StreamMessage

CORRELATION_RULE_VERSION = "spatiotemporal-v1"
CORRELATION_RELATION: Literal["spatiotemporal_cooccurrence"] = "spatiotemporal_cooccurrence"
CORRELATION_CAVEAT = (
    "Edges prove bounded spatial and temporal co-occurrence only; they do not establish "
    "causation, corroboration, or a shared real-world incident."
)

GeometryBasis = Literal["point", "polygon"]
SpatialRelation = Literal["intersects", "within_radius"]


def evidence_node_id(message: StreamMessage) -> str:
    """Return the revision-independent source identity used by graph edges."""
    return f"{message.event.source}:{message.event.event_id}"


@dataclass(frozen=True, slots=True)
class CorrelationPair:
    """One cross-source pair measured by PostGIS."""

    left: StreamMessage
    right: StreamMessage
    distance_km: float
    time_delta_minutes: float
    left_geometry_basis: GeometryBasis
    right_geometry_basis: GeometryBasis

    def __post_init__(self) -> None:
        if self.left.event.source == self.right.event.source:
            raise ValueError("correlation pairs must come from different sources")
        for value, label in (
            (self.distance_km, "distance_km"),
            (self.time_delta_minutes, "time_delta_minutes"),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{label} must be a finite non-negative number")


@dataclass(frozen=True, slots=True)
class CorrelationBatch:
    """Bounded database result with an explicit truncation marker."""

    pairs: tuple[CorrelationPair, ...]
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceNode:
    """One current source signal participating in an evidence graph."""

    node_id: str
    message: StreamMessage


@dataclass(frozen=True, slots=True)
class EvidenceEdge:
    """Auditable relationship with measured, non-causal facts."""

    edge_id: str
    from_node_id: str
    to_node_id: str
    relation: Literal["spatiotemporal_cooccurrence"]
    spatial_relation: SpatialRelation
    distance_km: float
    time_delta_minutes: float
    from_geometry_basis: GeometryBasis
    to_geometry_basis: GeometryBasis
    rule_version: str = CORRELATION_RULE_VERSION


@dataclass(frozen=True, slots=True)
class IncidentCandidate:
    """One connected component of cross-source evidence, not a verified incident."""

    incident_id: str
    title: str
    started_at: datetime
    latest_signal_at: datetime
    center_latitude: float | None
    center_longitude: float | None
    sources: tuple[str, ...]
    nodes: tuple[EvidenceNode, ...]
    edges: tuple[EvidenceEdge, ...]
    max_distance_km: float
    time_span_minutes: float
    rule_version: str = CORRELATION_RULE_VERSION
    caveat: str = CORRELATION_CAVEAT


@dataclass(frozen=True, slots=True)
class CorrelationResult:
    """Sorted incident-candidate response before HTTP serialization."""

    incidents: tuple[IncidentCandidate, ...]
    total_incidents: int
    incidents_truncated: bool
    candidate_edges_truncated: bool


def _content_id(prefix: str, values: tuple[str, ...]) -> str:
    encoded = (CORRELATION_RULE_VERSION + "\n" + "\n".join(values)).encode()
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:20]}"


def _edge_from_pair(pair: CorrelationPair) -> EvidenceEdge:
    endpoints = [
        (evidence_node_id(pair.left), pair.left_geometry_basis),
        (evidence_node_id(pair.right), pair.right_geometry_basis),
    ]
    endpoints.sort(key=lambda value: value[0])
    from_endpoint, to_endpoint = endpoints
    return EvidenceEdge(
        edge_id=_content_id("edge", (from_endpoint[0], to_endpoint[0])),
        from_node_id=from_endpoint[0],
        to_node_id=to_endpoint[0],
        relation=CORRELATION_RELATION,
        spatial_relation="intersects" if pair.distance_km <= 0.001 else "within_radius",
        distance_km=round(pair.distance_km, 3),
        time_delta_minutes=round(pair.time_delta_minutes, 3),
        from_geometry_basis=from_endpoint[1],
        to_geometry_basis=to_endpoint[1],
    )


def _center(nodes: tuple[EvidenceNode, ...]) -> tuple[float | None, float | None]:
    """Calculate an antimeridian-safe spherical mean of source focus points."""
    locations = [node.message.event.location for node in nodes if node.message.event.location]
    if not locations:
        return None, None
    vectors = [
        (
            math.cos(math.radians(location.latitude)) * math.cos(math.radians(location.longitude)),
            math.cos(math.radians(location.latitude)) * math.sin(math.radians(location.longitude)),
            math.sin(math.radians(location.latitude)),
        )
        for location in locations
    ]
    x = sum(vector[0] for vector in vectors) / len(vectors)
    y = sum(vector[1] for vector in vectors) / len(vectors)
    z = sum(vector[2] for vector in vectors) / len(vectors)
    horizontal = math.hypot(x, y)
    if horizontal < 1e-12 and abs(z) < 1e-12:
        anchor = locations[0]
        return anchor.latitude, anchor.longitude
    latitude = math.degrees(math.atan2(z, horizontal))
    longitude = math.degrees(math.atan2(y, x))
    return round(latitude, 6), round(longitude, 6)


def _place_for(nodes: tuple[EvidenceNode, ...]) -> str:
    for node in sorted(
        nodes,
        key=lambda item: (item.message.event.occurred_at, item.node_id),
    ):
        place = node.message.event.payload.get("place")
        if isinstance(place, str) and place.strip():
            return place.strip()
    return "mapped area"


def build_incident_candidates(
    batch: CorrelationBatch,
    *,
    limit: int,
) -> CorrelationResult:
    """Build deterministic connected components from bounded candidate pairs."""
    if not 1 <= limit <= 100:
        raise ValueError("incident limit must be between 1 and 100")

    parents: dict[str, str] = {}
    messages: dict[str, StreamMessage] = {}
    edges: dict[str, EvidenceEdge] = {}

    def find(node_id: str) -> str:
        parent = parents[node_id]
        while parent != parents[parent]:
            parents[parent] = parents[parents[parent]]
            parent = parents[parent]
        parents[node_id] = parent
        return parent

    def union(left_id: str, right_id: str) -> None:
        left_root = find(left_id)
        right_root = find(right_id)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        parents[second] = first

    for pair in batch.pairs:
        edge = _edge_from_pair(pair)
        for node_id, message in (
            (evidence_node_id(pair.left), pair.left),
            (evidence_node_id(pair.right), pair.right),
        ):
            existing = messages.get(node_id)
            if existing is not None and existing.stream_id != message.stream_id:
                raise ValueError("one evidence node resolved to multiple current revisions")
            messages[node_id] = message
            parents.setdefault(node_id, node_id)
        existing_edge = edges.get(edge.edge_id)
        if existing_edge is not None and existing_edge != edge:
            raise ValueError("one evidence edge resolved to conflicting measurements")
        edges[edge.edge_id] = edge
        union(edge.from_node_id, edge.to_node_id)

    components: dict[str, list[str]] = {}
    for node_id in sorted(messages):
        components.setdefault(find(node_id), []).append(node_id)

    incidents: list[IncidentCandidate] = []
    for node_ids in components.values():
        ordered_ids = tuple(sorted(node_ids))
        nodes = tuple(
            EvidenceNode(node_id=node_id, message=messages[node_id]) for node_id in ordered_ids
        )
        component_edges = tuple(
            sorted(
                (
                    edge
                    for edge in edges.values()
                    if edge.from_node_id in ordered_ids and edge.to_node_id in ordered_ids
                ),
                key=lambda edge: edge.edge_id,
            )
        )
        times = tuple(node.message.event.occurred_at for node in nodes)
        started_at = min(times)
        latest_signal_at = max(times)
        latitude, longitude = _center(nodes)
        sources = tuple(sorted({node.message.event.source for node in nodes}))
        incidents.append(
            IncidentCandidate(
                incident_id=_content_id("incident", ordered_ids),
                title=f"{len(sources)}-source signal cluster near {_place_for(nodes)}",
                started_at=started_at,
                latest_signal_at=latest_signal_at,
                center_latitude=latitude,
                center_longitude=longitude,
                sources=sources,
                nodes=nodes,
                edges=component_edges,
                max_distance_km=max(edge.distance_km for edge in component_edges),
                time_span_minutes=round(
                    (latest_signal_at - started_at).total_seconds() / 60,
                    3,
                ),
            )
        )

    incidents.sort(
        key=lambda incident: (incident.latest_signal_at, incident.incident_id), reverse=True
    )
    total_incidents = len(incidents)
    return CorrelationResult(
        incidents=tuple(incidents[:limit]),
        total_incidents=total_incidents,
        incidents_truncated=total_incidents > limit,
        candidate_edges_truncated=batch.truncated,
    )

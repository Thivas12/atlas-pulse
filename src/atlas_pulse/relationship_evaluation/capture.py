"""Capture deterministic, blinded relationship cases from the deployed API."""

from __future__ import annotations

import hashlib
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import cast
from urllib.parse import urlsplit, urlunsplit

import httpx

from atlas_pulse.api import (
    EvidenceClaimResponse,
    EvidenceEdgeResponse,
    EvidenceNodeResponse,
    EvidenceRelationshipResponse,
    IncidentsResponse,
)
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.projections.base import SourceName
from atlas_pulse.relationship_evaluation.base import (
    CapturedClaim,
    EventEvidence,
    RelationshipBenchmarkDefinition,
    RelationshipCase,
    RelationshipPool,
    SystemPrediction,
    relationship_case_id,
)
from atlas_pulse.relationships import ClaimPredicate
from atlas_pulse.retrieval.document import document_hash, render_event_document


@dataclass(frozen=True, slots=True)
class _EdgeBundle:
    edge: EvidenceEdgeResponse
    left: EventEvidence
    right: EventEvidence
    claims: tuple[EvidenceClaimResponse, ...]
    relationships: tuple[EvidenceRelationshipResponse, ...]

    @property
    def source_pair_key(self) -> str:
        return "+".join(sorted((self.left.source, self.right.source)))


def _safe_endpoint(value: str) -> str:
    parsed = urlsplit(value.strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an absolute HTTP(S) endpoint")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base URL must not contain embedded credentials")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _expected_parameters(definition: RelationshipBenchmarkDefinition) -> dict[str, object]:
    parameters = definition.parameters
    return {
        "radius_km": parameters.radius_km,
        "time_window_minutes": parameters.time_window_minutes,
        "lookback_hours": parameters.lookback_hours,
        "candidate_edge_limit": parameters.candidate_edge_limit,
        "incident_limit": parameters.incident_limit,
        "active_only": parameters.active_only,
        "bbox": parameters.bbox,
    }


def _event_evidence(node: EvidenceNodeResponse) -> EventEvidence:
    text = render_event_document(node.event)
    return EventEvidence(
        node_id=node.node_id,
        source=cast(SourceName, node.event.source),
        event_id=node.event.event_id,
        event_type=node.event.event_type,
        occurred_at=node.event.occurred_at,
        document_text=text,
        document_hash=document_hash(text),
    )


def _captured_claim(claim: EvidenceClaimResponse) -> CapturedClaim:
    return CapturedClaim(
        claim_id=claim.claim_id,
        node_id=claim.node_id,
        predicate=claim.predicate,
        value=claim.value,
        scope=claim.scope,
        scope_value=claim.scope_value,
        evidence_field=claim.evidence_field,
        evidence_excerpt=claim.evidence_excerpt,
        qualifier=claim.qualifier,
    )


def _prediction(bundle: _EdgeBundle, predicate: ClaimPredicate) -> SystemPrediction:
    relevant_claims = tuple(
        sorted(
            (
                _captured_claim(claim)
                for claim in bundle.claims
                if claim.predicate == predicate
                and claim.node_id in {bundle.left.node_id, bundle.right.node_id}
            ),
            key=lambda claim: claim.claim_id,
        )
    )
    decisive = tuple(
        relationship
        for relationship in bundle.relationships
        if relationship.edge_id == bundle.edge.edge_id
        and relationship.predicate == predicate
        and relationship.label != "insufficient_evidence"
    )
    decisive_labels = {relationship.label for relationship in decisive}
    if len(decisive_labels) > 1:
        raise RuntimeError(
            f"edge {bundle.edge.edge_id} emitted conflicting decisions for {predicate}"
        )

    if decisive:
        label = next(iter(decisive_labels))
        supporting = decisive
    else:
        label = "insufficient_evidence"
        supporting = tuple(
            relationship
            for relationship in bundle.relationships
            if relationship.edge_id == bundle.edge.edge_id
            and relationship.label == "insufficient_evidence"
        )

    return SystemPrediction(
        label=label,
        relationship_ids=tuple(
            sorted({relationship.relationship_id for relationship in supporting})
        ),
        claims=relevant_claims,
        bases=tuple(sorted({relationship.basis for relationship in supporting})),
        rationales=tuple(sorted({relationship.rationale for relationship in supporting})),
    )


def _validate_response(
    response: IncidentsResponse,
    definition: RelationshipBenchmarkDefinition,
) -> tuple[_EdgeBundle, ...]:
    if response.parameters.model_dump(mode="python") != _expected_parameters(definition):
        raise RuntimeError("incident API did not echo the exact requested correlation parameters")
    if response.rule_version != definition.expected_correlation_rule_version:
        raise RuntimeError("incident API correlation rule does not match the benchmark definition")
    if response.relationship_rule_version != definition.expected_relationship_rule_version:
        raise RuntimeError("incident API relationship rule does not match the benchmark definition")
    if response.count != len(response.items) or response.total_incidents < response.count:
        raise RuntimeError("incident API returned inconsistent result counts")

    bundles: dict[str, _EdgeBundle] = {}
    for incident in response.items:
        analysis = incident.relationship_analysis
        if incident.node_count != len(incident.nodes) or incident.edge_count != len(incident.edges):
            raise RuntimeError(
                f"incident {incident.incident_id} returned inconsistent graph counts"
            )
        if analysis.analyzed_edge_count != incident.edge_count:
            raise RuntimeError(
                f"incident {incident.incident_id} returned inconsistent relationship counts"
            )
        if analysis.rule_version != response.relationship_rule_version:
            raise RuntimeError("incident relationship analysis changed rule version within capture")
        if any(
            claim.rule_version != response.relationship_rule_version for claim in analysis.claims
        ) or any(
            relationship.rule_version != response.relationship_rule_version
            for relationship in analysis.relationships
        ):
            raise RuntimeError("claim or relationship rule version changed within capture")

        nodes = {node.node_id: node for node in incident.nodes}
        if len(nodes) != len(incident.nodes):
            raise RuntimeError(f"incident {incident.incident_id} returned duplicate node IDs")
        edges_by_id = {edge.edge_id: edge for edge in incident.edges}
        if len(edges_by_id) != len(incident.edges):
            raise RuntimeError(f"incident {incident.incident_id} returned duplicate edge IDs")
        claims_by_id = {claim.claim_id: claim for claim in analysis.claims}
        if len(claims_by_id) != len(analysis.claims):
            raise RuntimeError("relationship analysis returned duplicate claim IDs")
        relationship_ids = {relationship.relationship_id for relationship in analysis.relationships}
        if len(relationship_ids) != len(analysis.relationships):
            raise RuntimeError("relationship analysis returned duplicate relationship IDs")
        known_edges = set(edges_by_id)
        if any(relationship.edge_id not in known_edges for relationship in analysis.relationships):
            raise RuntimeError("relationship analysis references an edge outside its incident")
        if any(claim.node_id not in nodes for claim in analysis.claims):
            raise RuntimeError("relationship analysis references a node outside its incident")
        for relationship in analysis.relationships:
            edge = edges_by_id[relationship.edge_id]
            if (
                relationship.from_node_id != edge.from_node_id
                or relationship.to_node_id != edge.to_node_id
            ):
                raise RuntimeError("relationship endpoints do not match the measured parent edge")
            if relationship.label == "insufficient_evidence":
                if (
                    relationship.predicate is not None
                    or relationship.from_claim_id is not None
                    or relationship.to_claim_id is not None
                ):
                    raise RuntimeError("abstaining relationship unexpectedly references claims")
                continue
            if (
                relationship.predicate is None
                or relationship.from_claim_id is None
                or relationship.to_claim_id is None
            ):
                raise RuntimeError("decisive relationship is missing claim references")
            try:
                from_claim = claims_by_id[relationship.from_claim_id]
                to_claim = claims_by_id[relationship.to_claim_id]
            except KeyError as error:
                raise RuntimeError("relationship references an unknown claim") from error
            if (
                from_claim.node_id != edge.from_node_id
                or to_claim.node_id != edge.to_node_id
                or from_claim.predicate != relationship.predicate
                or to_claim.predicate != relationship.predicate
            ):
                raise RuntimeError(
                    "relationship claim references do not match its edge and predicate"
                )

        for edge in incident.edges:
            if edge.rule_version != response.rule_version:
                raise RuntimeError("evidence edge changed correlation rule within capture")
            if edge.from_node_id >= edge.to_node_id:
                raise RuntimeError("evidence edge endpoints are not in canonical node order")
            try:
                left_node = nodes[edge.from_node_id]
                right_node = nodes[edge.to_node_id]
            except KeyError as error:
                raise RuntimeError("evidence edge references an unknown node") from error
            if edge.edge_id in bundles:
                raise RuntimeError(f"duplicate evidence edge across incidents: {edge.edge_id}")
            bundles[edge.edge_id] = _EdgeBundle(
                edge=edge,
                left=_event_evidence(left_node),
                right=_event_evidence(right_node),
                claims=analysis.claims,
                relationships=analysis.relationships,
            )

    if not bundles:
        raise RuntimeError("incident API returned no measured cross-source edges to review")
    return tuple(bundles.values())


def _edge_sample_key(
    bundle: _EdgeBundle,
    *,
    definition_hash: str,
    source_pair: str,
) -> str:
    value = (
        f"{definition_hash}:{source_pair}:{bundle.edge.edge_id}:"
        f"{bundle.left.document_hash}:{bundle.right.document_hash}"
    )
    return hashlib.sha256(value.encode()).hexdigest()


def _sample_edges(
    bundles: tuple[_EdgeBundle, ...],
    *,
    definition_hash: str,
    per_source_pair: int,
) -> tuple[_EdgeBundle, ...]:
    grouped: dict[str, list[_EdgeBundle]] = defaultdict(list)
    for bundle in bundles:
        grouped[bundle.source_pair_key].append(bundle)

    selected: list[_EdgeBundle] = []
    for source_pair in sorted(grouped):
        candidates = grouped[source_pair]
        selected.extend(
            sorted(
                candidates,
                key=partial(
                    _edge_sample_key,
                    definition_hash=definition_hash,
                    source_pair=source_pair,
                ),
            )[:per_source_pair]
        )
    return tuple(selected)


def _relationship_case(bundle: _EdgeBundle, predicate: ClaimPredicate) -> RelationshipCase:
    source_pair = cast(
        tuple[SourceName, SourceName],
        tuple(sorted((bundle.left.source, bundle.right.source))),
    )
    case_id = relationship_case_id(
        edge_id=bundle.edge.edge_id,
        predicate=predicate,
        left=bundle.left,
        right=bundle.right,
        distance_km=bundle.edge.distance_km,
        time_delta_minutes=bundle.edge.time_delta_minutes,
        left_geometry_basis=bundle.edge.from_geometry_basis,
        right_geometry_basis=bundle.edge.to_geometry_basis,
    )
    return RelationshipCase(
        case_id=case_id,
        edge_id=bundle.edge.edge_id,
        predicate=predicate,
        source_pair=source_pair,
        left=bundle.left,
        right=bundle.right,
        distance_km=bundle.edge.distance_km,
        time_delta_minutes=bundle.edge.time_delta_minutes,
        left_geometry_basis=bundle.edge.from_geometry_basis,
        right_geometry_basis=bundle.edge.to_geometry_basis,
        system_prediction=_prediction(bundle, predicate),
    )


async def capture_relationship_pool(
    definition: RelationshipBenchmarkDefinition,
    *,
    base_url: str,
    client: httpx.AsyncClient | None = None,
) -> RelationshipPool:
    """Capture one bounded live graph and create a prediction-blind review pool."""
    endpoint = _safe_endpoint(base_url)
    definition_hash = canonical_sha256(definition)
    captured_at = datetime.now(UTC)
    owns_client = client is None
    active_client = client or httpx.AsyncClient(base_url=endpoint, timeout=60.0)
    started = time.perf_counter()
    try:
        raw_response = await active_client.get(
            "/v1/incidents",
            params=definition.parameters.api_parameters(),
        )
        latency_ms = (time.perf_counter() - started) * 1_000
        raw_response.raise_for_status()
        response = IncidentsResponse.model_validate(raw_response.json())
    finally:
        if owns_client:
            await active_client.aclose()

    bundles = _validate_response(response, definition)
    selected = _sample_edges(
        bundles,
        definition_hash=definition_hash,
        per_source_pair=definition.max_edges_per_source_pair,
    )
    cases = tuple(
        _relationship_case(bundle, predicate)
        for bundle in selected
        for predicate in definition.predicates
    )

    def blind_key(case: RelationshipCase) -> str:
        return hashlib.sha256(f"{definition_hash}:{case.case_id}".encode()).hexdigest()

    available_counts = Counter(bundle.source_pair_key for bundle in bundles)
    sampled_counts = Counter(bundle.source_pair_key for bundle in selected)
    timestamp = captured_at.strftime("%Y%m%dt%H%M%Sz").casefold()
    return RelationshipPool(
        pool_id=f"{definition.benchmark_id}-{timestamp}",
        benchmark_id=definition.benchmark_id,
        benchmark_sha256=definition_hash,
        rubric_version=definition.rubric_version,
        captured_at=captured_at,
        endpoint=endpoint,
        capture_latency_ms=round(latency_ms, 3),
        parameters=definition.parameters,
        predicates=definition.predicates,
        max_edges_per_source_pair=definition.max_edges_per_source_pair,
        correlation_rule_version=response.rule_version,
        relationship_rule_version=response.relationship_rule_version,
        incident_count=response.count,
        total_incidents=response.total_incidents,
        available_edge_count=len(bundles),
        sampled_edge_count=len(selected),
        available_edges_by_source_pair=dict(sorted(available_counts.items())),
        sampled_edges_by_source_pair=dict(sorted(sampled_counts.items())),
        incidents_truncated=response.incidents_truncated,
        candidate_edges_truncated=response.candidate_edges_truncated,
        cases=tuple(sorted(cases, key=blind_key)),
    )

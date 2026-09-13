"""Versioned claim relationships layered over measured evidence-graph edges."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from atlas_pulse.correlation import EvidenceNode, IncidentCandidate

RELATIONSHIP_RULE_VERSION = "structured-claims-v1"
RELATIONSHIP_CAVEAT = (
    "Annotations compare normalized source claims attached to measured edges. Corroboration is "
    "agreement at the named predicate and scope, not proof of truth or a shared incident; "
    "contradiction is a review flag, not adjudication. Insufficient evidence is not disagreement."
)

ClaimPredicate = Literal["hazard_domain", "evacuation_state", "road_access_state"]
ClaimScope = Literal["measured_edge_area", "named_place"]
RelationshipLabel = Literal["corroborates", "contradicts", "insufficient_evidence"]
RelationshipBasis = Literal[
    "exact_normalized_agreement",
    "mutually_exclusive_structured_values",
    "no_decisive_comparison",
]

_SPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w]+", flags=re.UNICODE)
_TEXT_FIELDS = ("title", "headline", "description", "instruction")

_NWS_TERM_DOMAINS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tsunami_related", ("tsunami",)),
    ("seismic", ("earthquake", "seismic")),
    ("volcanic", ("volcano", "volcanic", "ashfall")),
    ("fire_related", ("wildfire", "fire weather", "red flag", "fire warning")),
    (
        "hydrological",
        ("flash flood", "flood", "storm surge", "high water", "debris flow"),
    ),
    (
        "meteorological",
        (
            "tornado",
            "hurricane",
            "typhoon",
            "tropical storm",
            "thunderstorm",
            "blizzard",
            "winter storm",
            "snow",
            "ice storm",
            "wind",
            "heat",
            "cold",
            "fog",
            "dust",
        ),
    ),
)
_NWS_CATEGORY_DOMAINS = {
    "Met": "meteorological",
    "Geo": "geophysical",
    "Safety": "public_safety",
    "Security": "security",
    "Rescue": "rescue",
    "Fire": "fire_related",
    "Health": "health",
    "Env": "environmental",
    "Transport": "transport",
    "Infra": "infrastructure",
    "CBRNE": "hazardous_material",
}

_OPERATIONAL_PATTERNS: tuple[tuple[ClaimPredicate, str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "evacuation_state",
        "lifted",
        (
            re.compile(
                r"\bevacuation orders? (?:have been |has been |were )?(?:lifted|rescinded|cancelled)\b"
            ),
            re.compile(r"\bevacuation (?:is |are )?no longer (?:required|necessary)\b"),
        ),
    ),
    (
        "evacuation_state",
        "active",
        (
            re.compile(
                r"\bevacuation orders? (?:is |are |remain |remains )?(?:in effect|active|issued)\b"
            ),
            re.compile(r"\bordered to evacuate\b"),
            re.compile(r"\bevacuate (?:now|immediately)\b"),
        ),
    ),
    (
        "road_access_state",
        "open",
        (
            re.compile(r"\broad(?:way)?s? (?:has |have )?(?:reopened|re-opened)\b"),
            re.compile(r"\broad(?:way)?s? (?:is |are )?open again\b"),
        ),
    ),
    (
        "road_access_state",
        "closed",
        (
            re.compile(r"\broad(?:way)?s? (?:is |are |remain |remains )?closed\b"),
            re.compile(r"\broad closure\b"),
        ),
    ),
)
_OPPOSITES = {
    "evacuation_state": frozenset({"active", "lifted"}),
    "road_access_state": frozenset({"closed", "open"}),
}


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    """One deterministic claim with exact provenance back to a graph node field."""

    claim_id: str
    node_id: str
    predicate: ClaimPredicate
    value: str
    scope: ClaimScope
    scope_value: str | None
    evidence_field: str
    evidence_excerpt: str
    qualifier: str | None = None
    rule_version: str = RELATIONSHIP_RULE_VERSION


@dataclass(frozen=True, slots=True)
class EvidenceRelationship:
    """One reviewable comparison that never mutates its measured parent edge."""

    relationship_id: str
    edge_id: str
    from_node_id: str
    to_node_id: str
    label: RelationshipLabel
    predicate: ClaimPredicate | None
    normalized_value: str | None
    from_claim_id: str | None
    to_claim_id: str | None
    basis: RelationshipBasis
    rationale: str
    rule_version: str = RELATIONSHIP_RULE_VERSION


@dataclass(frozen=True, slots=True)
class RelationshipAnalysis:
    """Versioned semantic annotations for one deterministic incident candidate."""

    claims: tuple[EvidenceClaim, ...]
    relationships: tuple[EvidenceRelationship, ...]
    analyzed_edge_count: int
    corroboration_count: int
    contradiction_count: int
    insufficient_evidence_count: int
    rule_version: str = RELATIONSHIP_RULE_VERSION
    caveat: str = RELATIONSHIP_CAVEAT


def _content_id(prefix: str, *values: str) -> str:
    encoded = (RELATIONSHIP_RULE_VERSION + "\n" + "\n".join(values)).encode()
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:20]}"


def _clean_excerpt(value: str) -> str:
    return _SPACE.sub(" ", value).strip()[:280]


def _normalized_place(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    display = _clean_excerpt(value)
    normalized = _SPACE.sub(" ", _NON_WORD.sub(" ", display.casefold())).strip()
    if not normalized:
        return None
    return normalized, display


def _make_claim(
    node: EvidenceNode,
    *,
    predicate: ClaimPredicate,
    value: str,
    scope: ClaimScope,
    scope_value: str | None,
    evidence_field: str,
    evidence_excerpt: str,
    qualifier: str | None = None,
) -> EvidenceClaim:
    excerpt = _clean_excerpt(evidence_excerpt)
    claim_id = _content_id(
        "claim",
        node.node_id,
        predicate,
        value,
        scope,
        scope_value or "",
        evidence_field,
        excerpt,
    )
    return EvidenceClaim(
        claim_id=claim_id,
        node_id=node.node_id,
        predicate=predicate,
        value=value,
        scope=scope,
        scope_value=scope_value,
        evidence_field=evidence_field,
        evidence_excerpt=excerpt,
        qualifier=qualifier,
    )


def _hazard_claims(node: EvidenceNode) -> list[EvidenceClaim]:
    event = node.message.event
    payload = event.payload
    claims: list[EvidenceClaim] = []

    if event.source == "usgs" and event.event_type.startswith("seismic."):
        claims.append(
            _make_claim(
                node,
                predicate="hazard_domain",
                value="seismic",
                scope="measured_edge_area",
                scope_value=None,
                evidence_field="event_type",
                evidence_excerpt=event.event_type,
            )
        )
        if payload.get("tsunami") is True:
            claims.append(
                _make_claim(
                    node,
                    predicate="hazard_domain",
                    value="tsunami_related",
                    scope="measured_edge_area",
                    scope_value=None,
                    evidence_field="payload.tsunami",
                    evidence_excerpt="true",
                    qualifier="USGS tsunami flag indicates tsunami relevance, not observed impact.",
                )
            )
    elif event.source == "firms" and event.event_type == "fire.thermal_anomaly":
        claims.append(
            _make_claim(
                node,
                predicate="hazard_domain",
                value="fire_related",
                scope="measured_edge_area",
                scope_value=None,
                evidence_field="event_type",
                evidence_excerpt=event.event_type,
                qualifier="A FIRMS thermal anomaly is not independently proof of wildfire.",
            )
        )
    elif event.source == "gdelt" and event.event_type == "geopolitical.gdelt_event":
        category = payload.get("category")
        excerpt = category if isinstance(category, str) and category.strip() else event.event_type
        claims.append(
            _make_claim(
                node,
                predicate="hazard_domain",
                value="material_conflict",
                scope="measured_edge_area",
                scope_value=None,
                evidence_field="payload.category" if excerpt != event.event_type else "event_type",
                evidence_excerpt=excerpt,
                qualifier="GDELT is a machine-coded media observation, not independent verification.",
            )
        )
    elif event.source == "nws" and event.event_type == "weather.alert":
        alert_type = payload.get("alert_type")
        category = payload.get("category")
        alert_text = alert_type.strip() if isinstance(alert_type, str) else ""
        normalized_alert = alert_text.casefold()
        domain = next(
            (
                candidate
                for candidate, terms in _NWS_TERM_DOMAINS
                if any(term in normalized_alert for term in terms)
            ),
            None,
        )
        evidence_field = "payload.alert_type"
        evidence_excerpt = alert_text
        if domain is None and isinstance(category, str):
            domain = _NWS_CATEGORY_DOMAINS.get(category)
            evidence_field = "payload.category"
            evidence_excerpt = category
        if domain is not None:
            claims.append(
                _make_claim(
                    node,
                    predicate="hazard_domain",
                    value=domain,
                    scope="measured_edge_area",
                    scope_value=None,
                    evidence_field=evidence_field,
                    evidence_excerpt=evidence_excerpt,
                )
            )
    return claims


def _operational_claims(node: EvidenceNode) -> list[EvidenceClaim]:
    payload = node.message.event.payload
    place = _normalized_place(payload.get("place"))
    if place is None:
        return []
    normalized_place, display_place = place
    matches: dict[ClaimPredicate, list[tuple[str, str, str]]] = defaultdict(list)
    for field in _TEXT_FIELDS:
        raw = payload.get(field)
        if not isinstance(raw, str) or not raw.strip():
            continue
        normalized_text = _clean_excerpt(raw).casefold()
        for predicate, value, patterns in _OPERATIONAL_PATTERNS:
            if any(pattern.search(normalized_text) for pattern in patterns):
                matches[predicate].append((value, f"payload.{field}", raw))

    claims: list[EvidenceClaim] = []
    for predicate, evidence in sorted(matches.items()):
        values = {value for value, _, _ in evidence}
        if len(values) != 1:
            continue
        value, field, excerpt = evidence[0]
        claims.append(
            _make_claim(
                node,
                predicate=predicate,
                value=value,
                scope="named_place",
                scope_value=display_place,
                evidence_field=field,
                evidence_excerpt=excerpt,
                qualifier=f"Compared only with claims using exact normalized place '{normalized_place}'.",
            )
        )
    return claims


def extract_claims(node: EvidenceNode) -> tuple[EvidenceClaim, ...]:
    """Extract conservative, source-backed claims from one evidence node."""
    claims = _hazard_claims(node) + _operational_claims(node)
    return tuple(sorted(claims, key=lambda claim: claim.claim_id))


def _scope_key(claim: EvidenceClaim) -> tuple[ClaimPredicate, ClaimScope, str]:
    normalized_scope = ""
    if claim.scope_value is not None:
        normalized = _normalized_place(claim.scope_value)
        normalized_scope = normalized[0] if normalized is not None else claim.claim_id
    return claim.predicate, claim.scope, normalized_scope


def _related(
    *,
    edge_id: str,
    from_node_id: str,
    to_node_id: str,
    left: EvidenceClaim,
    right: EvidenceClaim,
) -> EvidenceRelationship | None:
    if _scope_key(left) != _scope_key(right):
        return None
    if left.value == right.value:
        label: RelationshipLabel = "corroborates"
        basis: RelationshipBasis = "exact_normalized_agreement"
        normalized_value = left.value
        rationale = (
            f"Independent source claims agree on {left.predicate}={left.value} at "
            f"{left.scope.replace('_', ' ')} scope."
        )
    elif (opposites := _OPPOSITES.get(left.predicate)) is not None and frozenset(
        (left.value, right.value)
    ) == opposites:
        label = "contradicts"
        basis = "mutually_exclusive_structured_values"
        normalized_value = None
        rationale = (
            f"Independent source claims make mutually exclusive {left.predicate} assertions "
            f"at {left.scope.replace('_', ' ')} scope: {left.value} versus {right.value}."
        )
    else:
        return None

    ordered_claims = sorted((left, right), key=lambda claim: claim.node_id)
    from_claim, to_claim = ordered_claims
    return EvidenceRelationship(
        relationship_id=_content_id(
            "relationship",
            edge_id,
            label,
            from_claim.claim_id,
            to_claim.claim_id,
        ),
        edge_id=edge_id,
        from_node_id=from_node_id,
        to_node_id=to_node_id,
        label=label,
        predicate=left.predicate,
        normalized_value=normalized_value,
        from_claim_id=from_claim.claim_id,
        to_claim_id=to_claim.claim_id,
        basis=basis,
        rationale=rationale,
    )


def analyze_incident(incident: IncidentCandidate) -> RelationshipAnalysis:
    """Annotate measured edges using only deterministic, provenance-carrying claims."""
    claims_by_node = {node.node_id: extract_claims(node) for node in incident.nodes}
    claims = tuple(
        sorted(
            (claim for node_claims in claims_by_node.values() for claim in node_claims),
            key=lambda claim: claim.claim_id,
        )
    )
    relationships: list[EvidenceRelationship] = []

    for edge in incident.edges:
        edge_relationships: dict[str, EvidenceRelationship] = {}
        for left in claims_by_node.get(edge.from_node_id, ()):
            for right in claims_by_node.get(edge.to_node_id, ()):
                relationship = _related(
                    edge_id=edge.edge_id,
                    from_node_id=edge.from_node_id,
                    to_node_id=edge.to_node_id,
                    left=left,
                    right=right,
                )
                if relationship is not None:
                    edge_relationships[relationship.relationship_id] = relationship
        if not edge_relationships:
            unresolved = EvidenceRelationship(
                relationship_id=_content_id("relationship", edge.edge_id, "insufficient_evidence"),
                edge_id=edge.edge_id,
                from_node_id=edge.from_node_id,
                to_node_id=edge.to_node_id,
                label="insufficient_evidence",
                predicate=None,
                normalized_value=None,
                from_claim_id=None,
                to_claim_id=None,
                basis="no_decisive_comparison",
                rationale=(
                    "No exact normalized agreement or allowed mutually exclusive claim pair was "
                    "found; measured co-occurrence alone cannot establish semantic agreement or "
                    "conflict."
                ),
            )
            edge_relationships[unresolved.relationship_id] = unresolved
        relationships.extend(edge_relationships.values())

    ordered = tuple(sorted(relationships, key=lambda item: item.relationship_id))
    return RelationshipAnalysis(
        claims=claims,
        relationships=ordered,
        analyzed_edge_count=len(incident.edges),
        corroboration_count=sum(item.label == "corroborates" for item in ordered),
        contradiction_count=sum(item.label == "contradicts" for item in ordered),
        insufficient_evidence_count=sum(item.label == "insufficient_evidence" for item in ordered),
    )

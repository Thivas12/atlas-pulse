"""Golden tests for the versioned evidence-claim relationship layer."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast

from agent_rag_core import Event, GeoPoint

from atlas_pulse.correlation import (
    CorrelationBatch,
    CorrelationPair,
    IncidentCandidate,
    build_incident_candidates,
)
from atlas_pulse.relationships import (
    RELATIONSHIP_CAVEAT,
    RELATIONSHIP_RULE_VERSION,
    analyze_incident,
)
from atlas_pulse.streams import StreamMessage


class FixtureEvent(TypedDict):
    source: str
    event_id: str
    event_type: str
    payload: dict[str, object]


class FixtureExpectation(TypedDict):
    label: str
    predicate: str | None
    normalized_value: str | None


class FixtureCase(TypedDict):
    case_id: str
    left: FixtureEvent
    right: FixtureEvent
    expected: list[FixtureExpectation]


class RelationshipFixture(TypedDict):
    schema_version: str
    dataset_id: str
    rule_version: str
    description: str
    cases: list[FixtureCase]


def message(
    source: str,
    event_id: str,
    *,
    event_type: str,
    payload: dict[str, object],
    stream_id: str,
) -> StreamMessage:
    occurred = datetime(2026, 9, 13, 6, tzinfo=UTC)
    return StreamMessage(
        stream_id=stream_id,
        event=Event(
            event_id=event_id,
            event_type=event_type,
            source=source,
            occurred_at=occurred,
            ingested_at=occurred,
            location=GeoPoint(latitude=34, longitude=-118, altitude_km=None),
            payload=payload,
        ),
    )


def incident(left: StreamMessage, right: StreamMessage) -> IncidentCandidate:
    pair = CorrelationPair(
        left=left,
        right=right,
        distance_km=4.25,
        time_delta_minutes=5,
        left_geometry_basis="point",
        right_geometry_basis="polygon",
    )
    return build_incident_candidates(CorrelationBatch((pair,)), limit=10).incidents[0]


def test_checked_in_relationship_contract_cases_match_production_rules() -> None:
    path = Path(__file__).parents[1] / "evals/relationships/structured-claims-v1.json"
    fixture = cast(RelationshipFixture, json.loads(path.read_text(encoding="utf-8")))
    assert fixture["schema_version"] == "1.0.0"
    assert fixture["rule_version"] == RELATIONSHIP_RULE_VERSION
    assert fixture["cases"]

    for index, case in enumerate(fixture["cases"], start=1):
        left = case["left"]
        right = case["right"]
        analysis = analyze_incident(
            incident(
                message(
                    left["source"],
                    left["event_id"],
                    event_type=left["event_type"],
                    payload=left["payload"],
                    stream_id=f"{index * 2 - 1}-0",
                ),
                message(
                    right["source"],
                    right["event_id"],
                    event_type=right["event_type"],
                    payload=right["payload"],
                    stream_id=f"{index * 2}-0",
                ),
            )
        )
        actual = {
            (relationship.label, relationship.predicate, relationship.normalized_value)
            for relationship in analysis.relationships
        }
        expected = {
            (item["label"], item["predicate"], item["normalized_value"])
            for item in case["expected"]
        }
        assert actual == expected, case["case_id"]


def test_same_normalized_fire_domain_is_narrow_corroboration() -> None:
    firms = message(
        "firms",
        "thermal-1",
        event_type="fire.thermal_anomaly",
        payload={"place": "Test County", "title": "High-confidence thermal anomaly"},
        stream_id="1-0",
    )
    nws = message(
        "nws",
        "fire-warning-1",
        event_type="weather.alert",
        payload={"place": "Test County", "alert_type": "Fire Warning", "category": "Fire"},
        stream_id="2-0",
    )

    analysis = analyze_incident(incident(firms, nws))

    assert analysis.rule_version == RELATIONSHIP_RULE_VERSION
    assert analysis.caveat == RELATIONSHIP_CAVEAT
    assert analysis.analyzed_edge_count == 1
    assert analysis.corroboration_count == 1
    assert analysis.contradiction_count == 0
    assert analysis.insufficient_evidence_count == 0
    relationship = analysis.relationships[0]
    assert relationship.label == "corroborates"
    assert relationship.predicate == "hazard_domain"
    assert relationship.normalized_value == "fire_related"
    assert relationship.basis == "exact_normalized_agreement"
    assert relationship.from_claim_id is not None
    assert relationship.to_claim_id is not None
    assert {claim.evidence_field for claim in analysis.claims} == {
        "event_type",
        "payload.alert_type",
    }
    assert any(
        claim.qualifier and "not independently proof" in claim.qualifier
        for claim in analysis.claims
    )


def test_different_hazard_domains_remain_insufficient_not_contradictory() -> None:
    firms = message(
        "firms",
        "thermal-1",
        event_type="fire.thermal_anomaly",
        payload={"place": "Test County"},
        stream_id="1-0",
    )
    gdelt = message(
        "gdelt",
        "conflict-1",
        event_type="geopolitical.gdelt_event",
        payload={"place": "Test County", "category": "Fight"},
        stream_id="2-0",
    )

    analysis = analyze_incident(incident(firms, gdelt))

    assert analysis.corroboration_count == 0
    assert analysis.contradiction_count == 0
    assert analysis.insufficient_evidence_count == 1
    assert analysis.relationships[0].label == "insufficient_evidence"
    assert analysis.relationships[0].from_claim_id is None
    assert analysis.relationships[0].to_claim_id is None


def test_usgs_tsunami_flag_and_nws_warning_agree_only_on_tsunami_domain() -> None:
    usgs = message(
        "usgs",
        "quake-1",
        event_type="seismic.earthquake",
        payload={"place": "Coastal County", "tsunami": True},
        stream_id="1-0",
    )
    nws = message(
        "nws",
        "tsunami-warning-1",
        event_type="weather.alert",
        payload={"place": "Coastal County", "alert_type": "Tsunami Warning", "category": "Geo"},
        stream_id="2-0",
    )

    analysis = analyze_incident(incident(usgs, nws))

    assert analysis.corroboration_count == 1
    relationship = analysis.relationships[0]
    assert relationship.predicate == "hazard_domain"
    assert relationship.normalized_value == "tsunami_related"
    assert {claim.value for claim in analysis.claims} == {"seismic", "tsunami_related"}
    tsunami_claim = next(
        claim for claim in analysis.claims if claim.evidence_field == "payload.tsunami"
    )
    assert tsunami_claim.qualifier is not None
    assert "not observed impact" in tsunami_claim.qualifier


def test_mutually_exclusive_same_place_operational_claims_flag_contradiction() -> None:
    nws = message(
        "nws",
        "alert-1",
        event_type="weather.alert",
        payload={
            "place": "Test County",
            "alert_type": "Fire Warning",
            "category": "Fire",
            "instruction": "An evacuation order is in effect. Evacuate immediately.",
        },
        stream_id="1-0",
    )
    report = message(
        "local_authority",
        "report-1",
        event_type="safety.update",
        payload={
            "place": "TEST COUNTY",
            "headline": "Evacuation order has been lifted",
        },
        stream_id="2-0",
    )

    analysis = analyze_incident(incident(nws, report))

    assert analysis.contradiction_count == 1
    contradiction = next(item for item in analysis.relationships if item.label == "contradicts")
    assert contradiction.predicate == "evacuation_state"
    assert contradiction.normalized_value is None
    assert contradiction.basis == "mutually_exclusive_structured_values"
    assert "active" in contradiction.rationale
    assert "lifted" in contradiction.rationale


def test_opposite_operational_phrases_at_different_places_are_not_compared() -> None:
    alert = message(
        "nws",
        "alert-1",
        event_type="weather.alert",
        payload={"place": "North County", "instruction": "Evacuate immediately."},
        stream_id="1-0",
    )
    report = message(
        "local_authority",
        "report-1",
        event_type="safety.update",
        payload={"place": "South County", "headline": "Evacuation order has been lifted"},
        stream_id="2-0",
    )

    analysis = analyze_incident(incident(alert, report))

    assert analysis.contradiction_count == 0
    assert analysis.insufficient_evidence_count == 1


def test_same_place_road_closure_and_reopening_flag_contradiction() -> None:
    closure = message(
        "nws",
        "alert-1",
        event_type="weather.alert",
        payload={"place": "Test Pass", "description": "The road remains closed."},
        stream_id="1-0",
    )
    reopening = message(
        "transport_authority",
        "update-1",
        event_type="transport.update",
        payload={"place": "Test Pass", "title": "Road has reopened"},
        stream_id="2-0",
    )

    analysis = analyze_incident(incident(closure, reopening))

    assert analysis.contradiction_count == 1
    relationship = analysis.relationships[0]
    assert relationship.predicate == "road_access_state"
    assert relationship.label == "contradicts"


def test_nws_category_fallback_and_missing_place_are_handled_conservatively() -> None:
    health_alert = message(
        "nws",
        "health-1",
        event_type="weather.alert",
        payload={"place": 42, "alert_type": "Special Statement", "category": "Health"},
        stream_id="1-0",
    )
    unknown = message(
        "future_source",
        "unknown-1",
        event_type="future.signal",
        payload={"place": "", "description": 99},
        stream_id="2-0",
    )

    analysis = analyze_incident(incident(health_alert, unknown))

    assert [(claim.predicate, claim.value, claim.evidence_field) for claim in analysis.claims] == [
        ("hazard_domain", "health", "payload.category")
    ]
    assert analysis.insufficient_evidence_count == 1


def test_ambiguous_single_node_operational_text_is_not_promoted_to_a_claim() -> None:
    ambiguous = message(
        "nws",
        "alert-1",
        event_type="weather.alert",
        payload={
            "place": "Test County",
            "headline": "Evacuation order has been lifted",
            "instruction": "Evacuate immediately.",
        },
        stream_id="1-0",
    )
    report = message(
        "local_authority",
        "report-1",
        event_type="safety.update",
        payload={"place": "Test County", "instruction": "Evacuate immediately."},
        stream_id="2-0",
    )

    analysis = analyze_incident(incident(ambiguous, report))

    ambiguous_claims = [
        claim
        for claim in analysis.claims
        if claim.node_id == "nws:alert-1" and claim.predicate == "evacuation_state"
    ]
    assert ambiguous_claims == []
    assert analysis.insufficient_evidence_count == 1


def test_claim_and_relationship_ids_are_independent_of_pair_direction() -> None:
    first = message(
        "firms",
        "thermal-1",
        event_type="fire.thermal_anomaly",
        payload={"place": "Test County"},
        stream_id="1-0",
    )
    second = message(
        "nws",
        "fire-warning-1",
        event_type="weather.alert",
        payload={"place": "Test County", "alert_type": "Wildfire Warning", "category": "Fire"},
        stream_id="2-0",
    )

    assert analyze_incident(incident(first, second)) == analyze_incident(incident(second, first))

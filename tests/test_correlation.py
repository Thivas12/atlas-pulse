"""Deterministic, non-causal evidence-graph contract tests."""

from datetime import UTC, datetime, timedelta

import pytest
from agent_rag_core import Event, GeoPoint

from atlas_pulse.correlation import (
    CORRELATION_CAVEAT,
    CORRELATION_RELATION,
    CORRELATION_RULE_VERSION,
    CorrelationBatch,
    CorrelationPair,
    build_incident_candidates,
    evidence_node_id,
)
from atlas_pulse.projections import CorrelationQuery
from atlas_pulse.streams import StreamMessage


def message(
    source: str,
    event_id: str,
    *,
    stream_id: str,
    minutes: int = 0,
    latitude: float | None = 12.0,
    longitude: float | None = 77.0,
    place: object = "Test City",
) -> StreamMessage:
    location = (
        GeoPoint(latitude=latitude, longitude=longitude, altitude_km=None)
        if latitude is not None and longitude is not None
        else None
    )
    return StreamMessage(
        stream_id=stream_id,
        event=Event(
            event_id=event_id,
            event_type=f"test.{source}",
            source=source,
            occurred_at=datetime(2026, 9, 12, 12, tzinfo=UTC) + timedelta(minutes=minutes),
            ingested_at=datetime(2026, 9, 12, 13, tzinfo=UTC),
            location=location,
            payload={"place": place},
        ),
    )


def pair(
    left: StreamMessage,
    right: StreamMessage,
    *,
    distance_km: float = 2.5,
    time_delta_minutes: float = 10,
    left_basis: str = "point",
    right_basis: str = "point",
) -> CorrelationPair:
    return CorrelationPair(
        left=left,
        right=right,
        distance_km=distance_km,
        time_delta_minutes=time_delta_minutes,
        left_geometry_basis=left_basis,  # type: ignore[arg-type]
        right_geometry_basis=right_basis,  # type: ignore[arg-type]
    )


def test_builds_stable_connected_evidence_graph_independent_of_pair_order() -> None:
    fire = message("firms", "fire-1", stream_id="100-0")
    conflict = message("gdelt", "conflict-1", stream_id="101-0", minutes=5)
    weather = message("nws", "alert-1", stream_id="102-0", minutes=15)
    first = pair(fire, conflict, distance_km=0, time_delta_minutes=5)
    second = pair(conflict, weather, distance_km=7.25, time_delta_minutes=10)

    forward = build_incident_candidates(CorrelationBatch((first, second)), limit=10)
    reverse = build_incident_candidates(CorrelationBatch((second, first)), limit=10)

    assert forward == reverse
    assert forward.total_incidents == 1
    assert forward.incidents_truncated is False
    incident = forward.incidents[0]
    assert incident.incident_id.startswith("incident-")
    assert incident.title == "3-source signal cluster near Test City"
    assert incident.sources == ("firms", "gdelt", "nws")
    assert [node.node_id for node in incident.nodes] == [
        "firms:fire-1",
        "gdelt:conflict-1",
        "nws:alert-1",
    ]
    assert len(incident.edges) == 2
    assert incident.edges[0].rule_version == CORRELATION_RULE_VERSION
    assert {edge.relation for edge in incident.edges} == {CORRELATION_RELATION}
    assert {edge.spatial_relation for edge in incident.edges} == {
        "intersects",
        "within_radius",
    }
    assert incident.max_distance_km == 7.25
    assert incident.time_span_minutes == 15
    assert incident.caveat == CORRELATION_CAVEAT


def test_keeps_disconnected_components_and_applies_incident_limit() -> None:
    recent = pair(
        message("firms", "recent-fire", stream_id="200-0", minutes=30),
        message("nws", "recent-alert", stream_id="201-0", minutes=31),
    )
    older = pair(
        message("gdelt", "older-report", stream_id="100-0"),
        message("usgs", "older-quake", stream_id="101-0", minutes=1),
    )
    result = build_incident_candidates(
        CorrelationBatch((older, recent), truncated=True),
        limit=1,
    )

    assert result.total_incidents == 2
    assert result.incidents_truncated is True
    assert result.candidate_edges_truncated is True
    assert result.incidents[0].latest_signal_at.minute == 31


def test_uses_antimeridian_safe_center_and_rounded_measurements() -> None:
    west = message(
        "firms",
        "west",
        stream_id="1-0",
        latitude=10,
        longitude=179,
    )
    east = message(
        "gdelt",
        "east",
        stream_id="2-0",
        latitude=10,
        longitude=-179,
    )
    incident = build_incident_candidates(
        CorrelationBatch((pair(west, east, distance_km=2.34567, time_delta_minutes=3.4567),)),
        limit=10,
    ).incidents[0]

    assert incident.center_latitude == pytest.approx(10.001493)
    assert abs(incident.center_longitude or 0) == 180
    assert incident.edges[0].distance_km == 2.346
    assert incident.edges[0].time_delta_minutes == 3.457


def test_degenerate_center_uses_first_canonical_node_and_missing_place_falls_back() -> None:
    first = message(
        "firms",
        "one",
        stream_id="1-0",
        latitude=0,
        longitude=0,
        place=42,
    )
    opposite = message(
        "gdelt",
        "two",
        stream_id="2-0",
        latitude=0,
        longitude=180,
        place="",
    )
    incident = build_incident_candidates(
        CorrelationBatch((pair(first, opposite),)),
        limit=10,
    ).incidents[0]

    assert incident.center_latitude == 0
    assert incident.center_longitude == 0
    assert incident.title.endswith("mapped area")


def test_component_without_focus_points_has_no_center() -> None:
    left = message("firms", "one", stream_id="1-0", latitude=None, longitude=None)
    right = message("gdelt", "two", stream_id="2-0", latitude=None, longitude=None)
    incident = build_incident_candidates(
        CorrelationBatch((pair(left, right),)),
        limit=10,
    ).incidents[0]
    assert incident.center_latitude is None
    assert incident.center_longitude is None


@pytest.mark.parametrize("value", [0, 101])
def test_builder_rejects_invalid_incident_limits(value: int) -> None:
    with pytest.raises(ValueError, match="incident limit"):
        build_incident_candidates(CorrelationBatch(()), limit=value)


@pytest.mark.parametrize(
    ("overrides", "message_text"),
    [
        ({"radius_km": 0}, "radius_km"),
        ({"radius_km": float("nan")}, "radius_km"),
        ({"time_window_minutes": 0}, "time_window_minutes"),
        ({"lookback_hours": 169}, "lookback_hours"),
        ({"edge_limit": 5_001}, "edge_limit"),
    ],
)
def test_correlation_query_rejects_unbounded_values(
    overrides: dict[str, float | int],
    message_text: str,
) -> None:
    with pytest.raises(ValueError, match=message_text):
        CorrelationQuery(**overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("distance", "minutes", "message_text"),
    [(-1, 1, "distance_km"), (float("inf"), 1, "distance_km"), (1, -1, "time_delta")],
)
def test_pair_rejects_invalid_measurements(
    distance: float,
    minutes: float,
    message_text: str,
) -> None:
    with pytest.raises(ValueError, match=message_text):
        pair(
            message("firms", "one", stream_id="1-0"),
            message("gdelt", "two", stream_id="2-0"),
            distance_km=distance,
            time_delta_minutes=minutes,
        )


def test_pair_rejects_same_source() -> None:
    with pytest.raises(ValueError, match="different sources"):
        pair(
            message("usgs", "one", stream_id="1-0"),
            message("usgs", "two", stream_id="2-0"),
        )


def test_builder_deduplicates_an_identical_candidate_pair() -> None:
    candidate = pair(
        message("firms", "one", stream_id="1-0"),
        message("gdelt", "two", stream_id="2-0"),
    )

    incident = build_incident_candidates(
        CorrelationBatch((candidate, candidate)),
        limit=10,
    ).incidents[0]

    assert len(incident.nodes) == 2
    assert len(incident.edges) == 1


def test_builder_rejects_conflicting_current_revisions_and_edge_measurements() -> None:
    first = message("firms", "one", stream_id="1-0")
    first_revision = message("firms", "one", stream_id="2-0")
    other = message("gdelt", "two", stream_id="3-0")
    with pytest.raises(ValueError, match="multiple current revisions"):
        build_incident_candidates(
            CorrelationBatch((pair(first, other), pair(first_revision, other))),
            limit=10,
        )

    with pytest.raises(ValueError, match="conflicting measurements"):
        build_incident_candidates(
            CorrelationBatch(
                (
                    pair(first, other, distance_km=1),
                    pair(first, other, distance_km=2),
                )
            ),
            limit=10,
        )


def test_evidence_node_identity_is_revision_independent() -> None:
    first = message("gdelt", "123", stream_id="1-0")
    revised = message("gdelt", "123", stream_id="99-4")
    assert evidence_node_id(first) == evidence_node_id(revised) == "gdelt:123"

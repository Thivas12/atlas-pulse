"""Live claim-pair capture, blind review, and scoring tests."""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from agent_rag_core import Event, GeoPoint
from pydantic import ValidationError

from atlas_pulse.api import create_app
from atlas_pulse.correlation import CorrelationBatch, CorrelationPair
from atlas_pulse.projections import CorrelationQuery, SignalPage, SignalQuery
from atlas_pulse.projections.base import SourceName
from atlas_pulse.relationship_evaluation import (
    JUDGMENT_COLUMNS,
    CapturedClaim,
    EventEvidence,
    RelationshipBenchmarkDefinition,
    RelationshipCaptureParameters,
    RelationshipCase,
    RelationshipPool,
    SystemPrediction,
    apply_relationship_judgments,
    build_relationship_judgment_sheet,
    capture_relationship_pool,
    relationship_case_id,
    render_relationship_markdown,
    score_relationship_pool,
)
from atlas_pulse.relationship_evaluation.cli import run_cli
from atlas_pulse.relationships import RELATIONSHIP_RULE_VERSION
from atlas_pulse.retrieval.document import document_hash, render_event_document
from atlas_pulse.streams import InMemoryEventBus, StreamMessage


class StubSignalStore:
    def __init__(self, batch: CorrelationBatch) -> None:
        self.batch = batch
        self.query: CorrelationQuery | None = None

    async def query_current(self, query: SignalQuery) -> SignalPage:
        del query
        return SignalPage(items=(), next_cursor=None, has_more=False)

    async def query_correlations(self, query: CorrelationQuery) -> CorrelationBatch:
        self.query = query
        return self.batch

    async def is_ready(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def _event(
    source: SourceName,
    event_id: str,
    *,
    event_type: str,
    payload: dict[str, object],
    minute: int,
) -> Event:
    occurred_at = datetime(2026, 9, 13, 9, minute, tzinfo=UTC)
    return Event(
        event_id=event_id,
        event_type=event_type,
        source=source,
        occurred_at=occurred_at,
        ingested_at=occurred_at,
        location=GeoPoint(latitude=34 + minute / 100, longitude=-118, altitude_km=None),
        payload=payload,
    )


def _pair(
    left: Event,
    right: Event,
    *,
    index: int,
    distance_km: float = 4.25,
) -> CorrelationPair:
    return CorrelationPair(
        left=StreamMessage(stream_id=f"{index * 2 - 1}-0", event=left),
        right=StreamMessage(stream_id=f"{index * 2}-0", event=right),
        distance_km=distance_km,
        time_delta_minutes=5,
        left_geometry_basis="point",
        right_geometry_basis="polygon",
    )


def _batch(*, truncated: bool = False, reverse: bool = False) -> CorrelationBatch:
    pairs = [
        _pair(
            _event(
                "firms",
                "thermal-1",
                event_type="fire.thermal_anomaly",
                payload={"place": "Alpha County", "title": "Thermal anomaly"},
                minute=1,
            ),
            _event(
                "nws",
                "fire-warning-1",
                event_type="weather.alert",
                payload={
                    "place": "Alpha County",
                    "alert_type": "Wildfire Warning",
                    "category": "Fire",
                },
                minute=2,
            ),
            index=1,
        ),
        _pair(
            _event(
                "firms",
                "thermal-2",
                event_type="fire.thermal_anomaly",
                payload={"place": "Beta County", "title": "Second anomaly"},
                minute=3,
            ),
            _event(
                "nws",
                "fire-warning-2",
                event_type="weather.alert",
                payload={
                    "place": "Beta County",
                    "alert_type": "Fire Warning",
                    "category": "Fire",
                },
                minute=4,
            ),
            index=2,
        ),
        _pair(
            _event(
                "usgs",
                "quake-1",
                event_type="seismic.earthquake",
                payload={"place": "Gamma County", "magnitude": 4.6},
                minute=5,
            ),
            _event(
                "gdelt",
                "conflict-1",
                event_type="geopolitical.gdelt_event",
                payload={"place": "Gamma County", "category": "Fight"},
                minute=6,
            ),
            index=3,
        ),
    ]
    if reverse:
        pairs.reverse()
    return CorrelationBatch(tuple(pairs), truncated=truncated)


def _definition(*, max_edges_per_source_pair: int = 1) -> RelationshipBenchmarkDefinition:
    return RelationshipBenchmarkDefinition(
        benchmark_id="live-claim-pairs.test",
        title="Test live claim pairs",
        description="A complete test definition for live relationship evaluation.",
        expected_correlation_rule_version="spatiotemporal-v1",
        expected_relationship_rule_version=RELATIONSHIP_RULE_VERSION,
        predicates=("hazard_domain", "evacuation_state", "road_access_state"),
        max_edges_per_source_pair=max_edges_per_source_pair,
        parameters=RelationshipCaptureParameters(
            radius_km=25,
            time_window_minutes=90,
            lookback_hours=48,
            candidate_edge_limit=250,
            incident_limit=10,
            active_only=False,
            bbox=(70, 5, 85, 20),
        ),
    )


async def _captured_pool(
    *,
    truncated: bool = False,
    reverse: bool = False,
    definition: RelationshipBenchmarkDefinition | None = None,
) -> RelationshipPool:
    store = StubSignalStore(_batch(truncated=truncated, reverse=reverse))
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus(), store))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        pool = await capture_relationship_pool(
            definition or _definition(),
            base_url="http://test/root?ignored=yes",
            client=client,
        )
    assert store.query is not None
    assert store.query.radius_km == 25
    assert store.query.bounds is not None
    return pool


def _completed_sheet(pool: RelationshipPool, *, introduce_error: bool = False) -> str:
    reader = csv.DictReader(io.StringIO(build_relationship_judgment_sheet(pool).content))
    rows = list(reader)
    for row in rows:
        is_fire_hazard = row["source_pair"] == "firms+nws" and row["predicate"] == "hazard_domain"
        row["gold_label"] = "corroborates" if is_fire_hazard else "insufficient_evidence"
        row["rationale"] = "The source texts support this rubric label."
    if introduce_error:
        fire_row = next(row for row in rows if row["gold_label"] == "corroborates")
        fire_row["gold_label"] = "insufficient_evidence"
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=JUDGMENT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


async def test_capture_samples_source_pairs_and_hides_predictions() -> None:
    pool = await _captured_pool(truncated=True)

    assert pool.endpoint == "http://test/root"
    assert pool.available_edge_count == 3
    assert pool.sampled_edge_count == 2
    assert pool.available_edges_by_source_pair == {"firms+nws": 2, "gdelt+usgs": 1}
    assert pool.sampled_edges_by_source_pair == {"firms+nws": 1, "gdelt+usgs": 1}
    assert len(pool.cases) == 6
    assert pool.candidate_edges_truncated is True
    assert {case.predicate for case in pool.cases} == {
        "hazard_domain",
        "evacuation_state",
        "road_access_state",
    }
    fire_hazard = next(
        case
        for case in pool.cases
        if case.source_pair == ("firms", "nws") and case.predicate == "hazard_domain"
    )
    assert fire_hazard.system_prediction.label == "corroborates"
    assert len(fire_hazard.system_prediction.claims) == 2

    sheet = build_relationship_judgment_sheet(pool)
    assert sheet.pending_count == 6
    assert sheet.reused_count == 0
    reader = csv.DictReader(io.StringIO(sheet.content))
    assert tuple(reader.fieldnames or ()) == JUDGMENT_COLUMNS
    rows = list(reader)
    assert all(row["gold_label"] == "" for row in rows)
    assert "system_prediction" not in sheet.content
    assert "exact_normalized_agreement" not in sheet.content


async def test_capture_sampling_is_independent_of_api_order() -> None:
    first = await _captured_pool()
    second = await _captured_pool(reverse=True)

    assert {case.case_id for case in first.cases} == {case.case_id for case in second.cases}
    assert [case.case_id for case in first.cases] == [case.case_id for case in second.cases]


async def test_capture_rejects_unsafe_endpoint_empty_graph_and_rule_drift() -> None:
    with pytest.raises(ValueError, match="absolute HTTP"):
        await capture_relationship_pool(_definition(), base_url="localhost:8000")
    with pytest.raises(ValueError, match="embedded credentials"):
        await capture_relationship_pool(_definition(), base_url="http://user:pass@test")

    empty_store = StubSignalStore(CorrelationBatch(()))
    empty_transport = httpx.ASGITransport(app=create_app(InMemoryEventBus(), empty_store))
    async with httpx.AsyncClient(transport=empty_transport, base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="no measured cross-source edges"):
            await capture_relationship_pool(_definition(), base_url="http://test", client=client)

    drifted = _definition().model_copy(
        update={"expected_relationship_rule_version": "structured-claims-v2"}
    )
    with pytest.raises(RuntimeError, match="relationship rule"):
        await _captured_pool(definition=drifted)


def test_review_import_and_metrics_cover_abstention_and_slices() -> None:
    pool = asyncio.run(_captured_pool())
    reviewed_at = datetime(2026, 9, 13, 12, tzinfo=UTC)
    reviewed = apply_relationship_judgments(
        pool,
        _completed_sheet(pool),
        reviewer="  Human   Reviewer ",
        reviewed_at=reviewed_at,
    )

    assert reviewed.judgment_status == "reviewed"
    assert reviewed.reviewer == "Human Reviewer"
    assert reviewed.reviewed_at == reviewed_at
    report = score_relationship_pool(reviewed)
    assert report.overall.case_count == 6
    assert report.overall.accuracy == 1
    assert report.overall.decisive_coverage == pytest.approx(1 / 6)
    assert report.overall.abstention_rate == pytest.approx(5 / 6)
    assert report.overall.selective_accuracy == 1
    assert report.overall.labels["corroborates"].precision == 1
    assert report.overall.labels["corroborates"].recall == 1
    assert report.overall.labels["contradicts"].precision is None
    assert report.predicates["hazard_domain"].case_count == 2
    assert report.source_pairs["firms+nws"].case_count == 3
    assert report.confusion_matrix["corroborates"]["corroborates"] == 1
    assert all(outcome.correct for outcome in report.outcomes)

    markdown = render_relationship_markdown(report)
    assert "Decisive coverage" in markdown
    assert "N/A" in markdown
    assert "| None | None |" in markdown
    assert report.report_id in markdown


def test_metrics_retain_false_decision_and_error_case() -> None:
    pool = asyncio.run(_captured_pool())
    reviewed = apply_relationship_judgments(
        pool,
        _completed_sheet(pool, introduce_error=True),
        reviewer="Reviewer",
    )
    report = score_relationship_pool(reviewed)

    assert report.overall.accuracy == pytest.approx(5 / 6)
    assert report.overall.selective_accuracy == 0
    assert report.overall.labels["corroborates"].support == 0
    assert report.overall.labels["corroborates"].predicted_count == 1
    assert report.overall.labels["corroborates"].precision == 0
    assert report.overall.labels["corroborates"].recall is None
    assert report.overall.labels["corroborates"].f1 == 0
    assert report.overall.labels["insufficient_evidence"].recall == pytest.approx(5 / 6)
    errors = [outcome for outcome in report.outcomes if not outcome.correct]
    assert len(errors) == 1
    assert errors[0].predicted_label == "corroborates"
    assert errors[0].gold_label == "insufficient_evidence"
    assert errors[0].case_id in render_relationship_markdown(report)


def test_exact_judgment_reuse_ignores_changed_system_prediction() -> None:
    pool = asyncio.run(_captured_pool())
    reviewed = apply_relationship_judgments(
        pool,
        _completed_sheet(pool),
        reviewer="Reviewer",
    )
    candidate_data = pool.model_dump(mode="python")
    candidate_data["relationship_rule_version"] = "candidate-v2"
    candidate_data["cases"][0]["system_prediction"] = {
        "label": "insufficient_evidence",
        "relationship_ids": (),
        "claims": (),
        "bases": (),
        "rationales": (),
    }
    candidate = RelationshipPool.model_validate(candidate_data)

    sheet = build_relationship_judgment_sheet(candidate, seed_pool=reviewed)

    assert sheet.reused_count == len(pool.cases)
    assert sheet.pending_count == 0
    assert all(row["gold_label"] for row in csv.DictReader(io.StringIO(sheet.content)))


def test_review_import_rejects_tampering_invalid_labels_and_incomplete_rows() -> None:
    pool = asyncio.run(_captured_pool())
    completed = _completed_sheet(pool)
    rows = list(csv.DictReader(io.StringIO(completed)))

    tampered = [dict(row) for row in rows]
    tampered[0]["left_document_text"] += " changed"
    with pytest.raises(ValueError, match="protected fields"):
        apply_relationship_judgments(
            pool,
            _write_rows(tampered),
            reviewer="Reviewer",
        )

    invalid = [dict(row) for row in rows]
    invalid[0]["gold_label"] = "maybe"
    with pytest.raises(ValueError, match="requires one of"):
        apply_relationship_judgments(pool, _write_rows(invalid), reviewer="Reviewer")

    with pytest.raises(ValueError, match="missing 1 case"):
        apply_relationship_judgments(pool, _write_rows(rows[:-1]), reviewer="Reviewer")
    with pytest.raises(ValueError, match="duplicates"):
        apply_relationship_judgments(pool, _write_rows([*rows, rows[0]]), reviewer="Reviewer")
    with pytest.raises(ValueError, match="reviewer"):
        apply_relationship_judgments(pool, completed, reviewer="   ")


def _write_rows(rows: list[dict[str, str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=JUDGMENT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def test_checked_in_live_benchmark_definition_is_strict_and_versioned() -> None:
    path = Path(__file__).parents[1] / "evals/relationships/live-claim-pairs-v1.json"
    definition = RelationshipBenchmarkDefinition.model_validate_json(
        path.read_text(encoding="utf-8")
    )

    assert definition.benchmark_id == "live-claim-pairs.v1"
    assert definition.rubric_version == "claim-pair-rubric-v1"
    assert definition.expected_relationship_rule_version == RELATIONSHIP_RULE_VERSION
    assert definition.predicates == (
        "hazard_domain",
        "evacuation_state",
        "road_access_state",
    )
    assert definition.max_edges_per_source_pair == 10
    assert definition.parameters.active_only is False


def test_contracts_reject_invalid_identities_and_partial_review_state() -> None:
    event = _event(
        "firms",
        "thermal-contract",
        event_type="fire.thermal_anomaly",
        payload={"place": "Contract County"},
        minute=7,
    )
    text = render_event_document(event)
    evidence = EventEvidence(
        node_id="firms:thermal-contract",
        source="firms",
        event_id="thermal-contract",
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        document_text=text,
        document_hash=document_hash(text),
    )
    with pytest.raises(ValidationError, match="document_hash"):
        EventEvidence.model_validate(
            {**evidence.model_dump(mode="python"), "document_hash": "0" * 64}
        )
    with pytest.raises(ValidationError, match="source:event_id"):
        EventEvidence.model_validate(
            {**evidence.model_dump(mode="python"), "node_id": "firms:wrong"}
        )
    with pytest.raises(ValidationError, match="relationship ID"):
        SystemPrediction(label="corroborates")
    with pytest.raises(ValidationError, match="must be unique"):
        SystemPrediction(
            label="insufficient_evidence",
            bases=("same", "same"),
        )
    with pytest.raises(ValidationError, match="predicates must be unique"):
        RelationshipBenchmarkDefinition.model_validate(
            {
                **_definition().model_dump(mode="python"),
                "predicates": ("hazard_domain", "hazard_domain"),
            }
        )
    with pytest.raises(ValidationError, match="west must be smaller"):
        RelationshipCaptureParameters(bbox=(20, -5, -10, 30))

    pool = asyncio.run(_captured_pool())
    partial = pool.model_dump(mode="python")
    partial["cases"][0]["gold_label"] = "corroborates"
    with pytest.raises(ValidationError, match="partial judgments"):
        RelationshipPool.model_validate(partial)


def test_case_contract_rejects_wrong_id_claim_predicate_and_endpoint_order() -> None:
    pool = asyncio.run(_captured_pool())
    case = pool.cases[0]
    data = case.model_dump(mode="python")
    with pytest.raises(ValidationError, match="case_id"):
        RelationshipCase.model_validate({**data, "case_id": "pair-" + "0" * 20})

    wrong_claim = CapturedClaim(
        claim_id="claim-wrong",
        node_id=case.left.node_id,
        predicate="hazard_domain",
        value="fire_related",
        scope="measured_edge_area",
        scope_value=None,
        evidence_field="event_type",
        evidence_excerpt="fire.thermal_anomaly",
    )
    other_predicate = next(
        predicate for predicate in _definition().predicates if predicate != wrong_claim.predicate
    )
    mismatch = {
        **data,
        "predicate": other_predicate,
        "system_prediction": {
            "label": "insufficient_evidence",
            "claims": (wrong_claim.model_dump(mode="python"),),
        },
    }
    mismatch["case_id"] = relationship_case_id(
        edge_id=case.edge_id,
        predicate=other_predicate,
        left=case.left,
        right=case.right,
        distance_km=case.distance_km,
        time_delta_minutes=case.time_delta_minutes,
        left_geometry_basis=case.left_geometry_basis,
        right_geometry_basis=case.right_geometry_basis,
    )
    with pytest.raises(ValidationError, match="claims must match"):
        RelationshipCase.model_validate(mismatch)

    reversed_data = {**data, "left": data["right"], "right": data["left"]}
    with pytest.raises(ValidationError, match="canonical node order"):
        RelationshipCase.model_validate(reversed_data)


def test_cli_review_score_capture_and_failure_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pool = asyncio.run(_captured_pool(truncated=True))
    definition_path = tmp_path / "definition.json"
    definition_path.write_text(_definition().model_dump_json(indent=2), encoding="utf-8")
    capture_pool_path = tmp_path / "captured.json"
    capture_sheet_path = tmp_path / "captured.csv"

    async def fake_capture(
        definition: RelationshipBenchmarkDefinition,
        *,
        base_url: str,
    ) -> RelationshipPool:
        assert definition.benchmark_id == pool.benchmark_id
        assert base_url == "http://localhost:8000"
        return pool

    monkeypatch.setattr(
        "atlas_pulse.relationship_evaluation.cli.capture_relationship_pool", fake_capture
    )
    assert (
        run_cli(
            [
                "capture",
                "--definition",
                str(definition_path),
                "--output",
                str(capture_pool_path),
                "--judgments-output",
                str(capture_sheet_path),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "Capture warning" in captured.err
    assert "Sampling notice" in captured.err
    assert capture_pool_path.exists()
    assert capture_sheet_path.exists()

    capture_sheet_path.write_text(_completed_sheet(pool), encoding="utf-8")
    reviewed_path = tmp_path / "reviewed.json"
    assert (
        run_cli(
            [
                "review",
                "--pool",
                str(capture_pool_path),
                "--judgments",
                str(capture_sheet_path),
                "--reviewer",
                "CLI Reviewer",
                "--output",
                str(reviewed_path),
            ]
        )
        == 0
    )
    report_json = tmp_path / "report.json"
    report_md = tmp_path / "report.md"
    assert (
        run_cli(
            [
                "score",
                "--pool",
                str(reviewed_path),
                "--output-json",
                str(report_json),
                "--output-markdown",
                str(report_md),
            ]
        )
        == 0
    )
    assert "Claim-pair evaluation" in report_md.read_text(encoding="utf-8")
    assert (
        run_cli(
            [
                "score",
                "--pool",
                str(reviewed_path),
                "--output-json",
                str(report_json),
                "--output-markdown",
                str(report_md),
            ]
        )
        == 2
    )
    assert "refusing to overwrite" in capsys.readouterr().err


def test_unreviewed_pool_cannot_be_scored_or_seed_reuse() -> None:
    pool = asyncio.run(_captured_pool())
    with pytest.raises(ValueError, match="fully reviewed"):
        score_relationship_pool(pool)
    with pytest.raises(ValueError, match="seed must be fully reviewed"):
        build_relationship_judgment_sheet(pool, seed_pool=pool)

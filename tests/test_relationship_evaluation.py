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
    ADJUDICATION_COLUMNS,
    CANDIDATE_PREDICTION_COLUMNS,
    JUDGMENT_COLUMNS,
    CandidateEvaluationTask,
    CandidatePredictionBatch,
    CandidateSystemDefinition,
    CapturedClaim,
    EventEvidence,
    RelationshipBenchmarkDefinition,
    RelationshipCandidateComparisonReport,
    RelationshipCaptureParameters,
    RelationshipCase,
    RelationshipDevelopmentCandidateComparisonReport,
    RelationshipPool,
    SystemPrediction,
    apply_candidate_predictions,
    apply_relationship_adjudication,
    apply_relationship_judgments,
    build_candidate_evaluation_task,
    build_candidate_prediction_sheet,
    build_development_candidate_evaluation_task,
    build_relationship_adjudication_sheet,
    build_relationship_judgment_sheet,
    calculate_relationship_scores,
    candidate_prediction_batch_sha256,
    candidate_task_sha256,
    capture_relationship_pool,
    compare_independent_reviews,
    relationship_capture_sha256,
    relationship_case_id,
    render_adjudication_markdown,
    render_candidate_comparison_markdown,
    render_relationship_markdown,
    render_review_agreement_markdown,
    score_relationship_candidate,
    score_relationship_development_candidate,
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
    with pytest.raises(ValueError, match="timeout_seconds"):
        await capture_relationship_pool(
            _definition(),
            base_url="http://test",
            timeout_seconds=float("nan"),
        )
    with pytest.raises(ValueError, match="timeout_seconds"):
        await capture_relationship_pool(
            _definition(),
            base_url="http://test",
            timeout_seconds=901,
        )

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
        timeout_seconds: float,
    ) -> RelationshipPool:
        assert definition.benchmark_id == pool.benchmark_id
        assert base_url == "http://localhost:8000"
        assert timeout_seconds == 300
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


def test_cli_capture_names_blank_timeout_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    definition_path = tmp_path / "definition.json"
    definition_path.write_text(_definition().model_dump_json(indent=2), encoding="utf-8")

    async def timeout_capture(
        definition: RelationshipBenchmarkDefinition,
        *,
        base_url: str,
        timeout_seconds: float,
    ) -> RelationshipPool:
        assert definition.benchmark_id == "live-claim-pairs.test"
        assert base_url == "http://localhost:8000"
        assert timeout_seconds == 450
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(
        "atlas_pulse.relationship_evaluation.cli.capture_relationship_pool",
        timeout_capture,
    )
    assert (
        run_cli(
            [
                "capture",
                "--definition",
                str(definition_path),
                "--timeout-seconds",
                "450",
                "--output",
                str(tmp_path / "pool.json"),
                "--judgments-output",
                str(tmp_path / "judgments.csv"),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "450s timeout" in captured.out
    assert "relationship evaluation failed: ReadTimeout" in captured.err
    assert "--timeout-seconds" in captured.err


def test_unreviewed_pool_cannot_be_scored_or_seed_reuse() -> None:
    pool = asyncio.run(_captured_pool())
    with pytest.raises(ValueError, match="fully reviewed"):
        score_relationship_pool(pool)
    with pytest.raises(ValueError, match="seed must be fully reviewed"):
        build_relationship_judgment_sheet(pool, seed_pool=pool)


def _reviewed_pool(
    pool: RelationshipPool,
    *,
    reviewer: str,
    introduce_error: bool = False,
) -> RelationshipPool:
    return apply_relationship_judgments(
        pool,
        _completed_sheet(pool, introduce_error=introduce_error),
        reviewer=reviewer,
    )


def _completed_adjudication_sheet(
    first: RelationshipPool,
    second: RelationshipPool,
    *,
    label: str = "corroborates",
    rationale: str = "The first review follows the explicit claim-pair rubric.",
) -> str:
    sheet = build_relationship_adjudication_sheet(first, second)
    rows = list(csv.DictReader(io.StringIO(sheet.content)))
    for row in rows:
        row["adjudicated_label"] = label
        row["adjudication_rationale"] = rationale
    return _write_adjudication_rows(rows)


def _write_adjudication_rows(rows: list[dict[str, str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=ADJUDICATION_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def test_independent_review_agreement_is_canonical_sliced_and_system_blind() -> None:
    pool = asyncio.run(_captured_pool())
    alice = _reviewed_pool(pool, reviewer="Alice Reviewer")
    bob = _reviewed_pool(pool, reviewer="Bob Reviewer", introduce_error=True)
    generated_at = datetime(2026, 9, 13, 13, tzinfo=UTC)

    report = compare_independent_reviews(bob, alice, generated_at=generated_at)
    reverse = compare_independent_reviews(alice, bob, generated_at=generated_at)

    assert report.report_id == reverse.report_id
    assert report.first_review.reviewer == "Alice Reviewer"
    assert report.second_review.reviewer == "Bob Reviewer"
    assert report.generated_at == generated_at
    assert report.overall.case_count == 6
    assert report.overall.agreement_count == 5
    assert report.overall.disagreement_count == 1
    assert report.overall.observed_agreement == pytest.approx(5 / 6)
    assert report.overall.expected_agreement == pytest.approx(5 / 6)
    assert report.overall.cohen_kappa == pytest.approx(0)
    assert report.predicates["hazard_domain"].case_count == 2
    assert report.source_pairs["firms+nws"].case_count == 3
    assert report.confusion_matrix["corroborates"]["insufficient_evidence"] == 1
    assert len(report.disagreements) == 1

    sheet = build_relationship_adjudication_sheet(bob, alice)
    rows = list(csv.DictReader(io.StringIO(sheet.content)))
    assert tuple(rows[0]) == ADJUDICATION_COLUMNS
    assert len(rows) == 1
    assert rows[0]["first_label"] == "corroborates"
    assert rows[0]["second_label"] == "insufficient_evidence"
    assert rows[0]["adjudicated_label"] == ""
    assert "system_prediction" not in sheet.content
    assert "exact_normalized_agreement" not in sheet.content
    markdown = render_review_agreement_markdown(report)
    assert "Cohen's kappa" in markdown
    assert report.disagreements[0].case_id in markdown


def test_agreement_reports_undefined_kappa_for_degenerate_marginals() -> None:
    pool = asyncio.run(_captured_pool())
    reader = csv.DictReader(io.StringIO(build_relationship_judgment_sheet(pool).content))
    rows = list(reader)
    for row in rows:
        row["gold_label"] = "insufficient_evidence"
        row["rationale"] = "Neither document makes a comparable claim."
    completed = _write_rows(rows)
    first = apply_relationship_judgments(pool, completed, reviewer="First Reviewer")
    second = apply_relationship_judgments(pool, completed, reviewer="Second Reviewer")

    report = compare_independent_reviews(first, second)

    assert report.overall.observed_agreement == 1
    assert report.overall.expected_agreement == 1
    assert report.overall.cohen_kappa is None
    assert report.disagreements == ()
    assert "N/A" in render_review_agreement_markdown(report)


def test_independent_review_rejects_identity_capture_and_review_state_drift() -> None:
    pool = asyncio.run(_captured_pool())
    first = _reviewed_pool(pool, reviewer="Same Reviewer")
    same_person = _reviewed_pool(pool, reviewer="same reviewer", introduce_error=True)
    with pytest.raises(ValueError, match="different reviewer"):
        compare_independent_reviews(first, same_person)
    with pytest.raises(ValueError, match="fully reviewed"):
        compare_independent_reviews(first, pool)

    changed_data = pool.model_dump(mode="python")
    changed_data["relationship_rule_version"] = "candidate-v2"
    changed_pool = RelationshipPool.model_validate(changed_data)
    changed_review = _reviewed_pool(changed_pool, reviewer="Other Reviewer")
    with pytest.raises(ValueError, match="exact same captured pool"):
        compare_independent_reviews(first, changed_review)


def test_system_blind_adjudication_finalizes_provenance_and_scoring() -> None:
    pool = asyncio.run(_captured_pool())
    first = _reviewed_pool(pool, reviewer="Alice Reviewer")
    second = _reviewed_pool(pool, reviewer="Bob Reviewer", introduce_error=True)
    adjudicated_at = datetime(2026, 9, 13, 14, tzinfo=UTC)
    csv_text = _completed_adjudication_sheet(first, second)

    final_pool, record = apply_relationship_adjudication(
        second,
        first,
        csv_text,
        adjudicator="Casey Adjudicator",
        adjudicated_at=adjudicated_at,
    )

    assert final_pool.schema_version == "1.1.0"
    assert final_pool.reviewer == "Casey Adjudicator"
    assert final_pool.reviewed_at == adjudicated_at
    assert final_pool.adjudication is not None
    assert final_pool.adjudication.independent_reviewers == (
        "Alice Reviewer",
        "Bob Reviewer",
    )
    assert final_pool.adjudication.agreement_report_id == record.agreement_report_id
    assert final_pool.adjudication.adjudication_decision_count == 1
    assert relationship_capture_sha256(final_pool) == relationship_capture_sha256(first)
    assert record.final_pool_sha256
    assert record.adjudication_decision_count == 1
    assert record.inherited_agreement_count == 5
    assert record.decisions[0].adjudicated_label == "corroborates"

    report = score_relationship_pool(final_pool)
    assert report.schema_version == "1.1.0"
    assert report.adjudication == final_pool.adjudication
    assert any("system-blind adjudication" in caveat for caveat in report.caveats)
    score_markdown = render_relationship_markdown(report)
    assert "Independent reviewers" in score_markdown
    assert "Alice Reviewer" in score_markdown
    adjudication_markdown = render_adjudication_markdown(record)
    assert "Casey Adjudicator" in adjudication_markdown
    assert record.decisions[0].rationale in adjudication_markdown


def test_adjudication_import_rejects_tampering_missing_decisions_and_invalid_people() -> None:
    pool = asyncio.run(_captured_pool())
    first = _reviewed_pool(pool, reviewer="Alice Reviewer")
    second = _reviewed_pool(pool, reviewer="Bob Reviewer", introduce_error=True)
    completed = _completed_adjudication_sheet(first, second)
    rows = list(csv.DictReader(io.StringIO(completed)))

    tampered = [dict(row) for row in rows]
    tampered[0]["first_label"] = "contradicts"
    with pytest.raises(ValueError, match="changed protected fields"):
        apply_relationship_adjudication(
            first,
            second,
            _write_adjudication_rows(tampered),
            adjudicator="Third Reviewer",
        )

    invalid = [dict(row) for row in rows]
    invalid[0]["adjudicated_label"] = "maybe"
    with pytest.raises(ValueError, match="requires one of"):
        apply_relationship_adjudication(
            first,
            second,
            _write_adjudication_rows(invalid),
            adjudicator="Third Reviewer",
        )

    blank_reason = [dict(row) for row in rows]
    blank_reason[0]["adjudication_rationale"] = "   "
    with pytest.raises(ValueError, match="requires a rationale"):
        apply_relationship_adjudication(
            first,
            second,
            _write_adjudication_rows(blank_reason),
            adjudicator="Third Reviewer",
        )

    with pytest.raises(ValueError, match="missing 1 disagreement"):
        apply_relationship_adjudication(
            first,
            second,
            _write_adjudication_rows([]),
            adjudicator="Third Reviewer",
        )
    with pytest.raises(ValueError, match="duplicates"):
        apply_relationship_adjudication(
            first,
            second,
            _write_adjudication_rows([*rows, rows[0]]),
            adjudicator="Third Reviewer",
        )
    with pytest.raises(ValueError, match="independent from both reviewers"):
        apply_relationship_adjudication(
            first,
            second,
            completed,
            adjudicator="alice reviewer",
        )


def test_all_agreements_finalize_with_header_only_sheet() -> None:
    pool = asyncio.run(_captured_pool())
    first = _reviewed_pool(pool, reviewer="Alice Reviewer")
    second = _reviewed_pool(pool, reviewer="Bob Reviewer")
    sheet = build_relationship_adjudication_sheet(first, second)

    assert sheet.pending_count == 0
    assert list(csv.DictReader(io.StringIO(sheet.content))) == []
    final_pool, record = apply_relationship_adjudication(
        first,
        second,
        sheet.content,
        adjudicator="Casey Adjudicator",
    )
    assert record.adjudication_decision_count == 0
    assert record.inherited_agreement_count == len(pool.cases)
    assert final_pool.adjudication is not None
    assert final_pool.adjudication.cohen_kappa == 1
    assert "No disagreements" in render_adjudication_markdown(record)


def test_adjudicated_contract_rejects_missing_provenance_and_reuse_as_first_pass() -> None:
    pool = asyncio.run(_captured_pool())
    invalid = pool.model_dump(mode="python")
    invalid["schema_version"] = "1.1.0"
    with pytest.raises(ValidationError, match="requires adjudication provenance"):
        RelationshipPool.model_validate(invalid)

    first = _reviewed_pool(pool, reviewer="Alice Reviewer")
    second = _reviewed_pool(pool, reviewer="Bob Reviewer")
    final_pool, _ = apply_relationship_adjudication(
        first,
        second,
        build_relationship_adjudication_sheet(first, second).content,
        adjudicator="Casey Adjudicator",
    )
    with pytest.raises(ValueError, match="first-pass review"):
        compare_independent_reviews(first, final_pool)


def test_cli_agreement_adjudication_and_final_score(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pool = asyncio.run(_captured_pool())
    first = _reviewed_pool(pool, reviewer="Alice Reviewer")
    second = _reviewed_pool(pool, reviewer="Bob Reviewer", introduce_error=True)
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(first.model_dump_json(indent=2), encoding="utf-8")
    second_path.write_text(second.model_dump_json(indent=2), encoding="utf-8")
    agreement_json = tmp_path / "agreement.json"
    agreement_md = tmp_path / "agreement.md"
    adjudication_csv = tmp_path / "adjudication.csv"

    assert (
        run_cli(
            [
                "agreement",
                "--first-pool",
                str(second_path),
                "--second-pool",
                str(first_path),
                "--output-json",
                str(agreement_json),
                "--output-markdown",
                str(agreement_md),
                "--adjudication-output",
                str(adjudication_csv),
            ]
        )
        == 0
    )
    assert "1 disagreement" in capsys.readouterr().out
    rows = list(csv.DictReader(io.StringIO(adjudication_csv.read_text(encoding="utf-8"))))
    rows[0]["adjudicated_label"] = "corroborates"
    rows[0]["adjudication_rationale"] = "Explicit fire claims agree at the rubric scope."
    adjudication_csv.write_text(_write_adjudication_rows(rows), encoding="utf-8")

    final_pool_path = tmp_path / "gold.json"
    adjudication_json = tmp_path / "adjudication.json"
    adjudication_md = tmp_path / "adjudication.md"
    assert (
        run_cli(
            [
                "adjudicate",
                "--first-pool",
                str(first_path),
                "--second-pool",
                str(second_path),
                "--adjudication-sheet",
                str(adjudication_csv),
                "--adjudicator",
                "Casey Adjudicator",
                "--output-pool",
                str(final_pool_path),
                "--output-json",
                str(adjudication_json),
                "--output-markdown",
                str(adjudication_md),
            ]
        )
        == 0
    )
    assert "adjudicated 1 disagreement" in capsys.readouterr().out
    final_pool = RelationshipPool.model_validate_json(final_pool_path.read_text(encoding="utf-8"))
    assert final_pool.adjudication is not None
    assert "Final pool SHA-256" in adjudication_md.read_text(encoding="utf-8")

    original_first = first_path.read_text(encoding="utf-8")
    assert (
        run_cli(
            [
                "agreement",
                "--first-pool",
                str(first_path),
                "--second-pool",
                str(second_path),
                "--output-json",
                str(first_path),
                "--output-markdown",
                str(agreement_md),
                "--adjudication-output",
                str(adjudication_csv),
                "--force",
            ]
        )
        == 2
    )
    assert "must not replace input artifacts" in capsys.readouterr().err
    assert first_path.read_text(encoding="utf-8") == original_first


def _candidate_gold_pool() -> RelationshipPool:
    pool = asyncio.run(_captured_pool())
    first = _reviewed_pool(pool, reviewer="Alice Reviewer")
    second = _reviewed_pool(pool, reviewer="Bob Reviewer", introduce_error=True)
    final_pool, _ = apply_relationship_adjudication(
        first,
        second,
        _completed_adjudication_sheet(
            first,
            second,
            label="insufficient_evidence",
            rationale="The thermal record alone does not establish the warning's claim scope.",
        ),
        adjudicator="Casey Adjudicator",
        adjudicated_at=datetime(2026, 9, 13, 14, tzinfo=UTC),
    )
    return final_pool


def _candidate_system() -> CandidateSystemDefinition:
    return CandidateSystemDefinition(
        candidate_id="local-nli-sandbox-v1",
        model_id="example/revision-pinned-local-nli",
        model_revision="a" * 40,
        model_artifact_sha256="b" * 64,
        adapter_version="relationship-nli-adapter-v1",
        input_template_sha256="c" * 64,
        runtime="onnxruntime",
        runtime_version="1.23.0",
        parameters={"max_length": 512, "abstention_threshold": 0.72, "offline": True},
    )


def _write_candidate_rows(rows: list[dict[str, str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=CANDIDATE_PREDICTION_COLUMNS,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _completed_candidate_sheet(task: CandidateEvaluationTask) -> str:
    rows = list(csv.DictReader(io.StringIO(build_candidate_prediction_sheet(task).content)))
    improvement = next(
        row
        for row in rows
        if row["source_pair"] == "firms+nws" and row["predicate"] == "hazard_domain"
    )
    regression = next(row for row in rows if row["case_id"] != improvement["case_id"])
    for index, row in enumerate(rows):
        row["predicted_label"] = "insufficient_evidence"
        row["latency_ms"] = str(10 + index)
    regression["predicted_label"] = "contradicts"
    return _write_candidate_rows(rows)


def test_candidate_task_is_deterministic_gold_blind_and_adjudication_gated() -> None:
    pool = _candidate_gold_pool()
    task = build_candidate_evaluation_task(pool)

    assert task == build_candidate_evaluation_task(pool)
    assert task.task_sha256 == candidate_task_sha256(task)
    assert task.task_id == f"candidate-task-{task.task_sha256[:20]}"
    assert task.case_count == len(pool.cases)
    assert [case.case_id for case in task.cases] == sorted(case.case_id for case in pool.cases)
    serialized = task.model_dump_json()
    assert "gold_label" not in serialized
    assert "system_prediction" not in serialized
    assert "rationale" not in serialized
    assert "Alice Reviewer" not in serialized
    assert "Bob Reviewer" not in serialized
    assert "Casey Adjudicator" not in serialized

    sheet = build_candidate_prediction_sheet(task)
    rows = list(csv.DictReader(io.StringIO(sheet.content)))
    assert sheet.pending_count == task.case_count
    assert tuple(rows[0]) == CANDIDATE_PREDICTION_COLUMNS
    assert all(row["predicted_label"] == "" and row["latency_ms"] == "" for row in rows)

    unadjudicated = _reviewed_pool(
        asyncio.run(_captured_pool()),
        reviewer="One Reviewer",
    )
    with pytest.raises(ValueError, match="independently adjudicated"):
        build_candidate_evaluation_task(unadjudicated)

    changed = task.model_dump(mode="python")
    changed["task_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="task_sha256"):
        CandidateEvaluationTask.model_validate(changed)


def test_development_candidate_path_is_single_review_explicit_and_non_promoting() -> None:
    pool = _reviewed_pool(
        asyncio.run(_captured_pool()),
        reviewer="OpenAI Codex (AI-assisted)",
    )
    task = build_development_candidate_evaluation_task(pool)
    batch = apply_candidate_predictions(
        task,
        _completed_candidate_sheet(task),
        system=_candidate_system(),
        generated_at=datetime(2026, 9, 13, 15, tzinfo=UTC),
    )
    report = score_relationship_development_candidate(
        pool,
        task,
        batch,
        review_assistance="ai_assisted",
        generated_at=datetime(2026, 9, 13, 16, tzinfo=UTC),
    )

    assert report.evaluation_scope == "single_review_development"
    assert report.promotion_status == "blocked"
    assert report.development_review.reviewer == "OpenAI Codex (AI-assisted)"
    assert report.development_review.review_assistance == "ai_assisted"
    assert report.development_review.reviewed_pool_sha256 == report.reviewed_pool_sha256
    assert len(report.promotion_blockers) == 4
    assert report.paired_outcomes.improvements == 0
    assert report.paired_outcomes.regressions == 2
    assert report.report_id.startswith(f"{pool.pool_id}-development-candidate-")

    serialized_task = task.model_dump_json()
    assert "gold_label" not in serialized_task
    assert "OpenAI Codex" not in serialized_task
    markdown = render_candidate_comparison_markdown(report)
    assert "SINGLE-REVIEW DEVELOPMENT ONLY" in markdown
    assert "Review assistance: `ai_assisted`" in markdown
    assert "single-review development labels" in markdown
    with pytest.raises(ValidationError):
        RelationshipCandidateComparisonReport.model_validate(report.model_dump(mode="python"))

    with pytest.raises(ValueError, match="single-review pool"):
        build_development_candidate_evaluation_task(_candidate_gold_pool())
    with pytest.raises(ValueError, match="one complete reviewed pool"):
        build_development_candidate_evaluation_task(asyncio.run(_captured_pool()))
    with pytest.raises(ValueError, match="review_assistance"):
        score_relationship_development_candidate(
            pool,
            task,
            batch,
            review_assistance="undeclared",  # type: ignore[arg-type]
        )

    changed = report.model_dump(mode="python")
    changed["promotion_blockers"] = ()
    with pytest.raises(ValidationError, match="fixed promotion blocker"):
        RelationshipDevelopmentCandidateComparisonReport.model_validate(changed)


def test_candidate_predictions_and_paired_score_retain_tradeoffs_without_mutation() -> None:
    pool = _candidate_gold_pool()
    original_pool = pool.model_dump_json()
    task = build_candidate_evaluation_task(pool)
    generated_at = datetime(2026, 9, 13, 15, tzinfo=UTC)
    batch = apply_candidate_predictions(
        task,
        _completed_candidate_sheet(task),
        system=_candidate_system(),
        generated_at=generated_at,
    )

    assert batch.generated_at == generated_at
    assert batch.batch_sha256 == candidate_prediction_batch_sha256(batch)
    assert batch.batch_id == f"candidate-batch-{batch.batch_sha256[:20]}"
    report = score_relationship_candidate(
        pool,
        task,
        batch,
        generated_at=datetime(2026, 9, 13, 16, tzinfo=UTC),
    )

    assert report.promotion_status == "blocked"
    assert len(report.promotion_blockers) == 3
    assert report.overall.baseline.accuracy == pytest.approx(5 / 6)
    assert report.overall.candidate.accuracy == pytest.approx(5 / 6)
    assert report.overall.delta.accuracy == 0
    assert report.paired_outcomes.improvements == 1
    assert report.paired_outcomes.regressions == 1
    assert report.paired_outcomes.unchanged_correct == 4
    assert report.paired_outcomes.changed_incorrect == 0
    assert report.paired_outcomes.label_disagreements == 2
    assert report.prediction_transition_matrix["corroborates"]["insufficient_evidence"] == 1
    assert report.prediction_transition_matrix["insufficient_evidence"]["contradicts"] == 1
    assert report.candidate_latency.mean_ms == pytest.approx(12.5)
    assert report.candidate_latency.p50_ms == pytest.approx(12.5)
    assert report.candidate_latency.p95_ms == pytest.approx(14.75)
    assert report.adjudication == pool.adjudication
    assert pool.model_dump_json() == original_pool

    markdown = render_candidate_comparison_markdown(report)
    assert "Promotion status: BLOCKED" in markdown
    assert "1 | 1 | 4" in markdown
    assert "immutable revision" in markdown
    assert "Prediction transitions" in markdown
    assert "Promotion blockers" in markdown


def test_candidate_import_fails_closed_on_identity_completeness_and_latency() -> None:
    task = build_candidate_evaluation_task(_candidate_gold_pool())
    rows = list(csv.DictReader(io.StringIO(_completed_candidate_sheet(task))))
    system = _candidate_system()

    tampered = [dict(row) for row in rows]
    tampered[0]["task_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="changed protected fields"):
        apply_candidate_predictions(task, _write_candidate_rows(tampered), system=system)

    invalid_label = [dict(row) for row in rows]
    invalid_label[0]["predicted_label"] = "maybe"
    with pytest.raises(ValueError, match="requires one of"):
        apply_candidate_predictions(task, _write_candidate_rows(invalid_label), system=system)

    invalid_latency = [dict(row) for row in rows]
    invalid_latency[0]["latency_ms"] = "nan"
    with pytest.raises(ValueError, match="finite and non-negative"):
        apply_candidate_predictions(task, _write_candidate_rows(invalid_latency), system=system)

    with pytest.raises(ValueError, match="missing 1 case"):
        apply_candidate_predictions(task, _write_candidate_rows(rows[:-1]), system=system)
    with pytest.raises(ValueError, match="duplicates"):
        apply_candidate_predictions(task, _write_candidate_rows([*rows, rows[0]]), system=system)

    with pytest.raises(ValidationError, match="model_revision"):
        CandidateSystemDefinition.model_validate(
            {**system.model_dump(mode="python"), "model_revision": "main"}
        )
    with pytest.raises(ValidationError, match="must be finite"):
        CandidateSystemDefinition.model_validate(
            {**system.model_dump(mode="python"), "parameters": {"threshold": float("nan")}}
        )


def test_candidate_score_rejects_task_drift_and_incomplete_prediction_maps() -> None:
    pool = _candidate_gold_pool()
    task = build_candidate_evaluation_task(pool)
    batch = apply_candidate_predictions(
        task,
        _completed_candidate_sheet(task),
        system=_candidate_system(),
    )
    drifted_data = pool.model_dump(mode="python")
    drifted_data["capture_latency_ms"] += 1
    drifted_pool = RelationshipPool.model_validate(drifted_data)
    drifted_task = build_candidate_evaluation_task(drifted_pool)
    with pytest.raises(ValueError, match="does not match the exact adjudicated pool"):
        score_relationship_candidate(pool, drifted_task, batch)

    predictions = {case.case_id: case.system_prediction.label for case in pool.cases}
    predictions.pop(next(iter(predictions)))
    with pytest.raises(ValueError, match="missing 1 case"):
        calculate_relationship_scores(pool.cases, predictions)
    complete = {case.case_id: case.system_prediction.label for case in pool.cases}
    complete["pair-00000000000000000000"] = "insufficient_evidence"
    with pytest.raises(ValueError, match="unknown case"):
        calculate_relationship_scores(pool.cases, complete)

    changed = batch.model_dump(mode="python")
    changed["batch_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="batch_sha256"):
        CandidatePredictionBatch.model_validate(changed)


def test_cli_candidate_task_score_and_input_protection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pool = _candidate_gold_pool()
    pool_path = tmp_path / "gold-pool.json"
    pool_path.write_text(pool.model_dump_json(indent=2), encoding="utf-8")
    task_path = tmp_path / "candidate-task.json"
    predictions_path = tmp_path / "candidate-predictions.csv"

    assert (
        run_cli(
            [
                "candidate-task",
                "--pool",
                str(pool_path),
                "--output",
                str(task_path),
                "--predictions-output",
                str(predictions_path),
            ]
        )
        == 0
    )
    assert "gold-blind" in capsys.readouterr().out
    task = CandidateEvaluationTask.model_validate_json(task_path.read_text(encoding="utf-8"))
    predictions_path.write_text(_completed_candidate_sheet(task), encoding="utf-8")
    definition_path = tmp_path / "candidate-definition.json"
    definition_path.write_text(_candidate_system().model_dump_json(indent=2), encoding="utf-8")
    batch_path = tmp_path / "candidate-batch.json"
    report_path = tmp_path / "candidate-report.json"
    markdown_path = tmp_path / "candidate-report.md"

    assert (
        run_cli(
            [
                "candidate-score",
                "--pool",
                str(pool_path),
                "--task",
                str(task_path),
                "--predictions",
                str(predictions_path),
                "--candidate-definition",
                str(definition_path),
                "--output-batch",
                str(batch_path),
                "--output-json",
                str(report_path),
                "--output-markdown",
                str(markdown_path),
            ]
        )
        == 0
    )
    assert "blocked comparison" in capsys.readouterr().out
    assert (
        CandidatePredictionBatch.model_validate_json(batch_path.read_text(encoding="utf-8")).system
        == _candidate_system()
    )
    comparison = RelationshipCandidateComparisonReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    assert comparison.promotion_status == "blocked"
    assert "Promotion status: BLOCKED" in markdown_path.read_text(encoding="utf-8")

    original_pool = pool_path.read_text(encoding="utf-8")
    assert (
        run_cli(
            [
                "candidate-score",
                "--pool",
                str(pool_path),
                "--task",
                str(task_path),
                "--predictions",
                str(predictions_path),
                "--candidate-definition",
                str(definition_path),
                "--output-batch",
                str(pool_path),
                "--output-json",
                str(report_path),
                "--output-markdown",
                str(markdown_path),
                "--force",
            ]
        )
        == 2
    )
    assert "must not replace input artifacts" in capsys.readouterr().err
    assert pool_path.read_text(encoding="utf-8") == original_pool


def test_cli_development_candidate_task_and_score(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pool = _reviewed_pool(
        asyncio.run(_captured_pool()),
        reviewer="OpenAI Codex (AI-assisted)",
    )
    pool_path = tmp_path / "reviewed-pool.json"
    pool_path.write_text(pool.model_dump_json(indent=2), encoding="utf-8")
    task_path = tmp_path / "development-task.json"
    predictions_path = tmp_path / "development-predictions.csv"

    assert (
        run_cli(
            [
                "development-candidate-task",
                "--pool",
                str(pool_path),
                "--output",
                str(task_path),
                "--predictions-output",
                str(predictions_path),
            ]
        )
        == 0
    )
    assert "development-only label-blind" in capsys.readouterr().out
    task = CandidateEvaluationTask.model_validate_json(task_path.read_text(encoding="utf-8"))
    predictions_path.write_text(_completed_candidate_sheet(task), encoding="utf-8")
    definition_path = tmp_path / "candidate-definition.json"
    definition_path.write_text(_candidate_system().model_dump_json(indent=2), encoding="utf-8")
    batch_path = tmp_path / "development-batch.json"
    report_path = tmp_path / "development-report.json"
    markdown_path = tmp_path / "development-report.md"

    assert (
        run_cli(
            [
                "development-candidate-score",
                "--pool",
                str(pool_path),
                "--task",
                str(task_path),
                "--predictions",
                str(predictions_path),
                "--candidate-definition",
                str(definition_path),
                "--review-assistance",
                "ai_assisted",
                "--output-batch",
                str(batch_path),
                "--output-json",
                str(report_path),
                "--output-markdown",
                str(markdown_path),
            ]
        )
        == 0
    )
    assert "development-only blocked comparison" in capsys.readouterr().out
    report = RelationshipDevelopmentCandidateComparisonReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    assert report.development_review.review_assistance == "ai_assisted"
    assert "SINGLE-REVIEW DEVELOPMENT ONLY" in markdown_path.read_text(encoding="utf-8")

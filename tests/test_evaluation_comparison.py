"""Longitudinal retrieval comparison and exact judgment-reuse tests."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas_pulse.evaluation import (
    CandidatePool,
    CapturedRun,
    EvaluationFilters,
    EvaluationQuery,
    EvaluationQuerySet,
    PooledCandidate,
    PooledQuery,
    ScalarDelta,
    build_judgment_sheet,
    compare_pools,
    render_comparison_markdown,
)
from atlas_pulse.evaluation.cli import run_cli
from atlas_pulse.retrieval import RankingMode

NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)


def candidate(
    event_id: str,
    *,
    relevance: int | None,
    text: str | None = None,
) -> PooledCandidate:
    document_text = text or f"Operational evidence for {event_id}"
    return PooledCandidate(
        document_id=f"nws:{event_id}",
        source="nws",
        event_id=event_id,
        title=f"Alert {event_id}",
        occurred_at=NOW,
        document_text=document_text,
        document_hash=hashlib.sha256(document_text.encode()).hexdigest(),
        citation_status="traceable",
        citation_url=f"https://api.weather.gov/alerts/{event_id}",
        relevance=relevance,
        rationale="reviewed evidence" if relevance is not None else None,
    )


def evaluation_query(*, text: str = "dangerous weather affecting residents") -> EvaluationQuery:
    return EvaluationQuery(
        query_id="weather-impact",
        text=text,
        slices=("source-nws", "impact"),
        filters=EvaluationFilters(source="nws", active_only=False),
    )


def run(
    mode: RankingMode,
    document_ids: tuple[str, ...],
    *,
    rule: str,
    latency_ms: float,
) -> CapturedRun:
    return CapturedRun(
        mode=mode,
        ranking_rule=rule,
        embedding_model="test/model",
        latency_ms=latency_ms,
        document_ids=document_ids,
    )


def reviewed_pool(
    *,
    pool_id: str,
    captured_at: datetime,
    candidates: tuple[PooledCandidate, ...],
    lexical_ids: tuple[str, ...],
    hybrid_ids: tuple[str, ...],
    lexical_rule: str,
    hybrid_rule: str,
    query: EvaluationQuery | None = None,
    query_set_sha256: str = "a" * 64,
) -> CandidatePool:
    return CandidatePool(
        pool_id=pool_id,
        query_set_id="live-disruptions.v1",
        query_set_sha256=query_set_sha256,
        captured_at=captured_at,
        endpoint="https://atlas.example",
        judgment_status="reviewed",
        reviewer="Human Reviewer",
        reviewed_at=captured_at + timedelta(minutes=5),
        queries=(
            PooledQuery(
                query=query or evaluation_query(),
                candidates=candidates,
                runs=(
                    run("lexical", lexical_ids, rule=lexical_rule, latency_ms=10),
                    run("hybrid", hybrid_ids, rule=hybrid_rule, latency_ms=20),
                ),
            ),
        ),
    )


def comparison_pools() -> tuple[CandidatePool, CandidatePool]:
    baseline = reviewed_pool(
        pool_id="baseline-pool.v1",
        captured_at=NOW,
        candidates=(candidate("a", relevance=3), candidate("b", relevance=0)),
        lexical_ids=(),
        hybrid_ids=("nws:a", "nws:b"),
        lexical_rule="postgres-english-fts-v1",
        hybrid_rule="weighted-hybrid-v1",
    )
    updated = reviewed_pool(
        pool_id="candidate-pool.v1",
        captured_at=NOW + timedelta(minutes=1),
        candidates=(candidate("a", relevance=3), candidate("c", relevance=2)),
        lexical_ids=("nws:c",),
        hybrid_ids=("nws:c", "nws:a"),
        lexical_rule="postgres-english-fts-any-v2",
        hybrid_rule="rrf60-evidence-tiebreak-v2",
    )
    return baseline, updated


def test_exact_judgments_are_reused_without_reusing_changed_or_new_evidence() -> None:
    baseline, _ = comparison_pools()
    unjudged = CandidatePool(
        pool_id="new-capture.v1",
        query_set_id=baseline.query_set_id,
        query_set_sha256=baseline.query_set_sha256,
        captured_at=NOW + timedelta(minutes=2),
        endpoint="https://atlas.example",
        queries=(
            PooledQuery(
                query=evaluation_query(),
                candidates=(
                    candidate("a", relevance=None),
                    candidate("b", relevance=None, text="Revised operational evidence"),
                    candidate("c", relevance=None),
                ),
                runs=(
                    run("lexical", ("nws:a",), rule="lexical-v2", latency_ms=8),
                    run("hybrid", ("nws:a", "nws:c"), rule="hybrid-v2", latency_ms=12),
                ),
            ),
        ),
    )

    sheet = build_judgment_sheet(unjudged, seed_pool=baseline)
    rows = {
        row["document_id"]: row for row in csv.DictReader(io.StringIO(sheet.content, newline=""))
    }

    assert sheet.reused_count == 1
    assert sheet.pending_count == 2
    assert rows["nws:a"]["relevance_0_to_3"] == "3"
    assert rows["nws:a"]["rationale"] == "reviewed evidence"
    assert rows["nws:b"]["relevance_0_to_3"] == ""
    assert rows["nws:c"]["relevance_0_to_3"] == ""


def test_judgment_reuse_rejects_unreviewed_or_different_query_sets() -> None:
    baseline, _ = comparison_pools()
    raw = baseline.model_dump(mode="python")
    raw.update(judgment_status="unjudged", reviewer=None, reviewed_at=None)
    for pooled_query in raw["queries"]:
        for item in pooled_query["candidates"]:
            item.update(relevance=None, rationale=None)
    unjudged = CandidatePool.model_validate(raw)

    with pytest.raises(ValueError, match="fully reviewed"):
        build_judgment_sheet(unjudged, seed_pool=unjudged)
    with pytest.raises(ValueError, match="same query-set"):
        build_judgment_sheet(
            unjudged.model_copy(update={"query_set_sha256": "b" * 64}),
            seed_pool=baseline,
        )
    changed_query = unjudged.queries[0].model_copy(
        update={"query": evaluation_query(text="changed operator intent")}
    )
    with pytest.raises(ValueError, match="identical captured query"):
        build_judgment_sheet(
            unjudged.model_copy(update={"queries": (changed_query,)}),
            seed_pool=baseline,
        )


def test_comparison_uses_shared_candidate_denominators_and_tracks_coverage() -> None:
    baseline, updated = comparison_pools()

    comparison = compare_pools(baseline, updated, cutoffs=(2, 1, 2))

    assert comparison.schema_version == "1.0.0"
    assert comparison.capture_gap_seconds == 60
    assert (
        comparison.baseline_candidate_count,
        comparison.candidate_candidate_count,
        comparison.overlap_candidate_count,
        comparison.union_candidate_count,
    ) == (2, 2, 1, 3)
    assert comparison.baseline.ranking_rules["lexical"] == "postgres-english-fts-v1"
    assert comparison.candidate.ranking_rules["lexical"] == "postgres-english-fts-any-v2"
    lexical = comparison.modes["lexical"]
    assert lexical.candidate_coverage.baseline == 0
    assert lexical.candidate_coverage.candidate == 1
    assert lexical.resolved_empty_query_ids == ("weather-impact",)
    assert lexical.new_empty_query_ids == ()
    hybrid_at_two = comparison.modes["hybrid"].cutoffs[2].metrics
    assert hybrid_at_two["pooled_recall"].baseline == 0.5
    assert hybrid_at_two["pooled_recall"].candidate == 1
    assert hybrid_at_two["precision"].delta == 0.5
    markdown = render_comparison_markdown(comparison)
    assert "## Mode deltas at k=2" in markdown
    assert "`weather-impact`" in markdown
    assert "shared judged candidate union" in markdown


def test_comparison_fails_closed_on_incompatible_or_conflicting_evidence() -> None:
    baseline, updated = comparison_pools()

    with pytest.raises(ValueError, match="same query-set"):
        compare_pools(
            baseline,
            updated.model_copy(update={"query_set_sha256": "b" * 64}),
        )

    changed = candidate("a", relevance=3, text="Changed evidence under the same event")
    changed_query = updated.queries[0].model_copy(
        update={"candidates": (changed, candidate("c", relevance=2))}
    )
    with pytest.raises(ValueError, match="evidence changed"):
        compare_pools(baseline, updated.model_copy(update={"queries": (changed_query,)}))

    conflicting = candidate("a", relevance=1)
    conflicting_query = updated.queries[0].model_copy(
        update={"candidates": (conflicting, candidate("c", relevance=2))}
    )
    with pytest.raises(ValueError, match="conflicting relevance"):
        compare_pools(baseline, updated.model_copy(update={"queries": (conflicting_query,)}))

    changed_definition = updated.queries[0].model_copy(
        update={"query": evaluation_query(text="a different information need")}
    )
    with pytest.raises(ValueError, match="query definition changed"):
        compare_pools(baseline, updated.model_copy(update={"queries": (changed_definition,)}))


def test_scalar_delta_and_cli_comparison_contracts_are_strict(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="candidate minus baseline"):
        ScalarDelta(baseline=0.1, candidate=0.2, delta=0.5)

    baseline, updated = comparison_pools()
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    output_json = tmp_path / "comparison.json"
    output_markdown = tmp_path / "comparison.md"
    baseline_path.write_text(baseline.model_dump_json(indent=2), encoding="utf-8")
    candidate_path.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
    arguments = [
        "compare",
        "--baseline-pool",
        str(baseline_path),
        "--candidate-pool",
        str(candidate_path),
        "--cutoff",
        "2",
        "--output-json",
        str(output_json),
        "--output-markdown",
        str(output_markdown),
    ]

    assert run_cli(arguments) == 0
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["query_set_sha256"] == "a" * 64
    assert payload["modes"]["hybrid"]["cutoffs"]["2"]["metrics"]["ndcg"]["delta"] > 0
    assert "Candidate coverage transitions" in output_markdown.read_text(encoding="utf-8")
    assert run_cli(arguments) == 2


def test_cli_capture_can_seed_only_exact_prior_judgments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline, _ = comparison_pools()
    query_set = EvaluationQuerySet(
        query_set_id=baseline.query_set_id,
        title="Live disruption comparison",
        description="A frozen query definition for exact prior-judgment reuse.",
        modes=("lexical", "hybrid"),
        queries=(evaluation_query(),),
    )
    current = CandidatePool(
        pool_id="seeded-capture.v1",
        query_set_id=baseline.query_set_id,
        query_set_sha256=baseline.query_set_sha256,
        captured_at=NOW + timedelta(minutes=2),
        endpoint="https://atlas.example",
        queries=(
            PooledQuery(
                query=evaluation_query(),
                candidates=(candidate("a", relevance=None), candidate("c", relevance=None)),
                runs=(
                    run("lexical", ("nws:c",), rule="lexical-v2", latency_ms=8),
                    run("hybrid", ("nws:a", "nws:c"), rule="hybrid-v2", latency_ms=12),
                ),
            ),
        ),
    )
    queries_path = tmp_path / "queries.json"
    seed_path = tmp_path / "reviewed-baseline.json"
    pool_path = tmp_path / "pool.json"
    sheet_path = tmp_path / "judgments.csv"
    queries_path.write_text(query_set.model_dump_json(indent=2), encoding="utf-8")
    seed_path.write_text(baseline.model_dump_json(indent=2), encoding="utf-8")

    async def fake_capture(
        received: EvaluationQuerySet,
        *,
        base_url: str,
    ) -> CandidatePool:
        assert received == query_set
        assert base_url == "https://atlas.example"
        return current

    monkeypatch.setattr("atlas_pulse.evaluation.cli.capture_pool", fake_capture)
    assert (
        run_cli(
            [
                "capture",
                "--queries",
                str(queries_path),
                "--base-url",
                "https://atlas.example",
                "--output",
                str(pool_path),
                "--judgments-output",
                str(sheet_path),
                "--seed-reviewed-pool",
                str(seed_path),
            ]
        )
        == 0
    )
    rows = {
        row["document_id"]: row
        for row in csv.DictReader(io.StringIO(sheet_path.read_text(encoding="utf-8")))
    }
    assert rows["nws:a"]["relevance_0_to_3"] == "3"
    assert rows["nws:c"]["relevance_0_to_3"] == ""
    output = capsys.readouterr().out
    assert "Reused 1 exact prior judgment" in output
    assert "1 candidate(s) remain for review" in output

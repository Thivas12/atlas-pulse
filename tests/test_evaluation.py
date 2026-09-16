"""Human-reviewed retrieval evaluation, blinding, metrics, and CLI tests."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_rag_core import Event, GeoPoint
from pydantic import ValidationError

from atlas_pulse.evaluation import (
    CandidatePool,
    CapturedRun,
    EvaluationFilters,
    EvaluationQuery,
    EvaluationQuerySet,
    GatePolicy,
    GateRule,
    PooledCandidate,
    PooledQuery,
    apply_judgments,
    canonical_sha256,
    capture_pool,
    evaluate_gates,
    export_judgments,
    metrics_at_k,
    render_markdown,
    run_judgment_session,
    score_pool,
    validate_partial_judgments,
)
from atlas_pulse.evaluation.cli import run_cli
from atlas_pulse.evaluation.judgments import JUDGMENT_COLUMNS
from atlas_pulse.retrieval import CitationStatus

NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)


def evaluation_query(query_id: str = "storm-impact") -> EvaluationQuery:
    return EvaluationQuery(
        query_id=query_id,
        text="residents ordered to shelter from severe weather",
        slices=("weather", "semantic"),
        filters=EvaluationFilters(source="nws", active_only=False),
    )


def candidate(
    event_id: str,
    *,
    relevance: int | None = None,
    citation_status: CitationStatus = "traceable",
    document_text: str | None = None,
) -> PooledCandidate:
    text = document_text or f"Weather alert evidence for {event_id}"
    return PooledCandidate(
        document_id=f"nws:{event_id}",
        source="nws",
        event_id=event_id,
        title=f"Alert {event_id}",
        occurred_at=NOW,
        document_text=text,
        document_hash=hashlib.sha256(text.encode()).hexdigest(),
        citation_status=citation_status,
        citation_url=(
            f"https://api.weather.gov/alerts/{event_id}" if citation_status == "traceable" else None
        ),
        relevance=relevance,
    )


def pool(*, reviewed: bool = False) -> CandidatePool:
    candidates = (
        candidate("doc-1", relevance=3 if reviewed else None),
        candidate("doc-2", relevance=1 if reviewed else None, citation_status="missing"),
        candidate("doc-3", relevance=0 if reviewed else None),
    )
    runs = (
        CapturedRun(
            mode="lexical",
            ranking_rule="postgres-english-fts-any-v2",
            embedding_model="test/model",
            latency_ms=10,
            document_ids=("nws:doc-2", "nws:doc-1", "nws:doc-3"),
        ),
        CapturedRun(
            mode="dense",
            ranking_rule="bge-cosine-hnsw-v1",
            embedding_model="test/model",
            latency_ms=20,
            document_ids=("nws:doc-1", "nws:doc-3", "nws:doc-2"),
        ),
        CapturedRun(
            mode="rrf",
            ranking_rule="rrf60-v1",
            embedding_model="test/model",
            latency_ms=15,
            document_ids=("nws:doc-1", "nws:doc-2", "nws:doc-3"),
        ),
        CapturedRun(
            mode="hybrid",
            ranking_rule="rrf60-evidence-tiebreak-v2",
            embedding_model="test/model",
            latency_ms=25,
            document_ids=("nws:doc-1", "nws:doc-2", "nws:doc-3"),
        ),
    )
    return CandidatePool(
        pool_id="live-disruptions-v1-20260913t120000z",
        query_set_id="live-disruptions-v1",
        query_set_sha256="a" * 64,
        captured_at=NOW,
        endpoint="https://atlas.example",
        judgment_status="reviewed" if reviewed else "unjudged",
        reviewer="Human Reviewer" if reviewed else None,
        reviewed_at=NOW if reviewed else None,
        queries=(PooledQuery(query=evaluation_query(), candidates=candidates, runs=runs),),
    )


def completed_sheet(source_pool: CandidatePool) -> str:
    reader = csv.DictReader(io.StringIO(export_judgments(source_pool)))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=JUDGMENT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for index, row in enumerate(reader):
        row["relevance_0_to_3"] = str((3, 1, 0)[index])
        row["rationale"] = "direct match" if index == 0 else ""
        writer.writerow(row)
    return output.getvalue()


def search_payload(mode: str, *, event_id: str, document_text: str) -> dict[str, Any]:
    event = Event(
        event_id=event_id,
        event_type="weather.alert",
        source="nws",
        occurred_at=NOW,
        ingested_at=NOW,
        location=GeoPoint(latitude=35, longitude=-97, altitude_km=None),
        payload={
            "title": f"Alert {event_id}",
            "source_url": f"https://api.weather.gov/alerts/{event_id}",
        },
    )
    return {
        "count": 1,
        "candidates_considered": 1,
        "items": [
            {
                "stream_id": "100-1",
                "event": event.model_dump(mode="json"),
                "document_text": document_text,
                "distance_km": None,
                "ranking": {
                    "lexical_rank": 1,
                    "lexical_score": 0.9,
                    "dense_rank": 1,
                    "dense_similarity": 0.8,
                    "rrf_score": 1,
                    "exact_phrase_match": False,
                    "token_coverage": 0.5,
                    "rerank_score": 0.9,
                },
                "citation": {
                    "status": "traceable",
                    "url": f"https://api.weather.gov/alerts/{event_id}",
                    "source_field": "source_url",
                    "reasons": ["public_http_url", "source_event_identity_attached"],
                },
            }
        ],
        "embedding_model": "test/model",
        "ranking_mode": mode,
        "ranking_rule": f"{mode}-rule",
        "caveat": "ranked evidence only",
        "parameters": {
            "query": evaluation_query().text,
            "limit": 2,
            "candidate_limit": 50,
            "source": "nws",
            "occurred_after": None,
            "occurred_before": None,
            "active_only": False,
            "bbox": None,
            "near": None,
            "radius_km": None,
            "ranking_mode": mode,
        },
    }


def test_standard_metrics_are_graded_and_explicitly_pooled() -> None:
    reviewed = pool(reviewed=True)
    pooled_query = reviewed.queries[0]
    candidates = {item.document_id: item for item in pooled_query.candidates}

    at_one = metrics_at_k(pooled_query.runs[0].document_ids, candidates, cutoff=1)
    at_three = metrics_at_k(pooled_query.runs[0].document_ids, candidates, cutoff=3)

    assert at_one.precision == 1
    assert at_one.pooled_recall == 0.5
    assert at_one.reciprocal_rank == 1
    assert at_one.ndcg == pytest.approx(1 / 7)
    assert at_one.hit_rate == 1
    assert at_one.judged_rate == 1
    assert at_one.citation_traceability == 0
    assert at_three.precision == pytest.approx(2 / 3)
    assert at_three.pooled_recall == 1
    assert at_three.ndcg < 1
    assert at_three.citation_traceability == pytest.approx(2 / 3)


def test_metrics_handle_short_empty_and_unjudged_rankings_without_inflation() -> None:
    candidates = {
        "nws:doc-1": candidate("doc-1", relevance=None),
        "nws:doc-2": candidate("doc-2", relevance=0),
    }

    metrics = metrics_at_k(("nws:doc-1",), candidates, cutoff=3)

    assert metrics.precision == 0
    assert metrics.pooled_recall == 0
    assert metrics.reciprocal_rank == 0
    assert metrics.ndcg == 0
    assert metrics.hit_rate == 0
    assert metrics.judged_rate == pytest.approx(0)
    assert metrics.citation_traceability == pytest.approx(1 / 3)

    positional = metrics_at_k(
        ("nws:unknown", "nws:relevant"),
        {"nws:relevant": candidate("relevant", relevance=3)},
        cutoff=2,
    )
    assert positional.reciprocal_rank == 0.5
    assert positional.ndcg == pytest.approx(1 / 1.5849625007)


def test_scoring_aggregates_modes_slices_percentiles_and_content_hash() -> None:
    reviewed = pool(reviewed=True)

    report = score_pool(reviewed, cutoffs=(3, 1, 3))

    assert report.schema_version == "1.1.0"
    assert report.pool_sha256 == canonical_sha256(reviewed)
    assert report.report_id.endswith(report.pool_sha256[:12])
    assert report.embedding_models == ("test/model",)
    assert set(report.modes) == {"lexical", "dense", "rrf", "hybrid"}
    assert report.modes["hybrid"].query_count == 1
    assert report.modes["hybrid"].candidate_coverage == 1
    assert report.modes["hybrid"].empty_query_ids == ()
    assert report.modes["hybrid"].latency_p50_ms == 25
    assert set(report.modes["hybrid"].cutoffs) == {1, 3}
    assert report.slices["weather"]["dense"].query_count == 1
    assert "pooled recall" in report.caveats[0].casefold()
    assert "- Report schema: `1.1.0`" in render_markdown(report)

    reordered = {"b": 2, "a": 1}
    assert canonical_sha256(reordered) == canonical_sha256({"a": 1, "b": 2})


def test_scoring_exposes_empty_query_coverage_without_calling_it_irrelevant() -> None:
    reviewed = pool(reviewed=True)
    original = reviewed.queries[0]
    empty = PooledQuery(
        query=evaluation_query("empty-source-query"),
        candidates=(),
        runs=tuple(run.model_copy(update={"document_ids": ()}) for run in original.runs),
    )
    expanded = reviewed.model_copy(update={"queries": (original, empty)})

    report = score_pool(expanded, cutoffs=(1,))

    assert report.modes["hybrid"].candidate_coverage == 0.5
    assert report.modes["hybrid"].empty_query_ids == ("empty-source-query",)
    markdown = render_markdown(report)
    assert "| hybrid | 1/2 | `empty-source-query` |" in markdown
    assert "does not prove that an eligible source corpus existed" in markdown


@pytest.mark.parametrize("cutoffs", [(), (0,), (51,)])
def test_scoring_rejects_unreviewed_pools_and_invalid_cutoffs(cutoffs: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="cutoffs"):
        score_pool(pool(reviewed=True), cutoffs=cutoffs)
    with pytest.raises(ValueError, match="fully reviewed"):
        score_pool(pool(), cutoffs=(1,))


def test_regression_gates_pass_fail_and_render_with_caveats() -> None:
    report = score_pool(pool(reviewed=True), cutoffs=(1, 3))
    policy = GatePolicy(
        policy_id="reviewed-baseline-v1",
        rules=(
            GateRule(
                rule_id="hybrid-ndcg",
                mode="hybrid",
                metric="ndcg",
                cutoff=3,
                minimum=0.9,
            ),
            GateRule(
                rule_id="hybrid-latency",
                mode="hybrid",
                metric="latency_p95_ms",
                maximum=20,
            ),
            GateRule(
                rule_id="weather-dense-precision",
                mode="dense",
                slice_name=" Weather ",
                metric="precision",
                cutoff=1,
                minimum=1,
            ),
            GateRule(
                rule_id="weather-dense-coverage",
                mode="dense",
                slice_name="weather",
                metric="candidate_coverage",
                minimum=1,
            ),
        ),
    )

    outcomes = evaluate_gates(report, policy)
    markdown = render_markdown(report, gates=outcomes)

    assert [outcome.passed for outcome in outcomes] == [True, False, True, True]
    assert outcomes[0].comparison == ">= 0.9"
    assert outcomes[1].comparison == "<= 20"
    assert outcomes[2].slice_name == "weather"
    assert outcomes[3].observed == 1
    assert "## Mode comparison" in markdown
    assert "## Slice comparison" in markdown
    assert "## Candidate coverage gaps" in markdown
    assert "| `hybrid-latency` | overall / hybrid | latency_p95_ms | FAIL" in markdown
    assert "## Caveats" in markdown
    assert "Regression gates" not in render_markdown(report)


def test_gate_and_pool_contracts_reject_ambiguous_or_corrupt_artifacts() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        GateRule(
            rule_id="bad-rule",
            mode="hybrid",
            metric="ndcg",
            cutoff=10,
            minimum=0.5,
            maximum=0.8,
        )
    with pytest.raises(ValidationError, match="must not define a cutoff"):
        GateRule(
            rule_id="bad-latency",
            mode="hybrid",
            metric="latency_p95_ms",
            cutoff=10,
            maximum=500,
        )
    with pytest.raises(ValidationError, match="must not define a cutoff"):
        GateRule(
            rule_id="bad-coverage",
            mode="hybrid",
            metric="candidate_coverage",
            cutoff=10,
            minimum=1,
        )
    with pytest.raises(ValidationError, match="require a cutoff"):
        GateRule(rule_id="bad-quality", mode="hybrid", metric="ndcg", minimum=0.5)
    with pytest.raises(ValidationError, match="non-negative"):
        GateRule(
            rule_id="negative-latency",
            mode="hybrid",
            metric="latency_p95_ms",
            maximum=-1,
        )
    with pytest.raises(ValidationError, match=r"within \[0, 1\]"):
        GateRule(
            rule_id="impossible-quality",
            mode="hybrid",
            metric="ndcg",
            cutoff=10,
            minimum=1.1,
        )
    rule = GateRule(
        rule_id="same-rule",
        mode="hybrid",
        metric="ndcg",
        cutoff=1,
        minimum=0,
    )
    with pytest.raises(ValidationError, match="unique"):
        GatePolicy(policy_id="duplicate", rules=(rule, rule))

    base = pool().model_dump(mode="python")
    base["queries"][0]["runs"][0]["document_ids"] = ("nws:unknown",)
    with pytest.raises(ValidationError, match="outside the pool"):
        CandidatePool.model_validate(base)

    reviewed = pool().model_dump(mode="python")
    reviewed.update(judgment_status="reviewed", reviewer=None, reviewed_at=None)
    with pytest.raises(ValidationError, match="reviewer"):
        CandidatePool.model_validate(reviewed)

    corrupt_document = pool().model_dump(mode="python")
    corrupt_document["queries"][0]["candidates"][0]["document_text"] = "tampered"
    with pytest.raises(ValidationError, match="document_hash"):
        CandidatePool.model_validate(corrupt_document)

    partial = pool().model_dump(mode="python")
    partial["queries"][0]["candidates"][0]["relevance"] = 2
    with pytest.raises(ValidationError, match="partial judgments"):
        CandidatePool.model_validate(partial)

    mixed_models = pool().model_dump(mode="python")
    mixed_models["queries"][0]["runs"][0]["embedding_model"] = "other/model"
    with pytest.raises(ValidationError, match="exactly one embedding model"):
        CandidatePool.model_validate(mixed_models)


def test_query_contracts_validate_production_filters_and_unique_ids() -> None:
    filters = EvaluationFilters(
        bbox=(-100, 30, -90, 40),
        near=(-97, 35),
        radius_km=100,
        occurred_after=datetime(2026, 9, 1, tzinfo=UTC),
        occurred_before=NOW,
    )
    query = EvaluationQuery(
        query_id="bounded-storm",
        text="  severe   weather ",
        slices=(" Weather ", "Geo"),
        filters=filters,
    )
    production = filters.search_query(
        text=query.text,
        limit=10,
        candidate_limit=50,
        ranking_mode="dense",
    )
    assert query.text == "severe weather"
    assert query.slices == ("weather", "geo")
    assert production.ranking_mode == "dense"
    assert production.bounds is not None and production.near is not None

    with pytest.raises(ValidationError, match="unique"):
        EvaluationQuerySet(
            query_set_id="duplicate.v1",
            title="Duplicate queries",
            description="This intentionally repeats one query identifier.",
            queries=(query, query),
        )
    with pytest.raises(ValidationError):
        EvaluationFilters(bbox=(20, -5, -10, 30))


def test_checked_in_live_query_set_is_valid_and_covers_every_source() -> None:
    query_path = Path(__file__).parents[1] / "evals/retrieval/live-disruptions-v1.json"
    query_set = EvaluationQuerySet.model_validate_json(query_path.read_text(encoding="utf-8"))

    assert len(query_set.queries) == 15
    assert query_set.modes == ("lexical", "dense", "rrf", "hybrid")
    assert {query.filters.source for query in query_set.queries} >= {
        "usgs",
        "nws",
        "firms",
        "gdelt",
        None,
    }


def test_rank_blind_sheet_round_trip_protects_metadata_and_requires_completeness() -> None:
    unjudged = pool()
    blank = export_judgments(unjudged)
    header = blank.splitlines()[0]
    assert "rank" not in header
    assert "mode" not in header
    assert "relevance_0_to_3" in header

    formula_data = unjudged.model_dump(mode="python")
    formula_text = '=HYPERLINK("https://example.org")'
    formula_data["queries"][0]["candidates"][0]["document_text"] = formula_text
    formula_data["queries"][0]["candidates"][0]["document_hash"] = hashlib.sha256(
        formula_text.encode()
    ).hexdigest()
    formula_pool = CandidatePool.model_validate(formula_data)
    formula_rows = list(csv.DictReader(io.StringIO(export_judgments(formula_pool))))
    assert formula_rows[0]["document_text"].startswith("'=")

    reviewed = apply_judgments(
        unjudged,
        completed_sheet(unjudged),
        reviewer="  Human   Reviewer ",
        reviewed_at=NOW,
    )
    assert reviewed.judgment_status == "reviewed"
    assert reviewed.reviewer == "Human Reviewer"
    assert [item.relevance for item in reviewed.queries[0].candidates] == [3, 1, 0]
    assert reviewed.queries[0].candidates[0].rationale == "direct match"

    with pytest.raises(ValueError, match="unjudged"):
        apply_judgments(reviewed, completed_sheet(unjudged), reviewer="Human")
    with pytest.raises(ValueError, match="empty"):
        apply_judgments(unjudged, completed_sheet(unjudged), reviewer="  ")

    rows = list(csv.DictReader(io.StringIO(completed_sheet(unjudged))))
    rows[0]["title"] = "tampered"
    tampered = io.StringIO(newline="")
    writer = csv.DictWriter(tampered, fieldnames=JUDGMENT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    with pytest.raises(ValueError, match="protected fields"):
        apply_judgments(unjudged, tampered.getvalue(), reviewer="Human")

    missing = "\n".join(completed_sheet(unjudged).splitlines()[:-1]) + "\n"
    with pytest.raises(ValueError, match="missing"):
        apply_judgments(unjudged, missing, reviewer="Human")


def test_terminal_judgments_save_atomically_and_resume_blanks(tmp_path: Path) -> None:
    source_pool = pool()
    judgments_path = tmp_path / "judgments.csv"
    judgments_path.write_text(export_judgments(source_pool), encoding="utf-8")
    first_answers = iter(("3", "direct match", "s", "q"))
    first_output = io.StringIO()

    first = run_judgment_session(
        source_pool,
        judgments_path,
        prompt=lambda _message: next(first_answers),
        output=first_output,
    )

    assert first.total_count == 3
    assert first.graded_count == 1
    assert first.pending_count == 2
    assert first.stopped_early is True
    first_rows = validate_partial_judgments(
        source_pool,
        judgments_path.read_text(encoding="utf-8"),
    )
    assert [row["relevance_0_to_3"] for row in first_rows] == ["3", "", ""]
    assert first_rows[0]["rationale"] == "direct match"
    assert not tuple(tmp_path.glob(".judgments.csv.*.tmp"))

    second_answers = iter(("1", "", "0", "not relevant"))
    second_output = io.StringIO()
    second = run_judgment_session(
        source_pool,
        judgments_path,
        prompt=lambda _message: next(second_answers),
        output=second_output,
    )

    assert second.graded_count == 3
    assert second.pending_count == 0
    assert second.stopped_early is False
    completed = judgments_path.read_text(encoding="utf-8")
    reviewed = apply_judgments(source_pool, completed, reviewer="Human Reviewer")
    assert [item.relevance for item in reviewed.queries[0].candidates] == [3, 1, 0]
    assert "All candidates are graded" in second_output.getvalue()
    assert "lexical" not in first_output.getvalue()

    no_op_output = io.StringIO()
    no_op = run_judgment_session(
        source_pool,
        judgments_path,
        prompt=lambda _message: pytest.fail("a completed sheet must not prompt"),
        output=no_op_output,
    )
    assert no_op.pending_count == 0
    assert "ready to import" in no_op_output.getvalue()


def test_terminal_judgments_reprompt_and_sanitize_public_text(tmp_path: Path) -> None:
    source_data = pool().model_dump(mode="python")
    unsafe_text = "Weather alert\x1b[31m with terminal control"
    source_data["queries"][0]["candidates"][0]["document_text"] = unsafe_text
    source_data["queries"][0]["candidates"][0]["document_hash"] = hashlib.sha256(
        unsafe_text.encode()
    ).hexdigest()
    source_pool = CandidatePool.model_validate(source_data)
    judgments_path = tmp_path / "judgments.csv"
    judgments_path.write_text(export_judgments(source_pool), encoding="utf-8")
    answers = iter(("?", "9", "2", "x" * 1_001, "useful but incomplete", "q"))
    output = io.StringIO()

    progress = run_judgment_session(
        source_pool,
        judgments_path,
        prompt=lambda _message: next(answers),
        output=output,
    )

    rendered = output.getvalue()
    assert progress.graded_count == 1
    assert progress.stopped_early is True
    assert "Invalid choice" in rendered
    assert "Rationale must be 1000 characters or fewer" in rendered
    assert "\x1b" not in rendered
    assert "\N{REPLACEMENT CHARACTER}" in rendered


def test_terminal_judgments_keep_original_sheet_when_atomic_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_pool = pool()
    judgments_path = tmp_path / "judgments.csv"
    original = export_judgments(source_pool)
    judgments_path.write_text(original, encoding="utf-8")
    answers = iter(("3", ""))

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("atlas_pulse.evaluation.judging.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        run_judgment_session(
            source_pool,
            judgments_path,
            prompt=lambda _message: next(answers),
            output=io.StringIO(),
        )

    assert judgments_path.read_text(encoding="utf-8") == original
    assert not tuple(tmp_path.glob(".judgments.csv.*.tmp"))


async def test_capture_pools_live_modes_and_sends_exact_production_filters() -> None:
    observed_modes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        mode = request.url.params["ranking_mode"]
        observed_modes.append(mode)
        assert request.url.params["source"] == "nws"
        assert request.url.params["active_only"] == "false"
        event_id = "shared" if mode == "lexical" else "dense-only"
        return httpx.Response(
            200,
            json=search_payload(mode, event_id=event_id, document_text=f"Evidence {event_id}"),
        )

    query_set = EvaluationQuerySet(
        query_set_id="live-disruptions.v1",
        title="Live disruption retrieval",
        description="Operator-shaped queries over current public disruption feeds.",
        pool_depth=2,
        modes=("lexical", "dense"),
        queries=(evaluation_query(),),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://atlas.example",
    ) as client:
        captured = await capture_pool(
            query_set,
            base_url="https://atlas.example/?ignored=yes",
            client=client,
        )

    assert observed_modes == ["lexical", "dense"]
    assert captured.endpoint == "https://atlas.example"
    assert captured.query_set_sha256 == canonical_sha256(query_set)
    assert len(captured.queries[0].candidates) == 2
    assert [run.mode for run in captured.queries[0].runs] == ["lexical", "dense"]
    assert all(item.relevance is None for item in captured.queries[0].candidates)


async def test_capture_paces_after_a_successful_request_exhausts_the_budget() -> None:
    waits: list[float] = []
    reports: list[tuple[int, str]] = []
    observed_modes: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        mode = request.url.params["ranking_mode"]
        observed_modes.append(mode)
        headers = (
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": "2"}
            if mode == "lexical"
            else {}
        )
        return httpx.Response(
            200,
            headers=headers,
            json=search_payload(mode, event_id=mode, document_text=f"Evidence {mode}"),
        )

    query_set = EvaluationQuerySet(
        query_set_id="rate-limited-capture.v1",
        title="Rate-limited capture",
        description="A capture that crosses one fixed API request-budget window.",
        pool_depth=2,
        modes=("lexical", "dense"),
        queries=(evaluation_query(),),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://atlas.example",
    ) as client:
        captured = await capture_pool(
            query_set,
            base_url="https://atlas.example",
            client=client,
            sleeper=fake_sleep,
            on_rate_limit_wait=lambda seconds, reason: reports.append((seconds, reason)),
        )

    assert observed_modes == ["lexical", "dense"]
    assert waits == [2.0]
    assert reports == [(2, "API budget reset")]
    assert [run.mode for run in captured.queries[0].runs] == ["lexical", "dense"]


async def test_capture_retries_a_429_using_bounded_retry_after() -> None:
    waits: list[float] = []
    request_count = 0

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        mode = request.url.params["ranking_mode"]
        return httpx.Response(
            200,
            json=search_payload(mode, event_id="recovered", document_text="Recovered evidence"),
        )

    query_set = EvaluationQuerySet(
        query_set_id="retry-capture.v1",
        title="Retry capture",
        description="A capture that safely retries one exhausted request-budget response.",
        pool_depth=2,
        modes=("hybrid",),
        queries=(evaluation_query(),),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://atlas.example",
    ) as client:
        captured = await capture_pool(
            query_set,
            base_url="https://atlas.example",
            client=client,
            sleeper=fake_sleep,
        )

    assert request_count == 2
    assert waits == [3.0]
    assert captured.queries[0].runs[0].document_ids == ("nws:recovered",)


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        ({"X-RateLimit-Remaining": "0"}, "incomplete rate-limit headers"),
        (
            {"X-RateLimit-Remaining": "invalid", "X-RateLimit-Reset-After": "1"},
            "invalid X-RateLimit-Remaining",
        ),
    ],
)
async def test_capture_rejects_invalid_success_rate_limit_headers(
    headers: dict[str, str],
    message: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        mode = request.url.params["ranking_mode"]
        return httpx.Response(
            200,
            headers=headers,
            json=search_payload(mode, event_id="header", document_text="Header evidence"),
        )

    query_set = EvaluationQuerySet(
        query_set_id="invalid-header-capture.v1",
        title="Invalid header capture",
        description="A capture that rejects incomplete or malformed pacing metadata.",
        pool_depth=2,
        modes=("hybrid",),
        queries=(evaluation_query(),),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://atlas.example",
    ) as client:
        with pytest.raises(RuntimeError, match=message):
            await capture_pool(
                query_set,
                base_url="https://atlas.example",
                client=client,
            )


async def test_capture_continues_when_the_success_budget_has_remaining_capacity() -> None:
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        mode = request.url.params["ranking_mode"]
        return httpx.Response(
            200,
            headers={"X-RateLimit-Remaining": "1", "X-RateLimit-Reset-After": "60"},
            json=search_payload(mode, event_id="capacity", document_text="Capacity evidence"),
        )

    query_set = EvaluationQuerySet(
        query_set_id="remaining-capacity.v1",
        title="Remaining capacity",
        description="A capture that still has capacity in the current request window.",
        pool_depth=2,
        modes=("hybrid",),
        queries=(evaluation_query(),),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://atlas.example",
    ) as client:
        await capture_pool(
            query_set,
            base_url="https://atlas.example",
            client=client,
            sleeper=fake_sleep,
        )

    assert waits == []


@pytest.mark.parametrize("retry_after", [None, "0", "301", "tomorrow"])
async def test_capture_rejects_missing_or_unbounded_retry_after(
    retry_after: str | None,
) -> None:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}

    def rate_limited(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers=headers)

    query_set = EvaluationQuerySet(
        query_set_id="invalid-retry-capture.v1",
        title="Invalid retry capture",
        description="A capture that rejects unsafe server-directed waiting.",
        pool_depth=2,
        modes=("hybrid",),
        queries=(evaluation_query(),),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(rate_limited),
        base_url="https://atlas.example",
    ) as client:
        with pytest.raises(RuntimeError, match="Retry-After"):
            await capture_pool(
                query_set,
                base_url="https://atlas.example",
                client=client,
            )


async def test_capture_stops_after_bounded_rate_limit_retries() -> None:
    waits: list[float] = []
    request_count = 0

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    def rate_limited(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(429, headers={"Retry-After": "1"})

    query_set = EvaluationQuerySet(
        query_set_id="bounded-retry-capture.v1",
        title="Bounded retry capture",
        description="A capture that remains rate-limited beyond its retry boundary.",
        pool_depth=2,
        modes=("hybrid",),
        queries=(evaluation_query(),),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(rate_limited),
        base_url="https://atlas.example",
    ) as client:
        with pytest.raises(RuntimeError, match="after 4 bounded request attempts"):
            await capture_pool(
                query_set,
                base_url="https://atlas.example",
                client=client,
                sleeper=fake_sleep,
            )

    assert request_count == 4
    assert waits == [1.0, 1.0, 1.0]


async def test_capture_caps_cumulative_server_directed_waiting() -> None:
    waits: list[float] = []
    request_count = 0

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    def exhausted_window(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        mode = request.url.params["ranking_mode"]
        return httpx.Response(
            200,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": "300"},
            json=search_payload(
                mode,
                event_id=f"window-{request_count}",
                document_text=f"Window evidence {request_count}",
            ),
        )

    query_set = EvaluationQuerySet(
        query_set_id="cumulative-wait-capture.v1",
        title="Cumulative wait capture",
        description="A capture that refuses excessive cumulative server-directed waiting.",
        pool_depth=2,
        modes=("lexical", "dense", "rrf", "hybrid"),
        queries=(evaluation_query("first"), evaluation_query("second")),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(exhausted_window),
        base_url="https://atlas.example",
    ) as client:
        with pytest.raises(RuntimeError, match="900-second total rate-limit wait bound"):
            await capture_pool(
                query_set,
                base_url="https://atlas.example",
                client=client,
                sleeper=fake_sleep,
            )

    assert request_count == 4
    assert waits == [300.0, 300.0, 300.0]


async def test_capture_rejects_unsafe_endpoints_mode_drift_and_document_drift() -> None:
    query_set = EvaluationQuerySet(
        query_set_id="live-disruptions.v1",
        title="Live disruption retrieval",
        description="Operator-shaped queries over current public disruption feeds.",
        pool_depth=2,
        modes=("lexical",),
        queries=(evaluation_query(),),
    )
    with pytest.raises(ValueError, match="absolute HTTP"):
        await capture_pool(query_set, base_url="localhost:8000")
    with pytest.raises(ValueError, match="credentials"):
        await capture_pool(query_set, base_url="https://user:secret@atlas.example")

    def wrong_mode(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=search_payload("dense", event_id="wrong", document_text="Wrong mode"),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(wrong_mode), base_url="https://atlas.example"
    ) as client:
        with pytest.raises(RuntimeError, match="returned mode"):
            await capture_pool(query_set, base_url="https://atlas.example", client=client)

    def wrong_echo(request: httpx.Request) -> httpx.Response:
        mode = request.url.params["ranking_mode"]
        payload = search_payload(mode, event_id="wrong-echo", document_text="Wrong filters")
        payload["parameters"]["source"] = None
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(wrong_echo), base_url="https://atlas.example"
    ) as client:
        with pytest.raises(RuntimeError, match="exact requested query"):
            await capture_pool(query_set, base_url="https://atlas.example", client=client)

    def wrong_counts(request: httpx.Request) -> httpx.Response:
        mode = request.url.params["ranking_mode"]
        payload = search_payload(mode, event_id="wrong-count", document_text="Wrong counts")
        payload["candidates_considered"] = 0
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(wrong_counts), base_url="https://atlas.example"
    ) as client:
        with pytest.raises(RuntimeError, match="inconsistent result counts"):
            await capture_pool(query_set, base_url="https://atlas.example", client=client)

    changing_set = query_set.model_copy(update={"modes": ("lexical", "dense")})

    def changing(request: httpx.Request) -> httpx.Response:
        mode = request.url.params["ranking_mode"]
        return httpx.Response(
            200,
            json=search_payload(mode, event_id="same", document_text=f"Revision {mode}"),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(changing), base_url="https://atlas.example"
    ) as client:
        with pytest.raises(RuntimeError, match="document changed"):
            await capture_pool(changing_set, base_url="https://atlas.example", client=client)


def test_cli_reviews_scores_gates_and_refuses_silent_overwrites(tmp_path: Path) -> None:
    source_pool = pool()
    pool_path = tmp_path / "pool.json"
    sheet_path = tmp_path / "judgments.csv"
    reviewed_path = tmp_path / "reviewed.json"
    report_json = tmp_path / "report.json"
    report_md = tmp_path / "report.md"
    policy_path = tmp_path / "policy.json"
    pool_path.write_text(source_pool.model_dump_json(indent=2), encoding="utf-8")
    sheet_path.write_text(completed_sheet(source_pool), encoding="utf-8")

    assert (
        run_cli(
            [
                "review",
                "--pool",
                str(pool_path),
                "--judgments",
                str(sheet_path),
                "--reviewer",
                "Human Reviewer",
                "--output",
                str(reviewed_path),
            ]
        )
        == 0
    )
    policy = GatePolicy(
        policy_id="cli-policy",
        rules=(
            GateRule(
                rule_id="latency-fails",
                mode="hybrid",
                metric="latency_p95_ms",
                maximum=1,
            ),
        ),
    )
    policy_path.write_text(policy.model_dump_json(indent=2), encoding="utf-8")
    score_args = [
        "score",
        "--pool",
        str(reviewed_path),
        "--policy",
        str(policy_path),
        "--cutoff",
        "1",
        "--output-json",
        str(report_json),
        "--output-markdown",
        str(report_md),
    ]
    assert run_cli(score_args) == 1
    machine_report = json.loads(report_json.read_text(encoding="utf-8"))
    assert machine_report["pool_id"] == source_pool.pool_id
    assert machine_report["gate_policy_id"] == "cli-policy"
    assert machine_report["gate_outcomes"][0]["passed"] is False
    assert "FAIL" in report_md.read_text(encoding="utf-8")
    assert run_cli(score_args) == 2

    ungated_json = tmp_path / "ungated.json"
    ungated_md = tmp_path / "ungated.md"
    assert (
        run_cli(
            [
                "score",
                "--pool",
                str(reviewed_path),
                "--output-json",
                str(ungated_json),
                "--output-markdown",
                str(ungated_md),
            ]
        )
        == 0
    )
    assert "Regression gates" not in ungated_md.read_text(encoding="utf-8")


def test_cli_maps_invalid_artifacts_and_duplicate_outputs_to_exit_two(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    output = tmp_path / "same"
    assert (
        run_cli(
            [
                "score",
                "--pool",
                str(invalid),
                "--output-json",
                str(output),
                "--output-markdown",
                str(output),
            ]
        )
        == 2
    )


def test_cli_judge_resumes_without_rewriting_protected_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_pool = pool()
    pool_path = tmp_path / "pool.json"
    judgments_path = tmp_path / "judgments.csv"
    pool_path.write_text(source_pool.model_dump_json(indent=2), encoding="utf-8")
    original = export_judgments(source_pool)
    judgments_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _message: "Q")

    assert (
        run_cli(
            [
                "judge",
                "--pool",
                str(pool_path),
                "--judgments",
                str(judgments_path),
            ]
        )
        == 0
    )
    assert judgments_path.read_text(encoding="utf-8") == original
    assert "Stopped safely" in capsys.readouterr().out


def test_cli_capture_writes_pool_and_rank_blind_sheet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query_set = EvaluationQuerySet(
        query_set_id="cli-capture.v1",
        title="CLI capture test",
        description="A valid query set used to prove capture output behavior.",
        modes=("hybrid",),
        queries=(evaluation_query(),),
    )
    queries_path = tmp_path / "queries.json"
    pool_path = tmp_path / "pool.json"
    sheet_path = tmp_path / "judgments.csv"
    queries_path.write_text(query_set.model_dump_json(indent=2), encoding="utf-8")

    async def fake_capture(
        received: EvaluationQuerySet,
        *,
        base_url: str,
        on_rate_limit_wait: object | None = None,
    ) -> CandidatePool:
        assert received == query_set
        assert base_url == "https://atlas.example"
        assert on_rate_limit_wait is not None
        return pool()

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
            ]
        )
        == 0
    )
    assert CandidatePool.model_validate_json(pool_path.read_text(encoding="utf-8")) == pool()
    assert tuple(csv.DictReader(io.StringIO(sheet_path.read_text(encoding="utf-8"))))


def test_cli_capture_warns_when_every_mode_is_empty_for_a_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    query_set = EvaluationQuerySet(
        query_set_id="empty-capture.v1",
        title="Empty capture test",
        description="A valid query set used to expose an empty candidate pool.",
        modes=("hybrid",),
        queries=(evaluation_query("missing-source"),),
    )
    queries_path = tmp_path / "queries.json"
    pool_path = tmp_path / "pool.json"
    sheet_path = tmp_path / "judgments.csv"
    queries_path.write_text(query_set.model_dump_json(indent=2), encoding="utf-8")
    source = pool()
    empty_pool = CandidatePool.model_validate(
        source.model_copy(
            update={
                "queries": (
                    PooledQuery(
                        query=evaluation_query("missing-source"),
                        candidates=(),
                        runs=(
                            CapturedRun(
                                mode="hybrid",
                                ranking_rule="rrf60-evidence-tiebreak-v2",
                                embedding_model="test/model",
                                latency_ms=5,
                                document_ids=(),
                            ),
                        ),
                    ),
                )
            }
        ).model_dump(mode="python")
    )

    async def fake_capture(
        _received: EvaluationQuerySet,
        *,
        base_url: str,
        on_rate_limit_wait: object | None = None,
    ) -> CandidatePool:
        assert base_url == "https://atlas.example"
        assert on_rate_limit_wait is not None
        return empty_pool

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
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert "Candidate coverage warning" in output.err
    assert "missing-source" in output.err

"""Pre-capture source, index, and exact-query readiness tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_rag_core import Event, GeoPoint
from pydantic import ValidationError

from atlas_pulse.evaluation import (
    EvaluationFilters,
    EvaluationQuery,
    EvaluationQuerySet,
    RetrievalCaptureReadinessReport,
    diagnose_retrieval_readiness,
    render_retrieval_readiness_markdown,
    retrieval_readiness_sha256,
)
from atlas_pulse.evaluation.capture import expected_parameters
from atlas_pulse.evaluation.cli import run_cli
from atlas_pulse.projections.base import SourceName
from atlas_pulse.retrieval.base import RankingMode
from atlas_pulse.source_polling import SourceFreshnessItem, SourceFreshnessResponse

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)
COMMIT_SHA = "a" * 40
EVENT = Event(
    event_id="viirs-diagnostic",
    event_type="fire.thermal_anomaly",
    source="firms",
    occurred_at=NOW - timedelta(minutes=30),
    ingested_at=NOW - timedelta(minutes=20),
    location=GeoPoint(latitude=12.9, longitude=80.2),
    payload={
        "title": "High-confidence VIIRS thermal anomaly",
        "confidence_rank": 3,
        "day_night": "night",
        "source_url": "https://firms.modaps.eosdis.nasa.gov/map/",
    },
)


def _query_set() -> EvaluationQuerySet:
    return EvaluationQuerySet(
        query_set_id="diagnostic-test.v1",
        title="Diagnostic test queries",
        description="A frozen query set used to verify pre-capture readiness evidence.",
        pool_depth=10,
        modes=("dense",),
        queries=(
            EvaluationQuery(
                query_id="firms-high-confidence-heat",
                text="high confidence intense satellite thermal anomaly",
                slices=("source-firms", "severity"),
                filters=EvaluationFilters(
                    source="firms",
                    active_only=True,
                    min_confidence_rank=3,
                ),
            ),
        ),
    )


def _inventory_query(*, active_only: bool) -> EvaluationQuery:
    return EvaluationQuery(
        query_id=f"diagnostic-firms-{'current' if active_only else 'retained'}",
        text="source",
        slices=("diagnostic",),
        filters=EvaluationFilters(source="firms", active_only=active_only),
    )


def _freshness(source: SourceName = "firms") -> SourceFreshnessResponse:
    item = SourceFreshnessItem(
        source=source,
        interval_seconds=900,
        poll_stale_after_seconds=2_700,
        source_stale_after_seconds=129_600,
        poll_status="healthy",
        source_data_status="current",
        last_outcome="succeeded",
        last_stage="complete",
        last_attempt_at=NOW - timedelta(minutes=10),
        last_success_at=NOW - timedelta(minutes=10),
        last_source_generated_at=NOW - timedelta(minutes=30),
        last_success_age_seconds=600,
        source_age_seconds=1_800,
        consecutive_failures=0,
        transport_attempts=1,
        timestamp_basis="latest_record",
        passed=True,
    )
    return SourceFreshnessResponse(generated_at=NOW, items=(item,), passed=True)


def _event_payload(*, occurred_at: str | None = None) -> dict[str, Any]:
    payload = EVENT.model_dump(mode="json")
    if occurred_at is not None:
        payload["occurred_at"] = occurred_at
    return payload


def _signal_payload(*, visible: bool, occurred_at: str | None = None) -> dict[str, Any]:
    items = (
        [{"stream_id": "100-1", "event": _event_payload(occurred_at=occurred_at)}]
        if visible
        else []
    )
    return {
        "count": len(items),
        "items": items,
        "next_cursor": "100-1" if items else None,
        "has_more": False,
        "order": "newest_revision_first",
    }


def _search_payload(
    query: EvaluationQuery,
    *,
    visible: bool,
    mode: RankingMode,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    items = (
        [
            {
                "stream_id": "100-1",
                "event": _event_payload(occurred_at=occurred_at),
                "document_text": "High-confidence nighttime VIIRS thermal anomaly",
                "distance_km": None,
                "ranking": {
                    "lexical_rank": 1 if mode == "lexical" else None,
                    "lexical_score": 0.8 if mode == "lexical" else None,
                    "dense_rank": 1 if mode == "dense" else None,
                    "dense_similarity": 0.9 if mode == "dense" else None,
                    "rrf_score": 1.0,
                    "exact_phrase_match": False,
                    "token_coverage": 0.5,
                    "rerank_score": 0.9,
                },
                "citation": {
                    "status": "traceable",
                    "url": "https://firms.modaps.eosdis.nasa.gov/map/",
                    "source_field": "source_url",
                    "reasons": ["public_http_url", "source_event_identity_attached"],
                },
            }
        ]
        if visible
        else []
    )
    return {
        "count": len(items),
        "candidates_considered": len(items),
        "items": items,
        "embedding_model": "BAAI/bge-small-en-v1.5",
        "ranking_mode": mode,
        "ranking_rule": (
            "postgres-english-fts-any-v2" if mode == "lexical" else "bge-cosine-hnsw-v1"
        ),
        "caveat": "ranked evidence only",
        "parameters": expected_parameters(
            query,
            mode=mode,
            pool_depth=1,
        ).model_dump(mode="json"),
    }


def _transport(
    *,
    exact_query_visible: bool = True,
    inventory_visible: bool = True,
    signals_visible: bool = True,
    observed_commit: str = COMMIT_SHA,
    freshness_source: SourceName = "firms",
    event_occurred_at: str | None = None,
) -> httpx.MockTransport:
    query_set = _query_set()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(
                200,
                json={"status": "ok", "version": "0.12.0", "commit_sha": observed_commit},
            )
        if request.url.path == "/readyz":
            return httpx.Response(
                200,
                json={
                    "status": "ready",
                    "version": "0.12.0",
                    "commit_sha": observed_commit,
                },
            )
        if request.url.path == "/v1/source-freshness":
            return httpx.Response(
                200,
                json=_freshness(freshness_source).model_dump(mode="json"),
            )
        if request.url.path == "/v1/signals":
            assert request.url.params["source"] == "firms"
            return httpx.Response(
                200,
                json=_signal_payload(
                    visible=signals_visible,
                    occurred_at=event_occurred_at,
                ),
            )
        if request.url.path == "/v1/search":
            requested_mode = request.url.params["ranking_mode"]
            assert requested_mode in {"lexical", "dense"}
            mode: RankingMode = "lexical" if requested_mode == "lexical" else "dense"
            if request.url.params["q"] == "source":
                assert mode == "lexical"
                query = _inventory_query(active_only=request.url.params["active_only"] == "true")
                visible = inventory_visible
            else:
                assert mode == "dense"
                query = query_set.queries[0]
                visible = exact_query_visible
            return httpx.Response(
                200,
                json=_search_payload(
                    query,
                    visible=visible,
                    mode=mode,
                    occurred_at=event_occurred_at,
                ),
            )
        raise AssertionError(f"unexpected diagnostic request: {request.url}")

    return httpx.MockTransport(handler)


async def _no_sleep(_seconds: float) -> None:
    return None


async def _report(
    *,
    exact_query_visible: bool = True,
    inventory_visible: bool = True,
) -> RetrievalCaptureReadinessReport:
    async with httpx.AsyncClient(
        transport=_transport(
            exact_query_visible=exact_query_visible,
            inventory_visible=inventory_visible,
        ),
        base_url="https://atlas.example",
    ) as client:
        return await diagnose_retrieval_readiness(
            _query_set(),
            base_url="https://atlas.example",
            expected_commit_sha=COMMIT_SHA,
            client=client,
            sleeper=_no_sleep,
        )


async def test_diagnostic_proves_commit_source_pipeline_and_exact_query_readiness() -> None:
    async with httpx.AsyncClient(
        transport=_transport(),
        base_url="https://atlas.example",
    ) as client:
        report = await diagnose_retrieval_readiness(
            _query_set(),
            base_url="https://atlas.example/?ignored=true",
            expected_commit_sha=COMMIT_SHA,
            client=client,
            sleeper=_no_sleep,
        )

    assert report.ready_for_capture is True
    assert report.blocked_sources == ()
    assert report.empty_query_ids == ()
    assert report.sources[0].source == "firms"
    assert report.sources[0].passed is True
    assert report.sources[0].current_index.ranking_mode == "lexical"
    assert report.sources[0].retained_index.ranking_mode == "lexical"
    assert report.queries[0].passed is True
    assert report.queries[0].probe.ranking_mode == "dense"
    assert report.schema_version == "1.1.0"
    assert report.rule_version == "retrieval-capture-readiness-v2"
    assert report.source_inventory_rule == "postgres-english-fts-any-v2"
    assert report.ranking_rule == "bge-cosine-hnsw-v1"
    assert report.report_sha256 == retrieval_readiness_sha256(report)
    assert RetrievalCaptureReadinessReport.model_validate_json(report.model_dump_json()) == report
    markdown = render_retrieval_readiness_markdown(report)
    assert "**Capture status: READY.**" in markdown
    assert "Source inventory rule: `postgres-english-fts-any-v2`" in markdown
    assert "| firms | yes | yes | yes | yes | yes | pass |" in markdown


async def test_diagnostic_normalizes_source_offsets_in_probe_evidence() -> None:
    async with httpx.AsyncClient(
        transport=_transport(event_occurred_at="2026-09-20T06:30:00-05:00"),
        base_url="https://atlas.example",
    ) as client:
        report = await diagnose_retrieval_readiness(
            _query_set(),
            base_url="https://atlas.example",
            expected_commit_sha=COMMIT_SHA,
            client=client,
            sleeper=_no_sleep,
        )

    expected = datetime(2026, 9, 20, 11, 30, tzinfo=UTC)
    probes = (
        report.sources[0].current_signals,
        report.sources[0].retained_signals,
        report.sources[0].current_index,
        report.sources[0].retained_index,
        report.queries[0].probe,
    )
    assert all(probe.occurred_at == expected for probe in probes)
    assert all(
        probe.occurred_at is not None and probe.occurred_at.tzinfo is UTC for probe in probes
    )


async def test_diagnostic_warns_on_an_empty_exact_filter_without_blaming_the_pipeline() -> None:
    async with httpx.AsyncClient(
        transport=_transport(exact_query_visible=False),
        base_url="https://atlas.example",
    ) as client:
        report = await diagnose_retrieval_readiness(
            _query_set(),
            base_url="https://atlas.example",
            expected_commit_sha=COMMIT_SHA,
            client=client,
            sleeper=_no_sleep,
        )

    assert report.ready_for_capture is True
    assert report.blocked_sources == ()
    assert report.empty_query_ids == ("firms-high-confidence-heat",)
    assert report.queries[0].failure_reason == "no_eligible_candidate"
    assert "**Capture status: READY WITH QUERY GAPS.**" in render_retrieval_readiness_markdown(
        report
    )


async def test_diagnostic_attributes_empty_queries_to_an_unindexed_source_pipeline() -> None:
    async with httpx.AsyncClient(
        transport=_transport(exact_query_visible=False, inventory_visible=False),
        base_url="https://atlas.example",
    ) as client:
        report = await diagnose_retrieval_readiness(
            _query_set(),
            base_url="https://atlas.example",
            expected_commit_sha=COMMIT_SHA,
            client=client,
            sleeper=_no_sleep,
        )

    assert report.blocked_sources == ("firms",)
    assert report.sources[0].blocking_reasons == (
        "current_projection_not_indexed",
        "retrieval_index_empty",
    )
    assert report.queries[0].failure_reason == "no_active_indexed_documents"


async def test_diagnostic_blocks_a_required_source_without_poll_freshness() -> None:
    async with httpx.AsyncClient(
        transport=_transport(exact_query_visible=False, freshness_source="usgs"),
        base_url="https://atlas.example",
    ) as client:
        report = await diagnose_retrieval_readiness(
            _query_set(),
            base_url="https://atlas.example",
            expected_commit_sha=COMMIT_SHA,
            client=client,
            sleeper=_no_sleep,
        )

    assert report.blocked_sources == ("firms",)
    assert report.sources[0].blocking_reasons == ("source_poll_not_configured",)
    assert report.queries[0].failure_reason == "source_pipeline_unready"


async def test_diagnostic_rejects_a_different_deployed_commit_before_capture() -> None:
    async with httpx.AsyncClient(
        transport=_transport(observed_commit="b" * 40),
        base_url="https://atlas.example",
    ) as client:
        with pytest.raises(RuntimeError, match="deployment commit mismatch"):
            await diagnose_retrieval_readiness(
                _query_set(),
                base_url="https://atlas.example",
                expected_commit_sha=COMMIT_SHA,
                client=client,
                sleeper=_no_sleep,
            )


async def test_diagnostic_report_rejects_identity_tampering() -> None:
    async with httpx.AsyncClient(
        transport=_transport(),
        base_url="https://atlas.example",
    ) as client:
        report = await diagnose_retrieval_readiness(
            _query_set(),
            base_url="https://atlas.example",
            expected_commit_sha=COMMIT_SHA,
            client=client,
            sleeper=_no_sleep,
        )

    tampered = report.model_dump(mode="json")
    tampered["application_version"] = "0.12.1"
    with pytest.raises(ValidationError, match="identity must match"):
        RetrievalCaptureReadinessReport.model_validate(tampered)


@pytest.mark.parametrize(
    ("inventory_visible", "expected_exit"),
    [(True, 0), (False, 1)],
)
def test_cli_writes_readiness_evidence_and_returns_pipeline_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    inventory_visible: bool,
    expected_exit: int,
) -> None:
    report = asyncio.run(_report(exact_query_visible=False, inventory_visible=inventory_visible))
    queries_path = tmp_path / "queries.json"
    output_json = tmp_path / "readiness.json"
    output_markdown = tmp_path / "readiness.md"
    queries_path.write_text(_query_set().model_dump_json(indent=2), encoding="utf-8")

    async def fake_diagnose(
        query_set: EvaluationQuerySet,
        *,
        base_url: str,
        expected_commit_sha: str,
        sleeper: object,
        on_rate_limit_wait: object | None = None,
    ) -> RetrievalCaptureReadinessReport:
        assert query_set == _query_set()
        assert base_url == "https://atlas.example"
        assert expected_commit_sha == COMMIT_SHA
        assert sleeper is not None
        assert on_rate_limit_wait is not None
        return report

    monkeypatch.setattr(
        "atlas_pulse.evaluation.cli.diagnose_retrieval_readiness",
        fake_diagnose,
    )
    exit_code = run_cli(
        [
            "diagnose",
            "--queries",
            str(queries_path),
            "--base-url",
            "https://atlas.example",
            "--expected-commit",
            COMMIT_SHA,
            "--output-json",
            str(output_json),
            "--output-markdown",
            str(output_markdown),
        ]
    )

    assert exit_code == expected_exit
    assert (
        RetrievalCaptureReadinessReport.model_validate_json(output_json.read_text(encoding="utf-8"))
        == report
    )
    assert report.report_id in output_markdown.read_text(encoding="utf-8")
    output = capsys.readouterr()
    assert "Eligibility warning" in output.err
    if inventory_visible:
        assert "PASS: deployment and required source pipelines" in output.out
    else:
        assert "Source pipeline blocked for: firms" in output.err

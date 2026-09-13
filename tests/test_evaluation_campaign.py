"""Multi-capture retrieval campaign and global-union trajectory tests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas_pulse.evaluation import (
    CandidatePool,
    CapturedRun,
    EvaluationCampaign,
    EvaluationFilters,
    EvaluationQuery,
    PooledCandidate,
    PooledQuery,
    build_campaign,
    render_campaign_markdown,
)
from atlas_pulse.evaluation.cli import run_cli

NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)


def _candidate(
    event_id: str,
    relevance: int,
    *,
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
        rationale="reviewed evidence",
    )


def _query() -> EvaluationQuery:
    return EvaluationQuery(
        query_id="weather-impact",
        text="dangerous weather affecting residents",
        slices=("source-nws", "impact"),
        filters=EvaluationFilters(source="nws", active_only=False),
    )


def _run(
    mode: str,
    document_ids: tuple[str, ...],
    *,
    rule: str,
    latency_ms: float,
) -> CapturedRun:
    return CapturedRun.model_validate(
        {
            "mode": mode,
            "ranking_rule": rule,
            "embedding_model": "test/model",
            "latency_ms": latency_ms,
            "document_ids": document_ids,
        }
    )


def _pool(
    *,
    pool_id: str,
    captured_at: datetime,
    reviewer: str,
    candidates: tuple[PooledCandidate, ...],
    lexical_ids: tuple[str, ...],
    hybrid_ids: tuple[str, ...],
    rule_version: int,
) -> CandidatePool:
    return CandidatePool(
        pool_id=pool_id,
        query_set_id="live-disruptions.v1",
        query_set_sha256="a" * 64,
        captured_at=captured_at,
        endpoint="https://atlas.example",
        judgment_status="reviewed",
        reviewer=reviewer,
        reviewed_at=captured_at + timedelta(minutes=15),
        queries=(
            PooledQuery(
                query=_query(),
                candidates=candidates,
                runs=(
                    _run(
                        "lexical",
                        lexical_ids,
                        rule=f"lexical-v{rule_version}",
                        latency_ms=10 + rule_version,
                    ),
                    _run(
                        "hybrid",
                        hybrid_ids,
                        rule=f"hybrid-v{rule_version}",
                        latency_ms=20 + rule_version,
                    ),
                ),
            ),
        ),
    )


def _campaign_pools() -> tuple[CandidatePool, CandidatePool, CandidatePool]:
    baseline = _pool(
        pool_id="capture-one.v1",
        captured_at=NOW,
        reviewer="Reviewer One",
        candidates=(_candidate("a", 3), _candidate("b", 0)),
        lexical_ids=(),
        hybrid_ids=("nws:a", "nws:b"),
        rule_version=1,
    )
    middle = _pool(
        pool_id="capture-two.v1",
        captured_at=NOW + timedelta(hours=1),
        reviewer="Reviewer Two",
        candidates=(_candidate("a", 3), _candidate("c", 2)),
        lexical_ids=("nws:c",),
        hybrid_ids=("nws:c", "nws:a"),
        rule_version=2,
    )
    latest = _pool(
        pool_id="capture-three.v1",
        captured_at=NOW + timedelta(hours=2),
        reviewer="Reviewer Two",
        candidates=(_candidate("a", 3), _candidate("c", 2), _candidate("d", 0)),
        lexical_ids=("nws:c",),
        hybrid_ids=("nws:a", "nws:c"),
        rule_version=2,
    )
    return baseline, middle, latest


def _unreviewed(pool: CandidatePool) -> CandidatePool:
    payload = pool.model_dump(mode="python")
    payload.update(judgment_status="unjudged", reviewer=None, reviewed_at=None)
    for pooled_query in payload["queries"]:
        for candidate in pooled_query["candidates"]:
            candidate.update(relevance=None, rationale=None)
    return CandidatePool.model_validate(payload)


def test_campaign_scores_every_capture_against_one_global_judged_union() -> None:
    pools = _campaign_pools()

    campaign = build_campaign(pools, cutoffs=(2, 1, 2))
    rebuilt = build_campaign(pools, cutoffs=(1, 2))

    assert campaign.schema_version == "1.0.0"
    assert campaign.campaign_id == rebuilt.campaign_id
    assert campaign.global_union_sha256 == rebuilt.global_union_sha256
    assert campaign.capture_count == 3
    assert campaign.reviewer_count == 2
    assert campaign.global_union_candidate_count == 4
    assert campaign.cutoffs == (1, 2)
    assert campaign.captures[0].elapsed_seconds == 0
    assert campaign.captures[-1].elapsed_seconds == 7200
    assert (
        campaign.captures[1].candidate_count,
        campaign.captures[1].overlap_previous_count,
        campaign.captures[1].added_candidate_count,
        campaign.captures[1].dropped_candidate_count,
    ) == (2, 1, 1, 1)
    assert (
        campaign.captures[2].candidate_count,
        campaign.captures[2].overlap_previous_count,
        campaign.captures[2].added_candidate_count,
        campaign.captures[2].dropped_candidate_count,
    ) == (3, 2, 1, 0)

    baseline_hybrid = campaign.captures[0].modes["hybrid"].cutoffs[2].metrics
    latest_hybrid = campaign.captures[-1].modes["hybrid"].cutoffs[2].metrics
    assert baseline_hybrid["pooled_recall"].candidate == 0.5
    assert baseline_hybrid["pooled_recall"].delta == 0
    assert latest_hybrid["pooled_recall"].candidate == 1
    assert latest_hybrid["pooled_recall"].delta == 0.5
    assert campaign.captures[-1].modes["lexical"].candidate_coverage.delta == 1

    markdown = render_campaign_markdown(campaign)
    assert "## Capture chain" in markdown
    assert "## Mode trajectories at k=2" in markdown
    assert "Values in parentheses are deltas from capture 1" in markdown
    assert "not an automatic cross-encoder" in markdown
    assert EvaluationCampaign.model_validate_json(campaign.model_dump_json()) == campaign


def test_campaign_rejects_missing_review_duplicate_or_nonchronological_pools() -> None:
    baseline, middle, latest = _campaign_pools()

    with pytest.raises(ValueError, match="at least two"):
        build_campaign((baseline,))
    with pytest.raises(ValueError, match="capture 2 pool must be fully reviewed"):
        build_campaign((baseline, _unreviewed(middle)))
    with pytest.raises(ValueError, match="unique reviewed pools"):
        build_campaign((baseline, baseline))
    with pytest.raises(ValueError, match="strict chronological order"):
        build_campaign((middle, baseline, latest))
    with pytest.raises(ValueError, match=r"within \[1, 50\]"):
        build_campaign((baseline, middle), cutoffs=())
    with pytest.raises(ValueError, match=r"within \[1, 50\]"):
        build_campaign((baseline, middle), cutoffs=(51,))


def test_campaign_rejects_query_mode_evidence_and_judgment_drift() -> None:
    baseline, middle, latest = _campaign_pools()

    with pytest.raises(ValueError, match="same query-set"):
        build_campaign((baseline, middle.model_copy(update={"query_set_sha256": "b" * 64})))

    changed_query = middle.queries[0].model_copy(
        update={"query": _query().model_copy(update={"text": "changed operator intent"})}
    )
    with pytest.raises(ValueError, match="query definition changed"):
        build_campaign((baseline, middle.model_copy(update={"queries": (changed_query,)})))

    changed_modes = middle.queries[0].model_copy(update={"runs": middle.queries[0].runs[1:]})
    with pytest.raises(ValueError, match="ranking modes changed"):
        build_campaign((baseline, middle.model_copy(update={"queries": (changed_modes,)})))

    changed_evidence = middle.queries[0].model_copy(
        update={
            "candidates": (
                _candidate("a", 3, text="Revised evidence under the same source ID"),
                _candidate("c", 2),
            )
        }
    )
    with pytest.raises(ValueError, match="evidence changed"):
        build_campaign((baseline, middle.model_copy(update={"queries": (changed_evidence,)})))

    conflicting_grade = latest.queries[0].model_copy(
        update={"candidates": (_candidate("a", 1), _candidate("c", 2), _candidate("d", 0))}
    )
    with pytest.raises(ValueError, match="conflicting relevance grades"):
        build_campaign(
            (baseline, middle, latest.model_copy(update={"queries": (conflicting_grade,)}))
        )


def test_campaign_contract_rejects_identity_transition_and_baseline_tampering() -> None:
    campaign = build_campaign(_campaign_pools(), cutoffs=(2,))

    payload = campaign.model_dump(mode="python")
    payload["campaign_id"] = "live-disruptions.v1-campaign-deadbeefdead"
    with pytest.raises(ValidationError, match="content-addressed campaign inputs"):
        EvaluationCampaign.model_validate(payload)

    payload = campaign.model_dump(mode="python")
    payload["captures"][1]["added_candidate_count"] = 0
    with pytest.raises(ValidationError, match="candidate_count must equal"):
        EvaluationCampaign.model_validate(payload)

    payload = campaign.model_dump(mode="python")
    coverage = payload["captures"][1]["modes"]["hybrid"]["candidate_coverage"]
    coverage["baseline"] = 0.25
    coverage["delta"] = coverage["candidate"] - coverage["baseline"]
    with pytest.raises(ValidationError, match="retain the first capture"):
        EvaluationCampaign.model_validate(payload)


def test_campaign_cli_writes_auditable_outputs_and_refuses_overwrite(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pools = _campaign_pools()
    pool_paths: list[Path] = []
    for index, pool in enumerate(pools, start=1):
        path = tmp_path / f"pool-{index}.json"
        path.write_text(pool.model_dump_json(indent=2), encoding="utf-8")
        pool_paths.append(path)
    output_json = tmp_path / "campaign.json"
    output_markdown = tmp_path / "campaign.md"
    arguments = ["campaign"]
    for path in pool_paths:
        arguments.extend(("--pool", str(path)))
    arguments.extend(
        (
            "--cutoff",
            "2",
            "--output-json",
            str(output_json),
            "--output-markdown",
            str(output_markdown),
        )
    )

    assert run_cli(arguments) == 0
    result = json.loads(output_json.read_text(encoding="utf-8"))
    assert result["capture_count"] == 3
    assert result["global_union_candidate_count"] == 4
    assert "Latest slice movement" in output_markdown.read_text(encoding="utf-8")
    assert "from 3 reviewed captures" in capsys.readouterr().out
    assert run_cli(arguments) == 2
    assert "refusing to overwrite" in capsys.readouterr().err

    one_pool_arguments = [
        "campaign",
        "--pool",
        str(pool_paths[0]),
        "--output-json",
        str(tmp_path / "one.json"),
        "--output-markdown",
        str(tmp_path / "one.md"),
    ]
    assert run_cli(one_pool_arguments) == 2
    assert "at least two reviewed pools" in capsys.readouterr().err

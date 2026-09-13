"""Content-addressed longitudinal campaigns over reviewed retrieval captures."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from itertools import pairwise
from typing import Literal

from pydantic import Field, field_validator, model_validator

from atlas_pulse.evaluation.base import (
    CandidatePool,
    PooledCandidate,
    PooledQuery,
    StrictModel,
)
from atlas_pulse.evaluation.comparison import (
    AggregateComparison,
    ComparedPool,
    ScalarDelta,
    compare_aggregate_metrics,
    pool_provenance,
)
from atlas_pulse.evaluation.metrics import canonical_sha256, score_pool
from atlas_pulse.retrieval import RankingMode

CampaignSchemaVersion = Literal["1.0.0"]
_CAMPAIGN_CONTRACT = "shared-reviewed-campaign-v1"
_CAMPAIGN_CAVEATS = (
    "Every capture is rescored against one global judged query-document union, so pooled-recall "
    "and ideal-ranking denominators remain identical across the complete campaign.",
    "The campaign requires complete human-reviewed pools and never creates, fills, or infers a "
    "relevance judgment.",
    "Live captures include corpus arrivals, revisions, expiry, ingestion state, endpoint changes, "
    "and cache effects; a trajectory is not automatically a controlled ranking experiment.",
    "Every quality and coverage delta is capture minus campaign baseline. Positive latency deltas "
    "are slower.",
    "The report is evidence for a human decision, not an automatic cross-encoder or model release "
    "approval.",
)


def _campaign_identifier(
    *,
    query_set_id: str,
    query_set_sha256: str,
    global_union_sha256: str,
    cutoffs: tuple[int, ...],
    capture_count: int,
    reviewer_count: int,
    global_union_candidate_count: int,
    captures: tuple[CampaignCapture, ...],
    caveats: tuple[str, ...],
) -> str:
    digest = canonical_sha256(
        {
            "contract": _CAMPAIGN_CONTRACT,
            "query_set_sha256": query_set_sha256,
            "global_union_sha256": global_union_sha256,
            "cutoffs": cutoffs,
            "capture_count": capture_count,
            "reviewer_count": reviewer_count,
            "global_union_candidate_count": global_union_candidate_count,
            "captures": tuple(
                capture.model_dump(mode="json", exclude_none=False) for capture in captures
            ),
            "caveats": caveats,
        }
    )
    return f"{query_set_id}-campaign-{digest[:12]}"


def _comparison_scalars(value: AggregateComparison) -> Iterator[tuple[str, ScalarDelta]]:
    yield "candidate_coverage", value.candidate_coverage
    yield "latency_p50_ms", value.latency_p50_ms
    yield "latency_p95_ms", value.latency_p95_ms
    for cutoff, metrics in sorted(value.cutoffs.items()):
        for metric_name, scalar in sorted(metrics.metrics.items()):
            yield f"cutoff:{cutoff}:{metric_name}", scalar


def _validate_baseline_reference(
    baseline: AggregateComparison,
    candidate: AggregateComparison,
    *,
    label: str,
) -> None:
    if candidate.query_count != baseline.query_count:
        raise ValueError(f"{label} query count must remain stable across campaign captures")
    if candidate.baseline_empty_query_ids != baseline.candidate_empty_query_ids:
        raise ValueError(f"{label} must retain the first capture as its coverage baseline")
    baseline_scalars = dict(_comparison_scalars(baseline))
    candidate_scalars = dict(_comparison_scalars(candidate))
    if set(baseline_scalars) != set(candidate_scalars):
        raise ValueError(f"{label} metric shape must remain stable across campaign captures")
    for name, scalar in candidate_scalars.items():
        expected = baseline_scalars[name].candidate
        if abs(scalar.baseline - expected) > 1e-12:
            raise ValueError(f"{label} must retain the first capture as its metric baseline")


class CampaignCapture(StrictModel):
    """One reviewed capture scored against the campaign-wide judged union."""

    sequence: int = Field(ge=1)
    pool: ComparedPool
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)
    candidate_count: int = Field(ge=0)
    overlap_previous_count: int = Field(ge=0)
    added_candidate_count: int = Field(ge=0)
    dropped_candidate_count: int = Field(ge=0)
    modes: dict[RankingMode, AggregateComparison] = Field(min_length=1)
    slices: dict[str, dict[RankingMode, AggregateComparison]] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_current_candidate_count(self) -> CampaignCapture:
        if self.overlap_previous_count + self.added_candidate_count != self.candidate_count:
            raise ValueError(
                "candidate_count must equal overlap_previous_count plus added_candidate_count"
            )
        if any(not modes for modes in self.slices.values()):
            raise ValueError("every campaign slice must contain at least one ranking mode")
        if set(self.modes) != set(self.pool.ranking_rules):
            raise ValueError("campaign mode metrics must match pool ranking-rule provenance")
        return self


class EvaluationCampaign(StrictModel):
    """Auditable multi-capture retrieval trajectory with one shared judgment universe."""

    schema_version: CampaignSchemaVersion = "1.0.0"
    campaign_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,159}$")
    query_set_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{2,79}$")
    query_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    cutoffs: tuple[int, ...] = Field(min_length=1)
    capture_count: int = Field(ge=2)
    reviewer_count: int = Field(ge=1)
    global_union_candidate_count: int = Field(ge=0)
    global_union_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    captures: tuple[CampaignCapture, ...] = Field(min_length=2)
    caveats: tuple[str, ...] = Field(min_length=1)

    @field_validator("cutoffs")
    @classmethod
    def validate_cutoffs(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("campaign cutoffs must be unique and sorted")
        if value[0] < 1 or value[-1] > 50:
            raise ValueError("campaign cutoffs must be within [1, 50]")
        return value

    @model_validator(mode="after")
    def validate_campaign_chain(self) -> EvaluationCampaign:
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("campaign generated_at must be timezone-aware")
        if self.capture_count != len(self.captures):
            raise ValueError("capture_count must match captures")
        reviewers = {capture.pool.reviewer for capture in self.captures}
        if self.reviewer_count != len(reviewers):
            raise ValueError("reviewer_count must match distinct capture reviewers")
        if self.global_union_candidate_count < max(
            capture.candidate_count for capture in self.captures
        ):
            raise ValueError("global union cannot be smaller than an individual capture")

        expected_sequences = tuple(range(1, len(self.captures) + 1))
        if tuple(capture.sequence for capture in self.captures) != expected_sequences:
            raise ValueError("campaign capture sequences must be contiguous and one-based")
        pool_hashes = tuple(capture.pool.pool_sha256 for capture in self.captures)
        if len(set(pool_hashes)) != len(pool_hashes):
            raise ValueError("campaign captures must reference unique reviewed pools")

        first = self.captures[0]
        if (
            first.elapsed_seconds != 0
            or first.overlap_previous_count != 0
            or first.dropped_candidate_count != 0
            or first.added_candidate_count != first.candidate_count
        ):
            raise ValueError("the first campaign capture must start from an empty prior state")

        expected_modes = set(first.modes)
        expected_slices = set(first.slices)
        for index, capture in enumerate(self.captures):
            if (
                capture.pool.captured_at.tzinfo is None
                or capture.pool.captured_at.utcoffset() is None
            ):
                raise ValueError("campaign capture times must be timezone-aware")
            expected_elapsed = (capture.pool.captured_at - first.pool.captured_at).total_seconds()
            if abs(capture.elapsed_seconds - expected_elapsed) > 1e-6:
                raise ValueError("elapsed_seconds must be measured from the first capture")
            if set(capture.modes) != expected_modes or set(capture.slices) != expected_slices:
                raise ValueError("campaign mode and slice shapes must remain stable")
            for mode in expected_modes:
                if set(capture.modes[mode].cutoffs) != set(self.cutoffs):
                    raise ValueError("campaign mode metrics must contain every declared cutoff")
                _validate_baseline_reference(
                    first.modes[mode],
                    capture.modes[mode],
                    label=f"mode {mode}",
                )
            for slice_name in expected_slices:
                expected_slice_modes = set(first.slices[slice_name])
                if set(capture.slices[slice_name]) != expected_slice_modes:
                    raise ValueError("campaign slice mode shapes must remain stable")
                for mode in expected_slice_modes:
                    if set(capture.slices[slice_name][mode].cutoffs) != set(self.cutoffs):
                        raise ValueError(
                            "campaign slice metrics must contain every declared cutoff"
                        )
                    _validate_baseline_reference(
                        first.slices[slice_name][mode],
                        capture.slices[slice_name][mode],
                        label=f"slice {slice_name} / mode {mode}",
                    )
            if index:
                previous = self.captures[index - 1]
                if capture.pool.captured_at <= previous.pool.captured_at:
                    raise ValueError("campaign captures must be strictly chronological")
                if (
                    capture.overlap_previous_count + capture.dropped_candidate_count
                    != previous.candidate_count
                ):
                    raise ValueError(
                        "previous candidate count must equal overlap plus dropped candidates"
                    )

        expected_id = _campaign_identifier(
            query_set_id=self.query_set_id,
            query_set_sha256=self.query_set_sha256,
            global_union_sha256=self.global_union_sha256,
            cutoffs=self.cutoffs,
            capture_count=self.capture_count,
            reviewer_count=self.reviewer_count,
            global_union_candidate_count=self.global_union_candidate_count,
            captures=self.captures,
            caveats=self.caveats,
        )
        if self.campaign_id != expected_id:
            raise ValueError("campaign_id must match the content-addressed campaign inputs")
        return self


def _validate_reviewed_pool(pool: CandidatePool, *, label: str) -> None:
    if pool.judgment_status != "reviewed" or pool.reviewer is None or pool.reviewed_at is None:
        raise ValueError(f"{label} pool must be fully reviewed")
    if pool.captured_at.tzinfo is None or pool.captured_at.utcoffset() is None:
        raise ValueError(f"{label} captured_at must be timezone-aware")
    if pool.reviewed_at.tzinfo is None or pool.reviewed_at.utcoffset() is None:
        raise ValueError(f"{label} reviewed_at must be timezone-aware")


def _candidate_keys(pool: CandidatePool) -> set[tuple[str, str]]:
    return {
        (pooled_query.query.query_id, candidate.document_id)
        for pooled_query in pool.queries
        for candidate in pooled_query.candidates
    }


def _shared_union(
    pools: tuple[CandidatePool, ...],
) -> dict[str, dict[str, PooledCandidate]]:
    reference = {item.query.query_id: item for item in pools[0].queries}
    reference_modes = {
        query_id: {run.mode for run in item.runs} for query_id, item in reference.items()
    }
    union: dict[str, dict[str, PooledCandidate]] = {query_id: {} for query_id in reference}
    for index, pool in enumerate(pools, start=1):
        if (
            pool.query_set_id != pools[0].query_set_id
            or pool.query_set_sha256 != pools[0].query_set_sha256
        ):
            raise ValueError("campaign requires the same query-set ID and SHA-256")
        queries = {item.query.query_id: item for item in pool.queries}
        if set(queries) != set(reference):
            raise ValueError("campaign requires identical query IDs")
        for query_id, reference_query in reference.items():
            current = queries[query_id]
            if current.query != reference_query.query:
                raise ValueError(f"query definition changed in capture {index}: {query_id}")
            if {run.mode for run in current.runs} != reference_modes[query_id]:
                raise ValueError(f"ranking modes changed in capture {index}: {query_id}")
            for candidate in current.candidates:
                existing = union[query_id].get(candidate.document_id)
                if existing is not None:
                    if existing.evidence_identity() != candidate.evidence_identity():
                        raise ValueError(
                            f"evidence changed under document ID {query_id} / "
                            f"{candidate.document_id}"
                        )
                    if existing.relevance != candidate.relevance:
                        raise ValueError(
                            f"conflicting relevance grades for {query_id} / {candidate.document_id}"
                        )
                else:
                    union[query_id][candidate.document_id] = candidate
    return union


def _align_pool(
    pool: CandidatePool,
    union: dict[str, dict[str, PooledCandidate]],
) -> CandidatePool:
    queries = tuple(
        PooledQuery(
            query=pooled_query.query,
            candidates=tuple(
                union[pooled_query.query.query_id][document_id]
                for document_id in sorted(union[pooled_query.query.query_id])
            ),
            runs=pooled_query.runs,
        )
        for pooled_query in pool.queries
    )
    return CandidatePool.model_validate(
        pool.model_copy(update={"queries": queries}).model_dump(mode="python")
    )


def build_campaign(
    pools: Sequence[CandidatePool],
    *,
    cutoffs: Iterable[int] = (1, 3, 5, 10),
) -> EvaluationCampaign:
    """Build one trajectory from strictly chronological, fully reviewed captures."""
    reviewed_pools = tuple(pools)
    if len(reviewed_pools) < 2:
        raise ValueError("a longitudinal campaign requires at least two reviewed pools")
    normalized_cutoffs = tuple(sorted(set(cutoffs)))
    if not normalized_cutoffs or normalized_cutoffs[0] < 1 or normalized_cutoffs[-1] > 50:
        raise ValueError("campaign cutoffs must be values within [1, 50]")

    pool_hashes: list[str] = []
    for index, pool in enumerate(reviewed_pools, start=1):
        _validate_reviewed_pool(pool, label=f"capture {index}")
        pool_hashes.append(canonical_sha256(pool))
    if len(set(pool_hashes)) != len(pool_hashes):
        raise ValueError("campaign captures must be unique reviewed pools")
    if any(
        current.captured_at <= previous.captured_at
        for previous, current in pairwise(reviewed_pools)
    ):
        raise ValueError("campaign pools must be supplied in strict chronological order")

    union = _shared_union(reviewed_pools)
    union_payload = tuple(
        {
            "query_id": query_id,
            "candidates": tuple(
                union[query_id][document_id].model_dump(
                    mode="json",
                    exclude={"rationale"},
                    exclude_none=False,
                )
                for document_id in sorted(union[query_id])
            ),
        }
        for query_id in sorted(union)
    )
    union_sha256 = canonical_sha256(union_payload)
    union_count = sum(len(candidates) for candidates in union.values())

    aligned = tuple(_align_pool(pool, union) for pool in reviewed_pools)
    reports = tuple(score_pool(pool, cutoffs=normalized_cutoffs) for pool in aligned)
    baseline_report = reports[0]
    first_captured_at = reviewed_pools[0].captured_at
    key_sets = tuple(_candidate_keys(pool) for pool in reviewed_pools)
    captures: list[CampaignCapture] = []
    for index, (pool, report, keys) in enumerate(
        zip(reviewed_pools, reports, key_sets, strict=True),
        start=1,
    ):
        previous_keys = key_sets[index - 2] if index > 1 else set()
        captures.append(
            CampaignCapture(
                sequence=index,
                pool=pool_provenance(pool),
                elapsed_seconds=(pool.captured_at - first_captured_at).total_seconds(),
                candidate_count=len(keys),
                overlap_previous_count=len(keys & previous_keys),
                added_candidate_count=len(keys - previous_keys),
                dropped_candidate_count=len(previous_keys - keys),
                modes={
                    mode: compare_aggregate_metrics(
                        baseline_report.modes[mode],
                        report.modes[mode],
                    )
                    for mode in sorted(report.modes)
                },
                slices={
                    slice_name: {
                        mode: compare_aggregate_metrics(
                            baseline_report.slices[slice_name][mode],
                            report.slices[slice_name][mode],
                        )
                        for mode in sorted(report.slices[slice_name])
                    }
                    for slice_name in sorted(report.slices)
                },
            )
        )

    capture_tuple = tuple(captures)
    reviewer_count = len({pool.reviewer for pool in reviewed_pools})
    campaign_id = _campaign_identifier(
        query_set_id=reviewed_pools[0].query_set_id,
        query_set_sha256=reviewed_pools[0].query_set_sha256,
        global_union_sha256=union_sha256,
        cutoffs=normalized_cutoffs,
        capture_count=len(capture_tuple),
        reviewer_count=reviewer_count,
        global_union_candidate_count=union_count,
        captures=capture_tuple,
        caveats=_CAMPAIGN_CAVEATS,
    )
    return EvaluationCampaign(
        campaign_id=campaign_id,
        query_set_id=reviewed_pools[0].query_set_id,
        query_set_sha256=reviewed_pools[0].query_set_sha256,
        generated_at=datetime.now(UTC),
        cutoffs=normalized_cutoffs,
        capture_count=len(capture_tuple),
        reviewer_count=reviewer_count,
        global_union_candidate_count=union_count,
        global_union_sha256=union_sha256,
        captures=capture_tuple,
        caveats=_CAMPAIGN_CAVEATS,
    )


def _metric(value: ScalarDelta, *, digits: int = 4) -> str:
    return f"{value.candidate:.{digits}f} ({value.delta:+.{digits}f})"


def _query_ids(values: tuple[str, ...]) -> str:
    return ", ".join(f"`{value}`" for value in values) or "None"


def render_campaign_markdown(campaign: EvaluationCampaign) -> str:
    """Render a compact human decision view over the complete campaign trajectory."""
    cutoff = max(campaign.cutoffs)
    lines = [
        f"# Retrieval campaign: {campaign.campaign_id}",
        "",
        f"- Campaign schema: `{campaign.schema_version}`",
        f"- Query set: `{campaign.query_set_id}`",
        f"- Query-set SHA-256: `{campaign.query_set_sha256}`",
        f"- Captures: `{campaign.capture_count}` across `{campaign.reviewer_count}` reviewer(s)",
        f"- Global judged query-document union: `{campaign.global_union_candidate_count}` "
        f"(`{campaign.global_union_sha256}`)",
        f"- Generated: `{campaign.generated_at.isoformat()}`",
        "",
        "## Capture chain",
        "",
        "| # | Pool | Captured | Reviewer | Candidates | Added | Dropped | Elapsed hours |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for capture in campaign.captures:
        lines.append(
            f"| {capture.sequence} | `{capture.pool.pool_id}` | "
            f"{capture.pool.captured_at.isoformat()} | {capture.pool.reviewer} | "
            f"{capture.candidate_count} | {capture.added_candidate_count} | "
            f"{capture.dropped_candidate_count} | {capture.elapsed_seconds / 3600:.2f} |"
        )

    lines.extend(
        [
            "",
            f"## Mode trajectories at k={cutoff}",
            "",
            "Values in parentheses are deltas from capture 1.",
            "",
            "| Capture | Mode | Ranking rule | Coverage | Pooled recall | MRR | nDCG | p95 ms |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for capture in campaign.captures:
        for mode, aggregate in capture.modes.items():
            metrics = aggregate.cutoffs[cutoff].metrics
            lines.append(
                f"| {capture.sequence} | {mode} | `{capture.pool.ranking_rules[mode]}` | "
                f"{_metric(aggregate.candidate_coverage)} | "
                f"{_metric(metrics['pooled_recall'])} | "
                f"{_metric(metrics['reciprocal_rank'])} | "
                f"{_metric(metrics['ndcg'])} | "
                f"{_metric(aggregate.latency_p95_ms, digits=2)} |"
            )

    latest = campaign.captures[-1]
    lines.extend(
        [
            "",
            f"## Latest slice movement at k={cutoff}",
            "",
            "| Slice | Mode | Coverage | Pooled recall | MRR | nDCG |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for slice_name, modes in latest.slices.items():
        for mode, aggregate in modes.items():
            metrics = aggregate.cutoffs[cutoff].metrics
            lines.append(
                f"| {slice_name} | {mode} | {_metric(aggregate.candidate_coverage)} | "
                f"{_metric(metrics['pooled_recall'])} | "
                f"{_metric(metrics['reciprocal_rank'])} | {_metric(metrics['ndcg'])} |"
            )

    lines.extend(
        [
            "",
            "## Latest coverage movement",
            "",
            "| Mode | Baseline empty | Latest empty | Resolved | New gaps |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for mode, aggregate in latest.modes.items():
        lines.append(
            f"| {mode} | {_query_ids(aggregate.baseline_empty_query_ids)} | "
            f"{_query_ids(aggregate.candidate_empty_query_ids)} | "
            f"{_query_ids(aggregate.resolved_empty_query_ids)} | "
            f"{_query_ids(aggregate.new_empty_query_ids)} |"
        )

    lines.extend(["", "## Decision boundary", ""])
    lines.extend(f"- {caveat}" for caveat in campaign.caveats)
    return "\n".join(lines) + "\n"

"""Shared-pool longitudinal comparison for reviewed retrieval captures."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from atlas_pulse.evaluation.base import (
    AggregateMetrics,
    CandidatePool,
    CutoffMetricName,
    CutoffMetrics,
    PooledCandidate,
    PooledQuery,
    StrictModel,
)
from atlas_pulse.evaluation.metrics import canonical_sha256, score_pool
from atlas_pulse.retrieval import RankingMode

ComparisonSchemaVersion = Literal["1.0.0"]
_CUTOFF_METRICS: tuple[CutoffMetricName, ...] = (
    "precision",
    "pooled_recall",
    "reciprocal_rank",
    "ndcg",
    "hit_rate",
    "judged_rate",
    "citation_traceability",
)
_CAVEATS = (
    "Both systems are rescored against a shared judged candidate union across both captures, so "
    "pooled-recall and ideal-ranking denominators are identical.",
    "A longitudinal live comparison includes corpus arrivals, revisions, expiry, ingestion state, "
    "and endpoint differences; it isolates ranking changes only when both endpoints read the same "
    "underlying snapshot.",
    "Every delta is candidate minus baseline. Positive quality and coverage deltas are higher; a "
    "positive latency delta is slower.",
    "The comparison fails closed when the query-set identity differs, evidence changes under the "
    "same document ID, or reviewers assign conflicting grades to identical evidence.",
)


class ScalarDelta(StrictModel):
    """One auditable candidate-minus-baseline measurement."""

    baseline: float = Field(allow_inf_nan=False)
    candidate: float = Field(allow_inf_nan=False)
    delta: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_delta(self) -> ScalarDelta:
        if abs(self.delta - (self.candidate - self.baseline)) > 1e-12:
            raise ValueError("delta must equal candidate minus baseline")
        return self


class CutoffComparison(StrictModel):
    """Every standard retrieval metric at one shared cutoff."""

    metrics: dict[CutoffMetricName, ScalarDelta]

    @field_validator("metrics")
    @classmethod
    def validate_complete_metrics(
        cls,
        value: dict[CutoffMetricName, ScalarDelta],
    ) -> dict[CutoffMetricName, ScalarDelta]:
        if set(value) != set(_CUTOFF_METRICS):
            raise ValueError("cutoff comparison must contain every retrieval metric exactly once")
        return value


class AggregateComparison(StrictModel):
    """Mode or slice comparison over one shared judged candidate universe."""

    query_count: int = Field(ge=1)
    candidate_coverage: ScalarDelta
    baseline_empty_query_ids: tuple[str, ...]
    candidate_empty_query_ids: tuple[str, ...]
    resolved_empty_query_ids: tuple[str, ...]
    new_empty_query_ids: tuple[str, ...]
    latency_p50_ms: ScalarDelta
    latency_p95_ms: ScalarDelta
    cutoffs: dict[int, CutoffComparison]

    @model_validator(mode="after")
    def validate_coverage_transitions(self) -> AggregateComparison:
        baseline = set(self.baseline_empty_query_ids)
        candidate = set(self.candidate_empty_query_ids)
        expected_resolved = tuple(sorted(baseline - candidate))
        expected_new = tuple(sorted(candidate - baseline))
        ordered_fields = (
            self.baseline_empty_query_ids,
            self.candidate_empty_query_ids,
            self.resolved_empty_query_ids,
            self.new_empty_query_ids,
        )
        if any(tuple(sorted(set(items))) != items for items in ordered_fields):
            raise ValueError("coverage query IDs must be unique and sorted")
        if self.resolved_empty_query_ids != expected_resolved:
            raise ValueError("resolved empty queries must equal baseline minus candidate empties")
        if self.new_empty_query_ids != expected_new:
            raise ValueError("new empty queries must equal candidate minus baseline empties")
        return self


class ComparedPool(StrictModel):
    """Immutable provenance for one side of a comparison."""

    pool_id: str
    pool_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    endpoint: str
    captured_at: datetime
    reviewer: str
    reviewed_at: datetime
    embedding_models: tuple[str, ...]
    ranking_rules: dict[RankingMode, str]


class EvaluationComparison(StrictModel):
    """Machine-readable before/after retrieval comparison with shared judgments."""

    schema_version: ComparisonSchemaVersion = "1.0.0"
    comparison_id: str
    query_set_id: str
    query_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    capture_gap_seconds: float = Field(allow_inf_nan=False)
    baseline_candidate_count: int = Field(ge=0)
    candidate_candidate_count: int = Field(ge=0)
    overlap_candidate_count: int = Field(ge=0)
    union_candidate_count: int = Field(ge=0)
    baseline: ComparedPool
    candidate: ComparedPool
    modes: dict[RankingMode, AggregateComparison]
    slices: dict[str, dict[RankingMode, AggregateComparison]]
    caveats: tuple[str, ...]

    @model_validator(mode="after")
    def validate_candidate_counts(self) -> EvaluationComparison:
        expected_union = (
            self.baseline_candidate_count
            + self.candidate_candidate_count
            - self.overlap_candidate_count
        )
        if self.union_candidate_count != expected_union:
            raise ValueError("union candidate count must satisfy set inclusion-exclusion")
        if self.overlap_candidate_count > min(
            self.baseline_candidate_count,
            self.candidate_candidate_count,
        ):
            raise ValueError("overlap candidate count cannot exceed either capture")
        return self


def _delta(baseline: float, candidate: float) -> ScalarDelta:
    return ScalarDelta(
        baseline=baseline,
        candidate=candidate,
        delta=candidate - baseline,
    )


def _cutoff_comparison(
    baseline: CutoffMetrics,
    candidate: CutoffMetrics,
) -> CutoffComparison:
    return CutoffComparison(
        metrics={
            name: _delta(getattr(baseline, name), getattr(candidate, name))
            for name in _CUTOFF_METRICS
        }
    )


def compare_aggregate_metrics(
    baseline: AggregateMetrics,
    candidate: AggregateMetrics,
) -> AggregateComparison:
    if baseline.query_count != candidate.query_count:
        raise ValueError("compared aggregates must contain the same number of queries")
    if set(baseline.cutoffs) != set(candidate.cutoffs):
        raise ValueError("compared aggregates must contain identical cutoffs")
    baseline_empty = tuple(sorted(baseline.empty_query_ids))
    candidate_empty = tuple(sorted(candidate.empty_query_ids))
    return AggregateComparison(
        query_count=baseline.query_count,
        candidate_coverage=_delta(
            baseline.candidate_coverage,
            candidate.candidate_coverage,
        ),
        baseline_empty_query_ids=baseline_empty,
        candidate_empty_query_ids=candidate_empty,
        resolved_empty_query_ids=tuple(sorted(set(baseline_empty) - set(candidate_empty))),
        new_empty_query_ids=tuple(sorted(set(candidate_empty) - set(baseline_empty))),
        latency_p50_ms=_delta(baseline.latency_p50_ms, candidate.latency_p50_ms),
        latency_p95_ms=_delta(baseline.latency_p95_ms, candidate.latency_p95_ms),
        cutoffs={
            cutoff: _cutoff_comparison(baseline.cutoffs[cutoff], candidate.cutoffs[cutoff])
            for cutoff in sorted(baseline.cutoffs)
        },
    )


def _validate_reviewed(pool: CandidatePool, *, label: str) -> None:
    if pool.judgment_status != "reviewed" or pool.reviewer is None or pool.reviewed_at is None:
        raise ValueError(f"{label} pool must be fully reviewed")


def _aligned_pools(
    baseline: CandidatePool,
    candidate: CandidatePool,
) -> tuple[CandidatePool, CandidatePool]:
    _validate_reviewed(baseline, label="baseline")
    _validate_reviewed(candidate, label="candidate")
    if (
        baseline.query_set_id != candidate.query_set_id
        or baseline.query_set_sha256 != candidate.query_set_sha256
    ):
        raise ValueError("comparison requires the same query-set ID and SHA-256")

    baseline_queries = {item.query.query_id: item for item in baseline.queries}
    candidate_queries = {item.query.query_id: item for item in candidate.queries}
    if set(baseline_queries) != set(candidate_queries):
        raise ValueError("comparison requires identical query IDs")

    union_by_query: dict[str, tuple[PooledCandidate, ...]] = {}
    for query_id, baseline_query in baseline_queries.items():
        candidate_query = candidate_queries[query_id]
        if baseline_query.query != candidate_query.query:
            raise ValueError(f"query definition changed across captures: {query_id}")
        if {run.mode for run in baseline_query.runs} != {run.mode for run in candidate_query.runs}:
            raise ValueError(f"ranking modes changed across captures: {query_id}")

        baseline_candidates = {item.document_id: item for item in baseline_query.candidates}
        candidate_candidates = {item.document_id: item for item in candidate_query.candidates}
        for document_id in set(baseline_candidates) & set(candidate_candidates):
            baseline_document = baseline_candidates[document_id]
            candidate_document = candidate_candidates[document_id]
            if baseline_document.evidence_identity() != candidate_document.evidence_identity():
                raise ValueError(f"evidence changed under document ID {query_id} / {document_id}")
            if baseline_document.relevance != candidate_document.relevance:
                raise ValueError(f"conflicting relevance grades for {query_id} / {document_id}")

        union = {**baseline_candidates, **candidate_candidates}
        union_by_query[query_id] = tuple(union[key] for key in sorted(union))

    def align(pool: CandidatePool) -> CandidatePool:
        queries = tuple(
            PooledQuery(
                query=pooled_query.query,
                candidates=union_by_query[pooled_query.query.query_id],
                runs=pooled_query.runs,
            )
            for pooled_query in pool.queries
        )
        return CandidatePool.model_validate(
            pool.model_copy(update={"queries": queries}).model_dump(mode="python")
        )

    return align(baseline), align(candidate)


def _rules(pool: CandidatePool) -> dict[RankingMode, str]:
    rules: dict[RankingMode, str] = {}
    for pooled_query in pool.queries:
        for run in pooled_query.runs:
            existing = rules.setdefault(run.mode, run.ranking_rule)
            if existing != run.ranking_rule:
                raise ValueError(f"pool has multiple rules for mode {run.mode}")
    return dict(sorted(rules.items()))


def pool_provenance(pool: CandidatePool) -> ComparedPool:
    """Return immutable reviewed-pool provenance for comparison artifacts."""
    _validate_reviewed(pool, label="candidate")
    assert pool.reviewer is not None
    assert pool.reviewed_at is not None
    return ComparedPool(
        pool_id=pool.pool_id,
        pool_sha256=canonical_sha256(pool),
        endpoint=pool.endpoint,
        captured_at=pool.captured_at,
        reviewer=pool.reviewer,
        reviewed_at=pool.reviewed_at,
        embedding_models=tuple(
            sorted({run.embedding_model for query in pool.queries for run in query.runs})
        ),
        ranking_rules=_rules(pool),
    )


def _candidate_keys(pool: CandidatePool) -> set[tuple[str, str]]:
    return {
        (pooled_query.query.query_id, candidate.document_id)
        for pooled_query in pool.queries
        for candidate in pooled_query.candidates
    }


def compare_pools(
    baseline: CandidatePool,
    candidate: CandidatePool,
    *,
    cutoffs: tuple[int, ...] = (1, 3, 5, 10),
) -> EvaluationComparison:
    """Rescore two reviewed captures against their shared judged candidate union."""
    normalized_cutoffs = tuple(sorted(set(cutoffs)))
    aligned_baseline, aligned_candidate = _aligned_pools(baseline, candidate)
    baseline_report = score_pool(aligned_baseline, cutoffs=normalized_cutoffs)
    candidate_report = score_pool(aligned_candidate, cutoffs=normalized_cutoffs)
    if set(baseline_report.modes) != set(candidate_report.modes):
        raise ValueError("comparison reports contain different ranking modes")
    if set(baseline_report.slices) != set(candidate_report.slices):
        raise ValueError("comparison reports contain different slices")

    baseline_identity = pool_provenance(baseline)
    candidate_identity = pool_provenance(candidate)
    baseline_candidates = _candidate_keys(baseline)
    candidate_candidates = _candidate_keys(candidate)
    digest = canonical_sha256(
        {
            "contract": "shared-reviewed-union-v1",
            "query_set_sha256": baseline.query_set_sha256,
            "baseline_pool_sha256": baseline_identity.pool_sha256,
            "candidate_pool_sha256": candidate_identity.pool_sha256,
            "cutoffs": normalized_cutoffs,
        }
    )
    return EvaluationComparison(
        comparison_id=f"{baseline.query_set_id}-{digest[:12]}",
        query_set_id=baseline.query_set_id,
        query_set_sha256=baseline.query_set_sha256,
        generated_at=datetime.now(UTC),
        capture_gap_seconds=(candidate.captured_at - baseline.captured_at).total_seconds(),
        baseline_candidate_count=len(baseline_candidates),
        candidate_candidate_count=len(candidate_candidates),
        overlap_candidate_count=len(baseline_candidates & candidate_candidates),
        union_candidate_count=len(baseline_candidates | candidate_candidates),
        baseline=baseline_identity,
        candidate=candidate_identity,
        modes={
            mode: compare_aggregate_metrics(
                baseline_report.modes[mode],
                candidate_report.modes[mode],
            )
            for mode in sorted(baseline_report.modes)
        },
        slices={
            slice_name: {
                mode: compare_aggregate_metrics(
                    baseline_report.slices[slice_name][mode],
                    candidate_report.slices[slice_name][mode],
                )
                for mode in sorted(baseline_report.slices[slice_name])
            }
            for slice_name in sorted(baseline_report.slices)
        },
        caveats=_CAVEATS,
    )


def _transition(value: ScalarDelta, *, digits: int = 4) -> str:
    return f"{value.baseline:.{digits}f} → {value.candidate:.{digits}f} ({value.delta:+.{digits}f})"


def _query_ids(values: tuple[str, ...]) -> str:
    return ", ".join(f"`{value}`" for value in values) or "None"


def render_comparison_markdown(comparison: EvaluationComparison) -> str:
    """Render a compact decision record while retaining exact deltas in JSON."""
    cutoff = max(next(iter(comparison.modes.values())).cutoffs)
    lines = [
        f"# Retrieval comparison: {comparison.comparison_id}",
        "",
        f"- Comparison schema: `{comparison.schema_version}`",
        f"- Query set: `{comparison.query_set_id}`",
        f"- Query-set SHA-256: `{comparison.query_set_sha256}`",
        f"- Baseline pool: `{comparison.baseline.pool_id}` (`{comparison.baseline.pool_sha256}`)",
        f"- Candidate pool: `{comparison.candidate.pool_id}` (`{comparison.candidate.pool_sha256}`)",
        f"- Pooled query-document candidates: baseline `{comparison.baseline_candidate_count}`, candidate "
        f"`{comparison.candidate_candidate_count}`, overlap `{comparison.overlap_candidate_count}`, "
        f"union `{comparison.union_candidate_count}`",
        f"- Capture gap: `{comparison.capture_gap_seconds:.3f}` seconds",
        f"- Generated: `{comparison.generated_at.isoformat()}`",
        "",
        f"## Mode deltas at k={cutoff}",
        "",
        "| Mode | Rule baseline → candidate | Coverage | Precision | Pooled recall | MRR | nDCG | Hit rate | p95 ms |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode, aggregate in comparison.modes.items():
        metrics = aggregate.cutoffs[cutoff].metrics
        baseline_rule = comparison.baseline.ranking_rules[mode]
        candidate_rule = comparison.candidate.ranking_rules[mode]
        lines.append(
            f"| {mode} | `{baseline_rule}` → `{candidate_rule}` | "
            f"{_transition(aggregate.candidate_coverage)} | "
            f"{_transition(metrics['precision'])} | {_transition(metrics['pooled_recall'])} | "
            f"{_transition(metrics['reciprocal_rank'])} | {_transition(metrics['ndcg'])} | "
            f"{_transition(metrics['hit_rate'])} | "
            f"{_transition(aggregate.latency_p95_ms, digits=2)} |"
        )

    lines.extend(
        [
            "",
            "## Candidate coverage transitions",
            "",
            "| Mode | Baseline empty | Candidate empty | Resolved | New gaps |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for mode, aggregate in comparison.modes.items():
        lines.append(
            f"| {mode} | {_query_ids(aggregate.baseline_empty_query_ids)} | "
            f"{_query_ids(aggregate.candidate_empty_query_ids)} | "
            f"{_query_ids(aggregate.resolved_empty_query_ids)} | "
            f"{_query_ids(aggregate.new_empty_query_ids)} |"
        )

    lines.extend(
        [
            "",
            f"## Slice deltas at k={cutoff}",
            "",
            "| Slice | Mode | Queries | Coverage | Pooled recall | MRR | nDCG |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for slice_name, modes in comparison.slices.items():
        for mode, aggregate in modes.items():
            metrics = aggregate.cutoffs[cutoff].metrics
            lines.append(
                f"| {slice_name} | {mode} | {aggregate.query_count} | "
                f"{_transition(aggregate.candidate_coverage)} | "
                f"{_transition(metrics['pooled_recall'])} | "
                f"{_transition(metrics['reciprocal_rank'])} | "
                f"{_transition(metrics['ndcg'])} |"
            )

    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {caveat}" for caveat in comparison.caveats)
    return "\n".join(lines) + "\n"

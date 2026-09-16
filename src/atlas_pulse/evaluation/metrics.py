"""Deterministic information-retrieval metrics and regression gates."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from math import log2

from pydantic import BaseModel

from atlas_pulse.evaluation.base import (
    AggregateMetrics,
    CandidatePool,
    CutoffMetrics,
    EvaluationReport,
    GateOutcome,
    GatePolicy,
    PooledCandidate,
    QueryRunMetrics,
)
from atlas_pulse.retrieval import RankingMode

_CAVEATS = (
    "Recall is pooled recall: relevance outside the judged candidate pool is unknown.",
    "Unjudged retrieved documents receive zero gain and reduce judged-rate; they are not proven irrelevant.",
    "Candidate coverage means a mode returned at least one result; it does not prove that an "
    "eligible source corpus existed or that no relevant event existed.",
    "Citation traceability validates link structure and event attachment, not factual truth.",
    "Latency is observed client-side for this capture and is not a universal service-level guarantee.",
)

_ADJUDICATED_CAVEAT = (
    "Relevance grades were finalized from two independent reviews and disagreement-only "
    "adjudication; agreement measures consistency, not factual truth."
)


def canonical_sha256(model: object) -> str:
    """Hash a Pydantic model or JSON-compatible value using canonical JSON."""
    value = (
        model.model_dump(mode="json", exclude_none=False, by_alias=True)
        if isinstance(model, BaseModel)
        else model
    )
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode()).hexdigest()


def _dcg(grades: Sequence[int]) -> float:
    return sum(
        ((2**grade - 1) / log2(rank + 1) for rank, grade in enumerate(grades, start=1)),
        start=0.0,
    )


def metrics_at_k(
    document_ids: Sequence[str],
    candidates: Mapping[str, PooledCandidate],
    *,
    cutoff: int,
) -> CutoffMetrics:
    """Score one ordered run against complete or incomplete pooled judgments."""
    retrieved = tuple(document_ids[:cutoff])
    grades: list[int] = []
    for document_id in retrieved:
        candidate = candidates.get(document_id)
        grades.append(
            candidate.relevance if candidate is not None and candidate.relevance is not None else 0
        )
    relevant_total = sum(
        candidate.relevance is not None and candidate.relevance > 0
        for candidate in candidates.values()
    )
    relevant_retrieved = sum(grade > 0 for grade in grades)
    first_relevant = next((rank for rank, grade in enumerate(grades, start=1) if grade > 0), None)
    ideal_grades = sorted(
        (candidate.relevance or 0 for candidate in candidates.values()),
        reverse=True,
    )[:cutoff]
    ideal_dcg = _dcg(ideal_grades)
    judged = [candidates.get(document_id) for document_id in retrieved]
    traceable = sum(
        candidate is not None and candidate.citation_status == "traceable" for candidate in judged
    )
    return CutoffMetrics(
        precision=relevant_retrieved / cutoff,
        pooled_recall=relevant_retrieved / relevant_total if relevant_total else 0,
        reciprocal_rank=1 / first_relevant if first_relevant is not None else 0,
        ndcg=_dcg(grades) / ideal_dcg if ideal_dcg else 0,
        hit_rate=float(first_relevant is not None),
        judged_rate=(
            sum(candidate is not None and candidate.relevance is not None for candidate in judged)
            / cutoff
        ),
        citation_traceability=traceable / cutoff,
    )


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _average_cutoffs(rows: Sequence[QueryRunMetrics]) -> dict[int, CutoffMetrics]:
    cutoffs = sorted({cutoff for row in rows for cutoff in row.cutoffs})
    result: dict[int, CutoffMetrics] = {}
    for cutoff in cutoffs:
        values = [row.cutoffs[cutoff] for row in rows]
        result[cutoff] = CutoffMetrics(
            precision=sum(value.precision for value in values) / len(values),
            pooled_recall=sum(value.pooled_recall for value in values) / len(values),
            reciprocal_rank=sum(value.reciprocal_rank for value in values) / len(values),
            ndcg=sum(value.ndcg for value in values) / len(values),
            hit_rate=sum(value.hit_rate for value in values) / len(values),
            judged_rate=sum(value.judged_rate for value in values) / len(values),
            citation_traceability=(
                sum(value.citation_traceability for value in values) / len(values)
            ),
        )
    return result


def _aggregate(rows: Sequence[QueryRunMetrics]) -> AggregateMetrics:
    if not rows:
        raise ValueError("cannot aggregate an empty metric group")
    latencies = [row.latency_ms for row in rows]
    empty_query_ids = tuple(sorted(row.query_id for row in rows if row.result_count == 0))
    return AggregateMetrics(
        query_count=len(rows),
        candidate_coverage=(len(rows) - len(empty_query_ids)) / len(rows),
        empty_query_ids=empty_query_ids,
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
        cutoffs=_average_cutoffs(rows),
    )


def score_pool(pool: CandidatePool, *, cutoffs: Iterable[int] = (1, 3, 5, 10)) -> EvaluationReport:
    """Score every captured mode after a reviewer completes the pooled judgments."""
    if pool.judgment_status != "reviewed" or pool.reviewer is None:
        raise ValueError("only a fully reviewed candidate pool can be scored")
    normalized_cutoffs = tuple(sorted(set(cutoffs)))
    if not normalized_cutoffs or normalized_cutoffs[0] < 1 or normalized_cutoffs[-1] > 50:
        raise ValueError("evaluation cutoffs must be values within [1, 50]")

    query_metrics: list[QueryRunMetrics] = []
    embedding_models: set[str] = set()
    for pooled_query in pool.queries:
        candidates = {candidate.document_id: candidate for candidate in pooled_query.candidates}
        for run in pooled_query.runs:
            embedding_models.add(run.embedding_model)
            query_metrics.append(
                QueryRunMetrics(
                    query_id=pooled_query.query.query_id,
                    slices=pooled_query.query.slices,
                    mode=run.mode,
                    ranking_rule=run.ranking_rule,
                    latency_ms=run.latency_ms,
                    result_count=len(run.document_ids),
                    cutoffs={
                        cutoff: metrics_at_k(
                            run.document_ids,
                            candidates,
                            cutoff=cutoff,
                        )
                        for cutoff in normalized_cutoffs
                    },
                )
            )

    by_mode: dict[RankingMode, list[QueryRunMetrics]] = defaultdict(list)
    by_slice: dict[str, dict[RankingMode, list[QueryRunMetrics]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in query_metrics:
        by_mode[row.mode].append(row)
        for slice_name in row.slices:
            by_slice[slice_name][row.mode].append(row)

    pool_hash = canonical_sha256(pool)
    now = datetime.now(UTC)
    return EvaluationReport(
        schema_version="1.2.0" if pool.adjudication is not None else "1.1.0",
        report_id=f"{pool.pool_id}-{pool_hash[:12]}",
        pool_id=pool.pool_id,
        pool_sha256=pool_hash,
        generated_at=now,
        reviewer=pool.reviewer,
        embedding_models=tuple(sorted(embedding_models)),
        query_metrics=tuple(query_metrics),
        modes={mode: _aggregate(rows) for mode, rows in sorted(by_mode.items())},
        slices={
            slice_name: {mode: _aggregate(rows) for mode, rows in sorted(mode_rows.items())}
            for slice_name, mode_rows in sorted(by_slice.items())
        },
        adjudication=pool.adjudication,
        caveats=((*_CAVEATS, _ADJUDICATED_CAVEAT) if pool.adjudication is not None else _CAVEATS),
    )


def evaluate_gates(report: EvaluationReport, policy: GatePolicy) -> tuple[GateOutcome, ...]:
    """Evaluate explicit quality floors and latency ceilings without hidden tolerances."""
    outcomes: list[GateOutcome] = []
    for rule in policy.rules:
        if rule.slice_name is None:
            aggregate = report.modes.get(rule.mode)
        else:
            slice_modes = report.slices.get(rule.slice_name)
            if slice_modes is None:
                raise ValueError(f"report does not contain slice {rule.slice_name}")
            aggregate = slice_modes.get(rule.mode)
        if aggregate is None:
            scope = f" in slice {rule.slice_name}" if rule.slice_name is not None else ""
            raise ValueError(f"report does not contain mode {rule.mode}{scope}")
        if rule.metric == "latency_p95_ms":
            observed = aggregate.latency_p95_ms
        elif rule.metric == "candidate_coverage":
            observed = aggregate.candidate_coverage
        else:
            assert rule.cutoff is not None
            cutoff = aggregate.cutoffs.get(rule.cutoff)
            if cutoff is None:
                raise ValueError(f"report does not contain cutoff {rule.cutoff}")
            observed = getattr(cutoff, rule.metric)
        if rule.minimum is not None:
            passed = observed >= rule.minimum
            comparison = f">= {rule.minimum:g}"
        else:
            assert rule.maximum is not None
            passed = observed <= rule.maximum
            comparison = f"<= {rule.maximum:g}"
        outcomes.append(
            GateOutcome(
                rule_id=rule.rule_id,
                mode=rule.mode,
                slice_name=rule.slice_name,
                metric=rule.metric,
                cutoff=rule.cutoff,
                passed=passed,
                observed=observed,
                comparison=comparison,
            )
        )
    return tuple(outcomes)

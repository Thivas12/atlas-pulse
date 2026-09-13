"""Human-in-the-loop retrieval evaluation public API."""

from atlas_pulse.evaluation.base import (
    AggregateMetrics,
    CandidatePool,
    CapturedRun,
    CutoffMetrics,
    EvaluationFilters,
    EvaluationQuery,
    EvaluationQuerySet,
    EvaluationReport,
    GateOutcome,
    GatePolicy,
    GateRule,
    PooledCandidate,
    PooledQuery,
    QueryRunMetrics,
)
from atlas_pulse.evaluation.capture import capture_pool
from atlas_pulse.evaluation.comparison import (
    AggregateComparison,
    ComparedPool,
    CutoffComparison,
    EvaluationComparison,
    ScalarDelta,
    compare_pools,
    render_comparison_markdown,
)
from atlas_pulse.evaluation.judgments import (
    JudgmentSheet,
    apply_judgments,
    build_judgment_sheet,
    export_judgments,
)
from atlas_pulse.evaluation.metrics import (
    canonical_sha256,
    evaluate_gates,
    metrics_at_k,
    score_pool,
)
from atlas_pulse.evaluation.report import render_markdown

__all__ = [
    "AggregateComparison",
    "AggregateMetrics",
    "CandidatePool",
    "CapturedRun",
    "ComparedPool",
    "CutoffComparison",
    "CutoffMetrics",
    "EvaluationComparison",
    "EvaluationFilters",
    "EvaluationQuery",
    "EvaluationQuerySet",
    "EvaluationReport",
    "GateOutcome",
    "GatePolicy",
    "GateRule",
    "JudgmentSheet",
    "PooledCandidate",
    "PooledQuery",
    "QueryRunMetrics",
    "ScalarDelta",
    "apply_judgments",
    "build_judgment_sheet",
    "canonical_sha256",
    "capture_pool",
    "compare_pools",
    "evaluate_gates",
    "export_judgments",
    "metrics_at_k",
    "render_comparison_markdown",
    "render_markdown",
    "score_pool",
]

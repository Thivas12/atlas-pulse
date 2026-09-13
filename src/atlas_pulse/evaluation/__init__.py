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
from atlas_pulse.evaluation.judgments import apply_judgments, export_judgments
from atlas_pulse.evaluation.metrics import (
    canonical_sha256,
    evaluate_gates,
    metrics_at_k,
    score_pool,
)
from atlas_pulse.evaluation.report import render_markdown

__all__ = [
    "AggregateMetrics",
    "CandidatePool",
    "CapturedRun",
    "CutoffMetrics",
    "EvaluationFilters",
    "EvaluationQuery",
    "EvaluationQuerySet",
    "EvaluationReport",
    "GateOutcome",
    "GatePolicy",
    "GateRule",
    "PooledCandidate",
    "PooledQuery",
    "QueryRunMetrics",
    "apply_judgments",
    "canonical_sha256",
    "capture_pool",
    "evaluate_gates",
    "export_judgments",
    "metrics_at_k",
    "render_markdown",
    "score_pool",
]

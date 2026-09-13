"""Classification, slice, and abstention metrics for reviewed claim pairs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.relationship_evaluation.base import (
    RELATIONSHIP_LABELS,
    LabelMetrics,
    RelationshipCase,
    RelationshipCaseOutcome,
    RelationshipEvaluationReport,
    RelationshipPool,
    RelationshipSliceMetrics,
)
from atlas_pulse.relationships import RelationshipLabel

_BASE_CAVEATS = (
    "Gold labels compare what two public source records explicitly say at the named predicate and scope; they do not establish that either source is true.",
    "Only pairs already linked by the bounded spatiotemporal evidence graph are eligible, so these metrics do not measure missed graph edges or unobserved sources.",
    "Source-pair stable-hash caps create a reviewable benchmark, not an estimate weighted to live source prevalence.",
    "Insufficient evidence is the deployed system's abstention. Decisive coverage must be read with selective accuracy and per-label recall, not optimized alone.",
)


@dataclass(frozen=True)
class RelationshipScoreSet:
    """Reusable metrics over one exact set of reviewed cases and predicted labels."""

    overall: RelationshipSliceMetrics
    predicates: dict[str, RelationshipSliceMetrics]
    source_pairs: dict[str, RelationshipSliceMetrics]
    confusion_matrix: dict[RelationshipLabel, dict[RelationshipLabel, int]]
    outcomes: tuple[RelationshipCaseOutcome, ...]


def _label_metrics(
    cases: Sequence[RelationshipCase],
    predictions: Mapping[str, RelationshipLabel],
    label: RelationshipLabel,
) -> LabelMetrics:
    support = sum(case.gold_label == label for case in cases)
    predicted = sum(predictions[case.case_id] == label for case in cases)
    true_positive = sum(
        case.gold_label == label and predictions[case.case_id] == label for case in cases
    )
    false_positive = predicted - true_positive
    false_negative = support - true_positive
    precision = true_positive / predicted if predicted else None
    recall = true_positive / support if support else None
    if true_positive:
        assert precision is not None and recall is not None
        f1 = 2 * precision * recall / (precision + recall)
    elif support or predicted:
        f1 = 0.0
    else:
        f1 = None
    return LabelMetrics(
        support=support,
        predicted_count=predicted,
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def _slice_metrics(
    cases: Sequence[RelationshipCase],
    predictions: Mapping[str, RelationshipLabel],
) -> RelationshipSliceMetrics:
    if not cases:
        raise ValueError("cannot score an empty relationship slice")
    if any(case.gold_label is None for case in cases):
        raise ValueError("relationship metrics require a gold label for every case")
    correct = sum(predictions[case.case_id] == case.gold_label for case in cases)
    decisive = [case for case in cases if predictions[case.case_id] != "insufficient_evidence"]
    labels = {label: _label_metrics(cases, predictions, label) for label in RELATIONSHIP_LABELS}
    f1_values = [metrics.f1 for metrics in labels.values() if metrics.f1 is not None]
    return RelationshipSliceMetrics(
        case_count=len(cases),
        accuracy=correct / len(cases),
        macro_f1=sum(f1_values) / len(f1_values) if f1_values else None,
        decisive_coverage=len(decisive) / len(cases),
        abstention_rate=(len(cases) - len(decisive)) / len(cases),
        selective_accuracy=(
            sum(predictions[case.case_id] == case.gold_label for case in decisive) / len(decisive)
            if decisive
            else None
        ),
        labels=labels,
    )


def _confusion_matrix(
    cases: Sequence[RelationshipCase],
    predictions: Mapping[str, RelationshipLabel],
) -> dict[RelationshipLabel, dict[RelationshipLabel, int]]:
    return {
        gold: {
            predicted: sum(
                case.gold_label == gold and predictions[case.case_id] == predicted for case in cases
            )
            for predicted in RELATIONSHIP_LABELS
        }
        for gold in RELATIONSHIP_LABELS
    }


def calculate_relationship_scores(
    cases: Sequence[RelationshipCase],
    predictions: Mapping[str, RelationshipLabel],
) -> RelationshipScoreSet:
    """Calculate identical metrics for any exact prediction map over reviewed cases."""
    if not cases:
        raise ValueError("cannot score an empty relationship case set")
    case_ids = {case.case_id for case in cases}
    prediction_ids = set(predictions)
    missing = sorted(case_ids - prediction_ids)
    unexpected = sorted(prediction_ids - case_ids)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append(f"missing {len(missing)} case(s)")
        if unexpected:
            details.append(f"contains {len(unexpected)} unknown case(s)")
        raise ValueError("prediction map " + " and ".join(details))
    invalid_labels = sorted(set(predictions.values()) - set(RELATIONSHIP_LABELS))
    if invalid_labels:
        raise ValueError(f"prediction map contains unknown label(s): {invalid_labels}")
    if any(case.gold_label is None for case in cases):
        raise ValueError("relationship metrics require a gold label for every case")

    by_predicate: dict[str, list[RelationshipCase]] = defaultdict(list)
    by_source_pair: dict[str, list[RelationshipCase]] = defaultdict(list)
    outcomes: list[RelationshipCaseOutcome] = []
    for case in cases:
        predicted = predictions[case.case_id]
        by_predicate[case.predicate].append(case)
        by_source_pair["+".join(case.source_pair)].append(case)
        assert case.gold_label is not None
        outcomes.append(
            RelationshipCaseOutcome(
                case_id=case.case_id,
                edge_id=case.edge_id,
                predicate=case.predicate,
                source_pair=case.source_pair,
                predicted_label=predicted,
                gold_label=case.gold_label,
                correct=predicted == case.gold_label,
            )
        )

    return RelationshipScoreSet(
        overall=_slice_metrics(cases, predictions),
        predicates={
            predicate: _slice_metrics(rows, predictions)
            for predicate, rows in sorted(by_predicate.items())
        },
        source_pairs={
            source_pair: _slice_metrics(rows, predictions)
            for source_pair, rows in sorted(by_source_pair.items())
        },
        confusion_matrix=_confusion_matrix(cases, predictions),
        outcomes=tuple(outcomes),
    )


def score_relationship_pool(pool: RelationshipPool) -> RelationshipEvaluationReport:
    """Score one fully reviewed pool while retaining every case-level outcome."""
    if pool.judgment_status != "reviewed" or pool.reviewer is None:
        raise ValueError("only a fully reviewed relationship pool can be scored")

    predictions = {case.case_id: case.system_prediction.label for case in pool.cases}
    scores = calculate_relationship_scores(pool.cases, predictions)

    pool_hash = canonical_sha256(pool)
    if pool.adjudication is None:
        review_caveat = (
            "A single reviewed pool has no inter-annotator agreement estimate; comparative "
            "public claims require independent review and adjudication."
        )
    else:
        kappa = (
            "undefined"
            if pool.adjudication.cohen_kappa is None
            else f"{pool.adjudication.cohen_kappa:.4f}"
        )
        review_caveat = (
            "Gold labels passed independent review and system-blind adjudication under "
            f"{pool.adjudication.process_version}; observed agreement was "
            f"{pool.adjudication.observed_agreement:.4f} and Cohen's kappa was {kappa}. "
            "Agreement measures consistency, not correctness."
        )
    return RelationshipEvaluationReport(
        schema_version=pool.schema_version,
        report_id=f"{pool.pool_id}-{pool_hash[:12]}",
        pool_id=pool.pool_id,
        pool_sha256=pool_hash,
        generated_at=datetime.now(UTC),
        reviewer=pool.reviewer,
        rubric_version=pool.rubric_version,
        correlation_rule_version=pool.correlation_rule_version,
        relationship_rule_version=pool.relationship_rule_version,
        incident_count=pool.incident_count,
        available_edge_count=pool.available_edge_count,
        sampled_edge_count=pool.sampled_edge_count,
        incidents_truncated=pool.incidents_truncated,
        candidate_edges_truncated=pool.candidate_edges_truncated,
        overall=scores.overall,
        predicates=scores.predicates,
        source_pairs=scores.source_pairs,
        confusion_matrix=scores.confusion_matrix,
        outcomes=scores.outcomes,
        adjudication=pool.adjudication,
        caveats=(*_BASE_CAVEATS, review_caveat),
    )

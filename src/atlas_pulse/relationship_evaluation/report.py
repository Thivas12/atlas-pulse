"""Human-readable rendering for reviewed relationship evaluation reports."""

from atlas_pulse.relationship_evaluation.base import (
    RELATIONSHIP_LABELS,
    RelationshipEvaluationReport,
    RelationshipSliceMetrics,
)


def _metric(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.4f}"


def _slice_row(name: str, metrics: RelationshipSliceMetrics) -> str:
    return (
        f"| {name} | {metrics.case_count} | {_metric(metrics.accuracy)} | "
        f"{_metric(metrics.macro_f1)} | {_metric(metrics.decisive_coverage)} | "
        f"{_metric(metrics.abstention_rate)} | {_metric(metrics.selective_accuracy)} |"
    )


def render_relationship_markdown(report: RelationshipEvaluationReport) -> str:
    """Render headline, slice, confusion, and error details from the machine report."""
    overall = report.overall
    lines = [
        f"# Claim-pair evaluation: {report.report_id}",
        "",
        f"- Pool: `{report.pool_id}`",
        f"- Pool SHA-256: `{report.pool_sha256}`",
        f"- Reviewer: `{report.reviewer}`",
        f"- Generated: `{report.generated_at.isoformat()}`",
        f"- Review rubric: `{report.rubric_version}`",
        f"- Correlation rule: `{report.correlation_rule_version}`",
        f"- Relationship rule: `{report.relationship_rule_version}`",
        f"- Captured incidents: {report.incident_count}",
        f"- Available / sampled edges: {report.available_edge_count} / {report.sampled_edge_count}",
        f"- API truncation: incidents={str(report.incidents_truncated).lower()}, candidate_edges={str(report.candidate_edges_truncated).lower()}",
        "",
        "## Overall",
        "",
        "| Cases | Accuracy | Macro F1 | Decisive coverage | Abstention rate | Selective accuracy |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| {overall.case_count} | {_metric(overall.accuracy)} | {_metric(overall.macro_f1)} | {_metric(overall.decisive_coverage)} | {_metric(overall.abstention_rate)} | {_metric(overall.selective_accuracy)} |",
        "",
        "## Per-label classification",
        "",
        "| Label | Gold support | Predicted | TP | FP | FN | Precision | Recall | F1 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label in RELATIONSHIP_LABELS:
        metrics = overall.labels[label]
        lines.append(
            f"| {label} | {metrics.support} | {metrics.predicted_count} | "
            f"{metrics.true_positive} | {metrics.false_positive} | "
            f"{metrics.false_negative} | {_metric(metrics.precision)} | "
            f"{_metric(metrics.recall)} | {_metric(metrics.f1)} |"
        )

    lines.extend(
        [
            "",
            "## Predicate slices",
            "",
            "| Predicate | Cases | Accuracy | Macro F1 | Decisive coverage | Abstention rate | Selective accuracy |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    lines.extend(_slice_row(name, metrics) for name, metrics in report.predicates.items())
    lines.extend(
        [
            "",
            "## Source-pair slices",
            "",
            "| Source pair | Cases | Accuracy | Macro F1 | Decisive coverage | Abstention rate | Selective accuracy |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    lines.extend(_slice_row(name, metrics) for name, metrics in report.source_pairs.items())

    lines.extend(
        [
            "",
            "## Confusion matrix",
            "",
            "Rows are human gold labels; columns are deployed-system predictions.",
            "",
            "| Gold \\ Predicted | Corroborates | Contradicts | Insufficient evidence |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for gold in RELATIONSHIP_LABELS:
        row = report.confusion_matrix[gold]
        lines.append(
            f"| {gold} | {row['corroborates']} | {row['contradicts']} | "
            f"{row['insufficient_evidence']} |"
        )

    errors = [outcome for outcome in report.outcomes if not outcome.correct]
    lines.extend(
        [
            "",
            "## Error cases",
            "",
            "| Case | Edge | Predicate | Source pair | Predicted | Gold |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    if errors:
        for outcome in errors:
            lines.append(
                f"| `{outcome.case_id}` | `{outcome.edge_id}` | {outcome.predicate} | "
                f"{'+'.join(outcome.source_pair)} | {outcome.predicted_label} | "
                f"{outcome.gold_label} |"
            )
    else:
        lines.append("| None | None | None | None | None | None |")

    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {caveat}" for caveat in report.caveats)
    return "\n".join(lines) + "\n"

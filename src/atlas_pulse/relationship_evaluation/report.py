"""Human-readable rendering for reviewed relationship evaluation reports."""

from atlas_pulse.relationship_evaluation.adjudication import (
    IndependentReviewAgreementReport,
    RelationshipAdjudicationReport,
    ReviewAgreementMetrics,
)
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


def _agreement_row(name: str, metrics: ReviewAgreementMetrics) -> str:
    return (
        f"| {name} | {metrics.case_count} | {metrics.agreement_count} | "
        f"{metrics.disagreement_count} | {_metric(metrics.observed_agreement)} | "
        f"{_metric(metrics.expected_agreement)} | {_metric(metrics.cohen_kappa)} |"
    )


def _markdown_text(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


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
    ]
    if report.adjudication is not None:
        provenance = report.adjudication
        lines.extend(
            [
                f"- Review process: `{provenance.process_version}`",
                "- Independent reviewers: "
                + ", ".join(f"`{reviewer}`" for reviewer in provenance.independent_reviewers),
                f"- Agreement report: `{provenance.agreement_report_id}`",
                f"- Observed agreement / Cohen's kappa: {_metric(provenance.observed_agreement)} / {_metric(provenance.cohen_kappa)}",
                f"- Adjudicated disagreements: {provenance.adjudication_decision_count}",
            ]
        )
    lines.extend(
        [
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
    )
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


def render_review_agreement_markdown(report: IndependentReviewAgreementReport) -> str:
    """Render agreement, chance correction, slices, and exact disagreement IDs."""
    first = report.first_review
    second = report.second_review
    lines = [
        f"# Independent-review agreement: {report.report_id}",
        "",
        f"- Pool: `{report.pool_id}`",
        f"- Capture SHA-256: `{report.capture_sha256}`",
        f"- First reviewer: `{first.reviewer}` (`{first.pool_sha256}`)",
        f"- Second reviewer: `{second.reviewer}` (`{second.pool_sha256}`)",
        f"- Generated: `{report.generated_at.isoformat()}`",
        "",
        "## Overall",
        "",
        "| Cases | Agreements | Disagreements | Observed agreement | Expected agreement | Cohen's kappa |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| {report.overall.case_count} | {report.overall.agreement_count} | {report.overall.disagreement_count} | {_metric(report.overall.observed_agreement)} | {_metric(report.overall.expected_agreement)} | {_metric(report.overall.cohen_kappa)} |",
        "",
        "## Predicate slices",
        "",
        "| Predicate | Cases | Agreements | Disagreements | Observed | Expected | Kappa |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(_agreement_row(name, metrics) for name, metrics in report.predicates.items())
    lines.extend(
        [
            "",
            "## Source-pair slices",
            "",
            "| Source pair | Cases | Agreements | Disagreements | Observed | Expected | Kappa |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    lines.extend(_agreement_row(name, metrics) for name, metrics in report.source_pairs.items())
    lines.extend(
        [
            "",
            "## Reviewer confusion matrix",
            "",
            f"Rows are `{first.reviewer}`; columns are `{second.reviewer}`.",
            "",
            "| First \\ Second | Corroborates | Contradicts | Insufficient evidence |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for first_label in RELATIONSHIP_LABELS:
        row = report.confusion_matrix[first_label]
        lines.append(
            f"| {first_label} | {row['corroborates']} | {row['contradicts']} | "
            f"{row['insufficient_evidence']} |"
        )
    lines.extend(
        [
            "",
            "## Disagreements",
            "",
            "| Case | Edge | Predicate | Source pair | First | Second |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    if report.disagreements:
        for item in report.disagreements:
            lines.append(
                f"| `{item.case_id}` | `{item.edge_id}` | {item.predicate} | "
                f"{'+'.join(item.source_pair)} | {item.first_label} | {item.second_label} |"
            )
    else:
        lines.append("| None | None | None | None | None | None |")
    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {caveat}" for caveat in report.caveats)
    return "\n".join(lines) + "\n"


def render_adjudication_markdown(report: RelationshipAdjudicationReport) -> str:
    """Render final gold-pool provenance and every disagreement decision."""
    lines = [
        f"# Relationship adjudication: {report.report_id}",
        "",
        f"- Pool: `{report.pool_id}`",
        f"- Capture SHA-256: `{report.capture_sha256}`",
        f"- Agreement report: `{report.agreement_report_id}`",
        f"- First reviewer: `{report.first_review.reviewer}` (`{report.first_review.pool_sha256}`)",
        f"- Second reviewer: `{report.second_review.reviewer}` (`{report.second_review.pool_sha256}`)",
        f"- Adjudicator: `{report.adjudicator}`",
        f"- Finalized: `{report.generated_at.isoformat()}`",
        f"- Final pool SHA-256: `{report.final_pool_sha256}`",
        f"- Final gold cases: {report.case_count}",
        f"- Consensus labels inherited: {report.inherited_agreement_count}",
        f"- Disagreements adjudicated: {report.adjudication_decision_count}",
        "",
        "## Decisions",
        "",
        "| Case | Predicate | Source pair | First | Second | Final | Rationale |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    if report.decisions:
        for item in report.decisions:
            lines.append(
                f"| `{item.case_id}` | {item.predicate} | {'+'.join(item.source_pair)} | "
                f"{item.first_label} | {item.second_label} | {item.adjudicated_label} | "
                f"{_markdown_text(item.rationale)} |"
            )
    else:
        lines.append("| None | None | None | None | None | None | No disagreements |")
    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {caveat}" for caveat in report.caveats)
    return "\n".join(lines) + "\n"

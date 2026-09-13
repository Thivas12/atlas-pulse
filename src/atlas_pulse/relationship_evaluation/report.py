"""Human-readable rendering for reviewed relationship evaluation reports."""

import json
from collections.abc import Mapping

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
from atlas_pulse.relationship_evaluation.candidates import (
    RelationshipCandidateComparisonReport,
    RelationshipSliceComparison,
)
from atlas_pulse.relationships import RelationshipLabel


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


def _delta_metric(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.4f}"


def _comparison_row(name: str, comparison: RelationshipSliceComparison) -> str:
    baseline = comparison.baseline
    candidate = comparison.candidate
    delta = comparison.delta
    return (
        f"| {name} | {baseline.case_count} | "
        f"{_metric(baseline.accuracy)} → {_metric(candidate.accuracy)} "
        f"({_delta_metric(delta.accuracy)}) | "
        f"{_metric(baseline.macro_f1)} → {_metric(candidate.macro_f1)} "
        f"({_delta_metric(delta.macro_f1)}) | "
        f"{_metric(baseline.decisive_coverage)} → {_metric(candidate.decisive_coverage)} "
        f"({_delta_metric(delta.decisive_coverage)}) | "
        f"{_metric(baseline.selective_accuracy)} → {_metric(candidate.selective_accuracy)} "
        f"({_delta_metric(delta.selective_accuracy)}) |"
    )


def _confusion_lines(
    matrix: Mapping[RelationshipLabel, Mapping[RelationshipLabel, int]],
) -> list[str]:
    lines = [
        "| Gold \\ Predicted | Corroborates | Contradicts | Insufficient evidence |",
        "| --- | ---: | ---: | ---: |",
    ]
    for gold in RELATIONSHIP_LABELS:
        row = matrix[gold]
        lines.append(
            f"| {gold} | {row['corroborates']} | {row['contradicts']} | "
            f"{row['insufficient_evidence']} |"
        )
    return lines


def render_candidate_comparison_markdown(
    report: RelationshipCandidateComparisonReport,
) -> str:
    """Render a paired candidate comparison with an explicit closed release boundary."""
    overall = report.overall
    baseline = overall.baseline
    candidate = overall.candidate
    delta = overall.delta
    system = report.candidate_system
    paired = report.paired_outcomes
    latency = report.candidate_latency
    parameters = json.dumps(system.parameters, sort_keys=True, separators=(",", ":"))
    lines = [
        f"# Candidate relationship comparison: {report.report_id}",
        "",
        "> **Promotion status: BLOCKED.** This artifact measures a candidate; it cannot authorize a production relationship rule.",
        "",
        f"- Gold pool: `{report.gold_pool_id}` (`{report.gold_pool_sha256}`)",
        f"- Gold-blind task: `{report.task_id}` (`{report.task_sha256}`)",
        f"- Prediction batch: `{report.prediction_batch_id}` (`{report.prediction_batch_sha256}`)",
        f"- Generated: `{report.generated_at.isoformat()}`",
        f"- Human review process: `{report.adjudication.process_version}`",
        "- Independent reviewers: "
        + ", ".join(f"`{reviewer}`" for reviewer in report.adjudication.independent_reviewers),
        f"- Adjudicator: `{report.adjudication.adjudicator}`",
        f"- Observed agreement / Cohen's kappa: {_metric(report.adjudication.observed_agreement)} / {_metric(report.adjudication.cohen_kappa)}",
        f"- Baseline relationship rule: `{report.baseline_relationship_rule_version}`",
        f"- Candidate: `{system.candidate_id}` via `{system.adapter_version}`",
        f"- Model: `{system.model_id}` at immutable revision `{system.model_revision}`",
        f"- Model artifact SHA-256: `{system.model_artifact_sha256}`",
        f"- Input template SHA-256: `{system.input_template_sha256}`",
        f"- Runtime: `{system.runtime}` `{system.runtime_version}`",
        f"- Inference parameters: `{_markdown_text(parameters)}`",
        "",
        "## Paired headline",
        "",
        "Candidate deltas are candidate minus the captured deployed rule. A positive abstention delta means more abstention, not an automatic improvement.",
        "",
        "| Metric | Baseline | Candidate | Delta |",
        "| --- | ---: | ---: | ---: |",
        f"| Accuracy | {_metric(baseline.accuracy)} | {_metric(candidate.accuracy)} | {_delta_metric(delta.accuracy)} |",
        f"| Macro F1 | {_metric(baseline.macro_f1)} | {_metric(candidate.macro_f1)} | {_delta_metric(delta.macro_f1)} |",
        f"| Decisive coverage | {_metric(baseline.decisive_coverage)} | {_metric(candidate.decisive_coverage)} | {_delta_metric(delta.decisive_coverage)} |",
        f"| Abstention rate | {_metric(baseline.abstention_rate)} | {_metric(candidate.abstention_rate)} | {_delta_metric(delta.abstention_rate)} |",
        f"| Selective accuracy | {_metric(baseline.selective_accuracy)} | {_metric(candidate.selective_accuracy)} | {_delta_metric(delta.selective_accuracy)} |",
        "",
        "## Per-label classification",
        "",
        "| Label | Gold | Baseline P / R / F1 | Candidate P / R / F1 | Baseline → candidate predicted |",
        "| --- | ---: | --- | --- | ---: |",
    ]
    for label in RELATIONSHIP_LABELS:
        baseline_label = baseline.labels[label]
        candidate_label = candidate.labels[label]
        lines.append(
            f"| {label} | {baseline_label.support} | "
            f"{_metric(baseline_label.precision)} / {_metric(baseline_label.recall)} / {_metric(baseline_label.f1)} | "
            f"{_metric(candidate_label.precision)} / {_metric(candidate_label.recall)} / {_metric(candidate_label.f1)} | "
            f"{baseline_label.predicted_count} → {candidate_label.predicted_count} |"
        )

    lines.extend(
        [
            "",
            "## Predicate slices",
            "",
            "| Predicate | Cases | Accuracy baseline → candidate (Δ) | Macro F1 baseline → candidate (Δ) | Decisive coverage baseline → candidate (Δ) | Selective accuracy baseline → candidate (Δ) |",
            "| --- | ---: | --- | --- | --- | --- |",
        ]
    )
    lines.extend(_comparison_row(name, value) for name, value in report.predicates.items())
    lines.extend(
        [
            "",
            "## Source-pair slices",
            "",
            "| Source pair | Cases | Accuracy baseline → candidate (Δ) | Macro F1 baseline → candidate (Δ) | Decisive coverage baseline → candidate (Δ) | Selective accuracy baseline → candidate (Δ) |",
            "| --- | ---: | --- | --- | --- | --- |",
        ]
    )
    lines.extend(_comparison_row(name, value) for name, value in report.source_pairs.items())

    lines.extend(
        [
            "",
            "## Paired outcomes",
            "",
            "| Cases | Improvements | Regressions | Unchanged correct | Unchanged incorrect | Changed but incorrect | Label disagreements |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            f"| {paired.case_count} | {paired.improvements} | {paired.regressions} | {paired.unchanged_correct} | {paired.unchanged_incorrect} | {paired.changed_incorrect} | {paired.label_disagreements} |",
            "",
            "## Baseline confusion matrix",
            "",
            "Rows are adjudicated gold labels; columns are captured deployed-rule predictions.",
            "",
            *_confusion_lines(report.baseline_confusion_matrix),
            "",
            "## Candidate confusion matrix",
            "",
            "Rows are adjudicated gold labels; columns are external candidate predictions.",
            "",
            *_confusion_lines(report.candidate_confusion_matrix),
            "",
            "## Prediction transitions",
            "",
            "Rows are captured deployed-rule predictions; columns are candidate predictions.",
            "",
            "| Baseline \\ Candidate | Corroborates | Contradicts | Insufficient evidence |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for baseline_prediction in RELATIONSHIP_LABELS:
        row = report.prediction_transition_matrix[baseline_prediction]
        lines.append(
            f"| {baseline_prediction} | {row['corroborates']} | {row['contradicts']} | "
            f"{row['insufficient_evidence']} |"
        )

    lines.extend(
        [
            "",
            "## Candidate latency",
            "",
            "| Cases | Total ms | Mean ms | p50 ms | p95 ms | Max ms |",
            "| ---: | ---: | ---: | ---: | ---: | ---: |",
            f"| {latency.case_count} | {latency.total_ms:.3f} | {latency.mean_ms:.3f} | {latency.p50_ms:.3f} | {latency.p95_ms:.3f} | {latency.max_ms:.3f} |",
            "",
            "## Changed or incorrect cases",
            "",
            "| Case | Edge | Predicate | Source pair | Baseline | Candidate | Gold | Paired outcome |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    notable = [outcome for outcome in report.outcomes if outcome.outcome != "unchanged_correct"]
    if notable:
        for outcome in notable:
            lines.append(
                f"| `{outcome.case_id}` | `{outcome.edge_id}` | {outcome.predicate} | "
                f"{'+'.join(outcome.source_pair)} | {outcome.baseline_label} | "
                f"{outcome.candidate_label} | {outcome.gold_label} | {outcome.outcome} |"
            )
    else:
        lines.append("| None | None | None | None | None | None | None | None |")

    lines.extend(["", "## Promotion blockers", ""])
    lines.extend(f"- {blocker}" for blocker in report.promotion_blockers)
    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {caveat}" for caveat in report.caveats)
    return "\n".join(lines) + "\n"


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

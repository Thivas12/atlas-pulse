"""Readable reports for descriptive grounded-answer evaluation evidence."""

from __future__ import annotations

from atlas_pulse.grounded_answer_evaluation.adjudication import (
    GroundedAnswerAdjudicationReport,
    IndependentGroundedAnswerReviewAgreementReport,
)
from atlas_pulse.grounded_answer_evaluation.metrics import (
    GroundedAnswerEvaluationReport,
    GroundedAnswerMetricSummary,
)


def _number(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _summary_row(name: str, value: GroundedAnswerMetricSummary) -> str:
    return (
        f"| {name} | {value.case_count} | {value.answered_case_count} | "
        f"{value.abstained_case_count} | {value.claim_count} | "
        f"{_number(value.mean_support_grade_0_to_3)} | "
        f"{_percent(value.fully_supported_claim_rate)} | "
        f"{_number(value.mean_citation_quality_0_to_2)} | "
        f"{_percent(value.complete_citation_rate)} | "
        f"{_number(value.mean_answer_relevance_0_to_2)} | "
        f"{_percent(value.appropriate_abstention_rate)} | "
        f"{_percent(value.strict_case_pass_rate)} | {value.p95_latency_ms:.3f} |"
    )


def render_grounded_answer_markdown(report: GroundedAnswerEvaluationReport) -> str:
    """Render first-pass or adjudicated metrics, cases, and non-promotion boundary."""
    if report.adjudication is None:
        review_lines = [f"- First-pass review: `{report.review_id}` by {report.reviewer}"]
    else:
        provenance = report.adjudication
        review_lines = [
            f"- Final adjudicated review: `{report.review_id}` by {report.reviewer}",
            "- Independent reviews: "
            + ", ".join(f"`{review_id}`" for review_id in provenance.independent_review_ids),
            f"- Agreement report: `{provenance.agreement_report_id}`",
            f"- Disputed rows / fields resolved: {provenance.adjudication_decision_count} / "
            f"{provenance.adjudicated_field_count}",
        ]
    lines = [
        "# Grounded-answer candidate review",
        "",
        f"- Report: `{report.report_id}`",
        f"- Task: `{report.task_id}`",
        f"- Candidate batch: `{report.batch_id}`",
        *review_lines,
        f"- Candidate: `{report.system.candidate_id}`",
        "- Promotion status: **BLOCKED**",
        "",
        "## Rubric",
        "",
        "- Support: 0 contradicted, 1 unsupported, 2 partially supported, 3 fully supported.",
        "- Citation quality: 0 irrelevant/contrary, 1 partial, 2 complete/direct support.",
        "- Answer relevance: 0 off-topic, 1 partial, 2 directly answers the question.",
        "- Strict case pass is descriptive and is not a release threshold.",
        "",
        "## Overall",
        "",
        "| Scope | Cases | Answered | Abstained | Claims | Mean support /3 | Fully supported | Mean citation /2 | Complete citation | Mean relevance /2 | Appropriate abstention | Strict case pass | p95 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        _summary_row("All", report.overall),
        "",
        "Token and latency measurements are candidate-supplied and descriptive: "
        f"mean input `{report.overall.mean_input_tokens:.1f}` tokens, mean output "
        f"`{report.overall.mean_output_tokens:.1f}` tokens, mean latency "
        f"`{report.overall.mean_latency_ms:.3f}` ms.",
        "",
        "## Declared slices",
        "",
        "| Scope | Cases | Answered | Abstained | Claims | Mean support /3 | Fully supported | Mean citation /2 | Complete citation | Mean relevance /2 | Appropriate abstention | Strict case pass | p95 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(_summary_row(name, value) for name, value in report.slices.items())
    lines.extend(
        [
            "",
            "## Case outcomes",
            "",
            "| Case | Query | Status | Claims | Min support | Min citation | Relevance | Abstention appropriate | Strict pass |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for outcome in report.outcomes:
        lines.append(
            f"| `{outcome.case_id}` | `{outcome.query_id}` | {outcome.response_status} | "
            f"{outcome.claim_count} | "
            f"{outcome.minimum_support_grade if outcome.minimum_support_grade is not None else 'n/a'} | "
            f"{outcome.minimum_citation_quality if outcome.minimum_citation_quality is not None else 'n/a'} | "
            f"{outcome.answer_relevance if outcome.answer_relevance is not None else 'n/a'} | "
            f"{outcome.abstention_appropriate if outcome.abstention_appropriate is not None else 'n/a'} | "
            f"{'yes' if outcome.strict_pass else 'no'} |"
        )
    lines.extend(["", "## Promotion blockers", ""])
    lines.extend(f"- {item}" for item in report.promotion_blockers)
    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {item}" for item in report.caveats)
    return "\n".join(lines) + "\n"


def render_grounded_answer_agreement_markdown(
    report: IndependentGroundedAnswerReviewAgreementReport,
) -> str:
    """Render independent-review agreement by rubric dimension."""
    lines = [
        "# Grounded-answer independent-review agreement",
        "",
        f"- Report: `{report.report_id}`",
        f"- Task / batch: `{report.task_id}` / `{report.batch_id}`",
        f"- First review: `{report.first_review.review_id}` by {report.first_review.reviewer}",
        f"- Second review: `{report.second_review.review_id}` by {report.second_review.reviewer}",
        "- Promotion status: **BLOCKED**",
        "",
        "## Judgment rows",
        "",
        "| Judgments | Complete agreement | Disputed rows | Disputed fields | Exact row agreement |",
        "| ---: | ---: | ---: | ---: | ---: |",
        f"| {report.judgments.judgment_count} | "
        f"{report.judgments.complete_agreement_count} | "
        f"{report.judgments.disagreement_count} | "
        f"{report.judgments.disputed_field_count} | "
        f"{report.judgments.exact_judgment_agreement:.1%} |",
        "",
        "## Rubric-field agreement",
        "",
        "| Field | Ratings | Agreements | Disagreements | Observed | Expected | Cohen's kappa |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for dimension, metrics in report.dimensions.items():
        kappa = "n/a" if metrics.cohen_kappa is None else f"{metrics.cohen_kappa:.3f}"
        lines.append(
            f"| `{dimension}` | {metrics.rating_count} | {metrics.agreement_count} | "
            f"{metrics.disagreement_count} | {metrics.observed_agreement:.1%} | "
            f"{metrics.expected_agreement:.1%} | {kappa} |"
        )
    lines.extend(
        [
            "",
            "## Disputed rows",
            "",
            "| Case | Claim | Fields |",
            "| --- | --- | --- |",
        ]
    )
    if report.disagreements:
        for disagreement in report.disagreements:
            lines.append(
                f"| `{disagreement.case_id}` | `{disagreement.claim_id or 'abstention'}` | "
                f"{', '.join(f'`{field}`' for field in disagreement.disputed_fields)} |"
            )
    else:
        lines.append("| None | None | No disagreements |")
    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {item}" for item in report.caveats)
    return "\n".join(lines) + "\n"


def render_grounded_answer_adjudication_markdown(
    report: GroundedAnswerAdjudicationReport,
) -> str:
    """Render finalized decision provenance without turning it into a release verdict."""
    lines = [
        "# Grounded-answer adjudication",
        "",
        f"- Report: `{report.report_id}`",
        f"- Agreement report: `{report.agreement_report_id}`",
        f"- Final review: `{report.final_review_id}`",
        f"- Adjudicator: {report.adjudicator}",
        "- Promotion status: **BLOCKED**",
        f"- Consensus rows inherited: {report.inherited_agreement_count}",
        f"- Disputed rows / fields resolved: {report.adjudication_decision_count} / "
        f"{report.adjudicated_field_count}",
        "",
        "## Decisions",
        "",
        "| Case | Claim | Resolved fields | Final rationale |",
        "| --- | --- | --- | --- |",
    ]
    if report.decisions:
        for decision in report.decisions:
            rationale = decision.final_judgment.rationale.replace("|", "\\|")
            lines.append(
                f"| `{decision.case_id}` | `{decision.claim_id or 'abstention'}` | "
                f"{', '.join(f'`{field}`' for field in decision.disputed_fields)} | "
                f"{rationale} |"
            )
    else:
        lines.append("| None | None | No disagreements | Independent consensus |")
    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {item}" for item in report.caveats)
    return "\n".join(lines) + "\n"

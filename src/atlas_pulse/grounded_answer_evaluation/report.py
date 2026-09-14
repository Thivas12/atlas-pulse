"""Readable reports for descriptive grounded-answer evaluation evidence."""

from __future__ import annotations

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
    """Render the first-pass metrics, cases, and non-promotion boundary."""
    lines = [
        "# Grounded-answer candidate review",
        "",
        f"- Report: `{report.report_id}`",
        f"- Task: `{report.task_id}`",
        f"- Candidate batch: `{report.batch_id}`",
        f"- First-pass review: `{report.review_id}` by {report.reviewer}",
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

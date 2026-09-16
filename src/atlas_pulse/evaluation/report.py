"""Human-readable rendering for retrieval evaluation and review reports."""

from atlas_pulse.evaluation.adjudication import (
    IndependentRetrievalReviewAgreementReport,
    RetrievalAdjudicationReport,
    RetrievalReviewAgreementMetrics,
)
from atlas_pulse.evaluation.base import EvaluationReport, GateOutcome


def _metric(value: float) -> str:
    return f"{value:.4f}"


def _kappa(value: float | None) -> str:
    return "N/A" if value is None else _metric(value)


def render_markdown(
    report: EvaluationReport,
    *,
    gates: tuple[GateOutcome, ...] | None = None,
) -> str:
    """Render a compact auditable report without dropping machine-readable detail."""
    active_gates = report.gate_outcomes if gates is None else gates
    lines = [
        f"# Retrieval evaluation: {report.report_id}",
        "",
        f"- Report schema: `{report.schema_version}`",
        f"- Pool: `{report.pool_id}`",
        f"- Pool SHA-256: `{report.pool_sha256}`",
        f"- Reviewer: `{report.reviewer}`",
        f"- Generated: `{report.generated_at.isoformat()}`",
        f"- Embedding model(s): `{', '.join(report.embedding_models)}`",
        *(
            [
                "- Review process: `independent-review-adjudication-v1`",
                "- Independent reviewers: "
                + ", ".join(
                    f"`{reviewer}`" for reviewer in report.adjudication.independent_reviewers
                ),
                f"- Agreement report: `{report.adjudication.agreement_report_id}`",
                f"- Observed agreement: `{_metric(report.adjudication.observed_agreement)}`",
                "- Cohen's kappa: "
                + (
                    "`N/A`"
                    if report.adjudication.cohen_kappa is None
                    else f"`{_metric(report.adjudication.cohen_kappa)}`"
                ),
                f"- Adjudicated disagreements: `{report.adjudication.adjudication_decision_count}`",
            ]
            if report.adjudication is not None
            else []
        ),
        *(
            [f"- Gate policy: `{report.gate_policy_id}`"]
            if report.gate_policy_id is not None
            else []
        ),
        "",
        "## Mode comparison",
        "",
        "| Mode | Coverage | k | Precision | Pooled recall | MRR | nDCG | Hit rate | Judged | Citation | p50 ms | p95 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode, aggregate in report.modes.items():
        for cutoff, metrics in aggregate.cutoffs.items():
            lines.append(
                f"| {mode} | {_metric(aggregate.candidate_coverage)} | {cutoff} | "
                f"{_metric(metrics.precision)} | "
                f"{_metric(metrics.pooled_recall)} | {_metric(metrics.reciprocal_rank)} | "
                f"{_metric(metrics.ndcg)} | {_metric(metrics.hit_rate)} | "
                f"{_metric(metrics.judged_rate)} | {_metric(metrics.citation_traceability)} | "
                f"{aggregate.latency_p50_ms:.2f} | {aggregate.latency_p95_ms:.2f} |"
            )

    lines.extend(
        [
            "",
            "## Candidate coverage gaps",
            "",
            "| Mode | Covered queries | Empty query IDs |",
            "| --- | ---: | --- |",
        ]
    )
    for mode, aggregate in report.modes.items():
        covered = aggregate.query_count - len(aggregate.empty_query_ids)
        empty = ", ".join(f"`{query_id}`" for query_id in aggregate.empty_query_ids) or "None"
        lines.append(f"| {mode} | {covered}/{aggregate.query_count} | {empty} |")

    lines.extend(
        [
            "",
            "## Slice comparison",
            "",
            "| Slice | Mode | Queries | Coverage | k | Precision | Pooled recall | MRR | nDCG | Hit rate | Citation |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for slice_name, modes in report.slices.items():
        for mode, aggregate in modes.items():
            cutoff = max(aggregate.cutoffs)
            metrics = aggregate.cutoffs[cutoff]
            lines.append(
                f"| {slice_name} | {mode} | {aggregate.query_count} | "
                f"{_metric(aggregate.candidate_coverage)} | {cutoff} | "
                f"{_metric(metrics.precision)} | {_metric(metrics.pooled_recall)} | "
                f"{_metric(metrics.reciprocal_rank)} | {_metric(metrics.ndcg)} | "
                f"{_metric(metrics.hit_rate)} | {_metric(metrics.citation_traceability)} |"
            )

    if active_gates:
        lines.extend(
            [
                "",
                "## Regression gates",
                "",
                "| Rule | Scope | Metric | Result | Observed | Required |",
                "| --- | --- | --- | --- | ---: | --- |",
            ]
        )
        for gate in active_gates:
            status = "PASS" if gate.passed else "FAIL"
            scope = gate.slice_name or "overall"
            metric = f"{gate.metric}@{gate.cutoff}" if gate.cutoff is not None else gate.metric
            lines.append(
                f"| `{gate.rule_id}` | {scope} / {gate.mode} | {metric} | {status} | "
                f"{_metric(gate.observed)} | {gate.comparison} |"
            )

    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {caveat}" for caveat in report.caveats)
    return "\n".join(lines) + "\n"


def _agreement_row(scope: str, metrics: RetrievalReviewAgreementMetrics) -> str:
    return (
        f"| {scope} | {metrics.judgment_count} | {metrics.agreement_count} | "
        f"{metrics.disagreement_count} | {_metric(metrics.observed_agreement)} | "
        f"{_metric(metrics.expected_agreement)} | {_kappa(metrics.cohen_kappa)} |"
    )


def render_retrieval_review_agreement_markdown(
    report: IndependentRetrievalReviewAgreementReport,
) -> str:
    """Render independent relevance-review agreement and every disagreement."""
    lines = [
        f"# Retrieval review agreement: {report.report_id}",
        "",
        f"- Pool: `{report.pool_id}`",
        f"- Capture SHA-256: `{report.capture_sha256}`",
        f"- First reviewer: `{report.first_review.reviewer}`",
        f"- First review SHA-256: `{report.first_review.pool_sha256}`",
        f"- Second reviewer: `{report.second_review.reviewer}`",
        f"- Second review SHA-256: `{report.second_review.pool_sha256}`",
        f"- Generated: `{report.generated_at.isoformat()}`",
        f"- Promotion status: `{report.promotion_status}`",
        "",
        "## Agreement summary",
        "",
        "| Scope | Judgments | Agreements | Disagreements | Observed | Expected | Cohen's kappa |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        _agreement_row("Overall", report.overall),
    ]
    for query_id, metrics in report.queries.items():
        lines.append(_agreement_row(f"Query `{query_id}`", metrics))
    for source, metrics in report.sources.items():
        lines.append(_agreement_row(f"Source `{source}`", metrics))
    for slice_name, metrics in report.slices.items():
        lines.append(_agreement_row(f"Slice `{slice_name}`", metrics))

    lines.extend(
        [
            "",
            "## Relevance confusion matrix",
            "",
            "Rows are the canonically ordered first review; columns are the second review.",
            "",
            "| First \\ Second | 0 | 1 | 2 | 3 |",
            "| ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for first_grade, row in report.confusion_matrix.items():
        lines.append(f"| {first_grade} | {row[0]} | {row[1]} | {row[2]} | {row[3]} |")

    lines.extend(["", "## Disagreements", ""])
    if report.disagreements:
        lines.extend(
            [
                "| Query | Document | Source | First | Second |",
                "| --- | --- | --- | ---: | ---: |",
            ]
        )
        for item in report.disagreements:
            lines.append(
                f"| `{item.query_id}` | `{item.document_id}` | {item.source} | "
                f"{item.first_relevance} | {item.second_relevance} |"
            )
    else:
        lines.append("No relevance-grade disagreements were found.")

    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {caveat}" for caveat in report.caveats)
    return "\n".join(lines) + "\n"


def render_retrieval_adjudication_markdown(report: RetrievalAdjudicationReport) -> str:
    """Render the final gold-pool provenance and third-person decisions."""
    lines = [
        f"# Retrieval adjudication: {report.report_id}",
        "",
        f"- Agreement report: `{report.agreement_report_id}`",
        f"- Pool: `{report.pool_id}`",
        f"- Capture SHA-256: `{report.capture_sha256}`",
        f"- First reviewer: `{report.first_review.reviewer}`",
        f"- First review SHA-256: `{report.first_review.pool_sha256}`",
        f"- Second reviewer: `{report.second_review.reviewer}`",
        f"- Second review SHA-256: `{report.second_review.pool_sha256}`",
        f"- Adjudicator: `{report.adjudicator}`",
        f"- Generated: `{report.generated_at.isoformat()}`",
        f"- Final pool SHA-256: `{report.final_pool_sha256}`",
        f"- Judgments: `{report.judgment_count}`",
        f"- Inherited agreements: `{report.inherited_agreement_count}`",
        f"- Adjudicated disagreements: `{report.adjudication_decision_count}`",
        f"- Promotion status: `{report.promotion_status}`",
        "",
        "## Decisions",
        "",
    ]
    if report.decisions:
        lines.extend(
            [
                "| Query | Document | Source | First | Second | Final | Rationale |",
                "| --- | --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for decision in report.decisions:
            lines.append(
                f"| `{decision.query_id}` | `{decision.document_id}` | {decision.source} | "
                f"{decision.first_relevance} | {decision.second_relevance} | "
                f"{decision.adjudicated_relevance} | {decision.rationale} |"
            )
    else:
        lines.append("No disagreements required adjudication.")
    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {caveat}" for caveat in report.caveats)
    return "\n".join(lines) + "\n"

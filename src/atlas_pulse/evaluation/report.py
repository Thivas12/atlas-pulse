"""Human-readable rendering for retrieval evaluation reports."""

from atlas_pulse.evaluation.base import EvaluationReport, GateOutcome


def _metric(value: float) -> str:
    return f"{value:.4f}"


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

"""Human-readable trajectory, drift, and release-assessment reports."""

from atlas_pulse.agent_trajectory.drift import AgentTrajectoryDriftReport
from atlas_pulse.agent_trajectory.release import AgentReleaseAssessment
from atlas_pulse.agent_trajectory.scoring import (
    AgentTrajectoryMetricSummary,
    AgentTrajectoryScoreReport,
)


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _summary_row(name: str, value: AgentTrajectoryMetricSummary) -> str:
    return (
        f"| {name} | {value.case_count} | {_percent(value.policy_compliance_rate)} | "
        f"{_percent(value.evidence_inspection_rate)} | {_percent(value.claim_trace_rate)} | "
        f"{_percent(value.strict_trajectory_pass_rate)} | "
        f"{_percent(value.grounded_strict_pass_rate)} | "
        f"{_percent(value.strict_end_to_end_pass_rate)} | {value.p95_step_count:.1f} | "
        f"{value.p95_latency_ms:.1f} |"
    )


def render_agent_trajectory_markdown(report: AgentTrajectoryScoreReport) -> str:
    """Render one observable trajectory score with its closed release boundary."""
    lines = [
        f"# Agent trajectory score: {report.report_id}",
        "",
        "> **Promotion status: BLOCKED.** This report cannot authorize model or agent execution.",
        "",
        f"- Candidate: `{report.system.candidate_id}`",
        f"- Benchmark: `{report.benchmark_id}`",
        f"- Task: `{report.task_id}` (`{report.task_sha256}`)",
        f"- Candidate batch: `{report.candidate_batch_id}`",
        f"- Grounded report: `{report.grounded_report_id}` (independently adjudicated)",
        f"- Trajectory batch: `{report.trajectory_batch_id}`",
        f"- Captured: `{report.captured_at.isoformat()}`",
        f"- Scored: `{report.generated_at.isoformat()}`",
        "",
        "## Headline and slices",
        "",
        "| Slice | Cases | Policy | Evidence inspected | Claims traced | Trajectory pass | Grounded pass | End-to-end pass | p95 steps | p95 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        _summary_row("overall", report.overall),
    ]
    lines.extend(_summary_row(name, value) for name, value in report.slices.items())
    lines.extend(
        [
            "",
            "## Case outcomes",
            "",
            "| Case | Status | Steps | Inspect | Claim trace | Policy | Trajectory | Grounded | End to end | Latency ms |",
            "| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- | ---: |",
        ]
    )
    for outcome in report.outcomes:
        lines.append(
            f"| `{outcome.case_id}` | {outcome.response_status} | {outcome.step_count} | "
            f"{_percent(outcome.evidence_inspection_rate)} | "
            f"{_percent(outcome.claim_trace_rate)} | "
            f"{'pass' if outcome.policy_compliant else 'fail'} | "
            f"{'pass' if outcome.strict_trajectory_pass else 'fail'} | "
            f"{'pass' if outcome.grounded_strict_pass else 'fail'} | "
            f"{'pass' if outcome.strict_end_to_end_pass else 'fail'} | "
            f"{outcome.latency_ms:.1f} |"
        )
    lines.extend(["", "## Boundaries", ""])
    lines.extend(f"- {caveat}" for caveat in report.caveats)
    return "\n".join(lines) + "\n"


def render_agent_trajectory_drift_markdown(report: AgentTrajectoryDriftReport) -> str:
    """Render an inspectable baseline-to-current trajectory drift decision."""
    lines = [
        f"# Agent trajectory drift: {report.drift_report_id}",
        "",
        f"> **Drift status: {report.status.upper().replace('_', ' ')}.** Execution remains disabled.",
        "",
        f"- Candidate: `{report.system.candidate_id}`",
        f"- Baseline: `{report.baseline_report_id}` at `{report.baseline_captured_at.isoformat()}`",
        f"- Current: `{report.current_report_id}` at `{report.current_captured_at.isoformat()}`",
        "",
        "| Metric | Baseline | Current | Deterioration | Maximum | Result |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for check in report.checks:
        deterioration = "undefined" if check.deterioration is None else f"{check.deterioration:.6f}"
        lines.append(
            f"| {check.metric} | {check.baseline:.6f} | {check.current:.6f} | "
            f"{deterioration} | {check.maximum_deterioration:.6f} | "
            f"{'pass' if check.passed else 'fail'} |"
        )
    lines.extend(["", "## Boundaries", ""])
    lines.extend(f"- {caveat}" for caveat in report.caveats)
    return "\n".join(lines) + "\n"


def render_agent_release_markdown(assessment: AgentReleaseAssessment) -> str:
    """Render a quality-threshold assessment without implying execution authorization."""
    label = assessment.status.upper().replace("_", " ")
    lines = [
        f"# Agent release assessment: {assessment.assessment_id}",
        "",
        f"> **Quality status: {label}.** Human approval is still required and execution is hard-disabled.",
        "",
        f"- Policy: `{assessment.policy.policy_id}` (`{assessment.policy.policy_sha256}`)",
        f"- Agent candidate: `{assessment.agent_system.candidate_id}`",
        f"- Relationship candidate: `{assessment.relationship_system.candidate_id}`",
        f"- Relationship report: `{assessment.relationship_evidence.report_id}`",
        f"- Trajectory captures: `{len(assessment.trajectory_evidence)}`",
        f"- Consecutive drift reports: `{len(assessment.drift_evidence)}`",
        f"- Gates: `{assessment.passed_gate_count}` passed / `{assessment.blocked_gate_count}` blocked",
        "",
        "## Threshold gates",
        "",
        "| Gate | Observed | Required | Result | Block reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for gate in assessment.gates:
        lines.append(
            f"| {gate.gate_id} | {gate.observed} | {gate.required} | "
            f"{'pass' if gate.passed else 'blocked'} | {gate.blocking_reason or '—'} |"
        )
    lines.extend(["", "## Boundaries", ""])
    lines.extend(f"- {caveat}" for caveat in assessment.caveats)
    return "\n".join(lines) + "\n"

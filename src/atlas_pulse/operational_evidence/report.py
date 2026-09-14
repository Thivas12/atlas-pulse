"""Human-readable output for sampled operational evidence campaigns."""

from atlas_pulse.operational_evidence.base import DeploymentTarget
from atlas_pulse.operational_evidence.campaign import OperationalEvidenceReport


def _display_seconds(value: float | None) -> str:
    if value is None:
        return "not observed"
    if value < 0:
        return f"{value:.1f} (future clock skew)"
    return f"{value:.1f}"


def render_operational_report(
    target: DeploymentTarget,
    report: OperationalEvidenceReport,
) -> str:
    """Render sampled evidence without converting it into an availability claim."""
    if report.target_id != target.target_id or report.target_sha256 != target.target_sha256:
        raise ValueError("operational report does not match the deployment target")
    label = report.status.upper().replace("_", " ")
    lines = [
        f"# AtlasPulse operational evidence: {report.report_id}",
        "",
        f"> **Evidence status: {label}.** Sampled observations only; this is not an SLA.",
        "",
        f"- Public origin: `{target.origin}`",
        f"- Reviewed commit: `{target.commit_sha}`",
        f"- Application version: `{target.application_version}`",
        f"- Deployment target: `{target.target_id}`",
        f"- Evidence window: `{report.window_start or 'not started'}` to "
        f"`{report.window_end or 'not started'}`",
        f"- Represented span: `{report.calendar_span_days}` calendar day(s)",
        f"- Collected artifacts: `{report.evidence_count}`",
        f"- Sampled public probes: `{report.passing_probe_count}/{report.probe_count}` passed "
        f"({report.sampled_probe_success_rate * 100:.2f}%)",
        f"- Longest passing-probe streak: `{report.longest_passing_probe_streak_days}` UTC day(s)",
        f"- Longest aligned probe/TLS/resource streak: "
        f"`{report.longest_aligned_sample_streak_days}` UTC day(s)",
        "",
        "## Minimum evidence checks",
        "",
        "| Requirement | Observed | Required | Result |",
        "| --- | --- | --- | --- |",
    ]
    for requirement in report.requirements:
        result = "pass" if requirement.passed else f"blocked: {requirement.blocking_reason}"
        lines.append(
            f"| {requirement.requirement_id} | {requirement.observed} | "
            f"{requirement.required} | {result} |"
        )
    lines.extend(
        [
            "",
            "## Public endpoint samples",
            "",
            "| Endpoint | Passed samples | p95 successful latency (ms) |",
            "| --- | ---: | ---: |",
        ]
    )
    for endpoint in report.endpoint_latency:
        latency = (
            "not observed"
            if endpoint.p95_passed_latency_ms is None
            else f"{endpoint.p95_passed_latency_ms:.1f}"
        )
        lines.append(
            f"| {endpoint.name} | {endpoint.passed_count}/{endpoint.observation_count} | "
            f"{latency} |"
        )
    lines.extend(
        [
            "",
            "## Bounded event visibility",
            "",
            "| Source | Visible probes | Event observations | p95 age (s) | Maximum age (s) | Clock skew |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for source in report.source_visibility:
        lines.append(
            f"| {source.source} | {source.visible_probe_count}/{source.probe_count} | "
            f"{source.event_observation_count} | "
            f"{_display_seconds(source.p95_visibility_age_seconds)} | "
            f"{_display_seconds(source.maximum_visibility_age_seconds)} | "
            f"{source.future_clock_skew_count} |"
        )
    lines.extend(
        [
            "",
            "## Drill and durability observations",
            "",
            f"- Passing resource snapshots: `{report.passing_resource_snapshot_count}/"
            f"{report.resource_snapshot_count}` across `{report.resource_snapshot_days}` UTC day(s)",
            f"- Probe/TLS/resource alignment: `{report.aligned_probe_resource_days}` UTC day(s)",
            f"- Passing restart recoveries: `{report.passing_restart_recovery_count}/"
            f"{report.restart_recovery_count}`",
            f"- Encrypted off-host backups observed: `{report.encrypted_off_host_backup_count}`",
            f"- Passing encrypted off-host restores: `{report.passing_restore_drill_count}/"
            f"{report.restore_drill_count}`",
            f"- Minimum observed certificate lifetime remaining: "
            f"`{_display_seconds(report.minimum_certificate_remaining_seconds)} seconds`",
            "",
            "## Claim boundary",
            "",
        ]
    )
    lines.extend(f"- {caveat}" for caveat in report.caveats)
    return "\n".join(lines) + "\n"

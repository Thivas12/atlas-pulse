"""Cross-validated summaries for longitudinal operational evidence campaigns."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import Field, model_validator

from atlas_pulse.evaluation.base import StrictModel
from atlas_pulse.operational_evidence.base import (
    PROBE_PATHS,
    BackupEvidence,
    DeploymentProbeEvidence,
    DeploymentTarget,
    OperationalEvidence,
    ResourceSnapshotEvidence,
    RestartRecoveryEvidence,
    RestoreDrillEvidence,
    _model_sha256,
    _require_utc,
)
from atlas_pulse.projections.base import SourceName

OPERATIONAL_CAMPAIGN_RULE_VERSION = "sampled-30-day-operational-campaign-v2"
OPERATIONAL_REPORT_CAVEATS = (
    "The success rate describes only collected samples. It is not continuous monitoring, an SLA, "
    "or proof of availability between observations.",
    "Thirty consecutively sampled UTC dates are a minimum evidence set, not continuous monitoring "
    "between samples or proof of production-scale capacity.",
    "The checked revision is operator-supplied image build metadata, not an independent "
    "reproducible-build or hardware attestation.",
    "Source freshness is sampled from worker-written Valkey state using the API host clock; it "
    "is not independent uptime monitoring or proof of upstream completeness.",
    "Bounded event visibility is reported separately from polling freshness because unchanged "
    "source responses may be deduplicated before publication.",
    "Off-host and encryption labels are operator assertions; a successful isolated restore binds "
    "the tested bytes but is not an independent disaster-recovery certification.",
    "Operational evidence cannot authorize model or agent execution. Human approval and the "
    "execution-release gate remain separate, and execution is disabled.",
)

RequirementId = Literal[
    "calendar_span_30_days",
    "passing_probe_30_days",
    "certificate_probe_30_days",
    "resource_snapshot_30_days",
    "required_sources_fresh_30_days",
    "restart_recovery_passed",
    "encrypted_off_host_backup",
    "restore_drill_passed",
]
REQUIREMENT_IDS: tuple[RequirementId, ...] = (
    "calendar_span_30_days",
    "certificate_probe_30_days",
    "encrypted_off_host_backup",
    "passing_probe_30_days",
    "required_sources_fresh_30_days",
    "resource_snapshot_30_days",
    "restart_recovery_passed",
    "restore_drill_passed",
)


class EvidenceReference(StrictModel):
    """Small immutable reference to one independently content-addressed observation."""

    evidence_id: str = Field(
        pattern=(
            r"^(deployment-probe|resource-snapshot|restart-recovery|backup-evidence|"
            r"restore-drill)-[0-9a-f]{20}$"
        )
    )
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal[
        "deployment_probe",
        "resource_snapshot",
        "restart_recovery",
        "backup",
        "restore_drill",
    ]
    observed_at: datetime

    @model_validator(mode="after")
    def validate_time(self) -> EvidenceReference:
        _require_utc(self.observed_at, "evidence reference observed_at")
        return self


class EndpointLatencySummary(StrictModel):
    """Descriptive successful-sample latency for one fixed endpoint."""

    name: Literal["health", "readiness", "source_freshness", "events", "agent_preflight"]
    observation_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    p95_passed_latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_counts(self) -> EndpointLatencySummary:
        if self.passed_count > self.observation_count:
            raise ValueError("endpoint passes cannot exceed observations")
        if (self.passed_count == 0) != (self.p95_passed_latency_ms is None):
            raise ValueError("endpoint p95 exists exactly when a passed sample exists")
        return self


class SourceVisibilitySummary(StrictModel):
    """Descriptive public-event visibility for one source across collected probes."""

    source: SourceName
    probe_count: int = Field(ge=0)
    visible_probe_count: int = Field(ge=0)
    event_observation_count: int = Field(ge=0)
    future_clock_skew_count: int = Field(ge=0)
    p95_visibility_age_seconds: float | None = Field(default=None, allow_inf_nan=False)
    maximum_visibility_age_seconds: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_summary(self) -> SourceVisibilitySummary:
        if self.visible_probe_count > self.probe_count:
            raise ValueError("visible source probes cannot exceed all probes")
        if self.future_clock_skew_count > self.visible_probe_count:
            raise ValueError("source clock-skew count cannot exceed visible probes")
        missing = self.visible_probe_count == 0
        if missing != (
            self.p95_visibility_age_seconds is None and self.maximum_visibility_age_seconds is None
        ):
            raise ValueError("source ages exist exactly when a visible sample exists")
        return self


class SourceFreshnessSummary(StrictModel):
    """Sampled worker-heartbeat and upstream-age evidence for one source."""

    source: SourceName
    probe_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    passing_observation_count: int = Field(ge=0)
    passing_day_count: int = Field(ge=0)
    longest_passing_streak_days: int = Field(ge=0)
    poll_healthy_count: int = Field(ge=0)
    source_current_count: int = Field(ge=0)
    maximum_consecutive_failures: int = Field(ge=0)
    last_success_age_observation_count: int = Field(ge=0)
    p95_last_success_age_seconds: float | None = Field(default=None, allow_inf_nan=False)
    source_age_observation_count: int = Field(ge=0)
    p95_source_age_seconds: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_summary(self) -> SourceFreshnessSummary:
        if self.observation_count > self.probe_count:
            raise ValueError("source freshness observations cannot exceed probes")
        if any(
            value > self.observation_count
            for value in (
                self.passing_observation_count,
                self.poll_healthy_count,
                self.source_current_count,
                self.last_success_age_observation_count,
                self.source_age_observation_count,
            )
        ):
            raise ValueError("source freshness aggregates cannot exceed observations")
        if self.longest_passing_streak_days > self.passing_day_count:
            raise ValueError("source freshness streak cannot exceed passing days")
        if (self.last_success_age_observation_count == 0) != (
            self.p95_last_success_age_seconds is None
        ):
            raise ValueError("last-success p95 exists exactly when an age sample exists")
        if (self.source_age_observation_count == 0) != (self.p95_source_age_seconds is None):
            raise ValueError("source-age p95 exists exactly when an age sample exists")
        return self


class OperationalRequirement(StrictModel):
    """One exact minimum-evidence check without a reliability interpretation."""

    requirement_id: RequirementId
    observed: str = Field(min_length=1, max_length=160)
    required: str = Field(min_length=1, max_length=160)
    passed: bool
    blocking_reason: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_result(self) -> OperationalRequirement:
        if self.passed == (self.blocking_reason is not None):
            raise ValueError("only failed requirements may contain a blocking reason")
        return self


class OperationalEvidenceReport(StrictModel):
    """Content-addressed sampled campaign summary with an explicitly limited claim."""

    schema_version: Literal["1.1.0"] = "1.1.0"
    rule_version: Literal["sampled-30-day-operational-campaign-v2"] = (
        "sampled-30-day-operational-campaign-v2"
    )
    identity_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    report_id: str = Field(pattern=r"^operational-report-[0-9a-f]{20}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_id: str = Field(pattern=r"^deployment-target-[0-9a-f]{20}$")
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    window_start: datetime | None = None
    window_end: datetime | None = None
    calendar_span_days: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    probe_count: int = Field(ge=0)
    passing_probe_count: int = Field(ge=0)
    sampled_probe_success_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    passing_probe_days: int = Field(ge=0)
    longest_passing_probe_streak_days: int = Field(ge=0)
    certificate_probe_days: int = Field(ge=0)
    longest_certificate_streak_days: int = Field(ge=0)
    resource_snapshot_count: int = Field(ge=0)
    passing_resource_snapshot_count: int = Field(ge=0)
    resource_snapshot_days: int = Field(ge=0)
    aligned_probe_resource_days: int = Field(ge=0)
    longest_aligned_sample_streak_days: int = Field(ge=0)
    restart_recovery_count: int = Field(ge=0)
    passing_restart_recovery_count: int = Field(ge=0)
    encrypted_off_host_backup_count: int = Field(ge=0)
    restore_drill_count: int = Field(ge=0)
    passing_restore_drill_count: int = Field(ge=0)
    minimum_certificate_remaining_seconds: float | None = Field(default=None, allow_inf_nan=False)
    endpoint_latency: tuple[EndpointLatencySummary, ...] = Field(min_length=5, max_length=5)
    source_visibility: tuple[SourceVisibilitySummary, ...]
    source_freshness: tuple[SourceFreshnessSummary, ...]
    requirements: tuple[OperationalRequirement, ...] = Field(min_length=8, max_length=8)
    passed_requirement_count: int = Field(ge=0, le=8)
    blocked_requirement_count: int = Field(ge=0, le=8)
    status: Literal["insufficient_evidence", "minimum_observation_set_complete"]
    claim_scope: Literal["sampled_observations_not_an_sla"] = "sampled_observations_not_an_sla"
    evidence: tuple[EvidenceReference, ...]
    execution_enabled: Literal[False] = False
    caveats: tuple[str, ...] = OPERATIONAL_REPORT_CAVEATS

    @model_validator(mode="after")
    def validate_report(self) -> OperationalEvidenceReport:
        generated = _require_utc(self.generated_at, "generated_at")
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("operational report window must be fully present or absent")
        if self.evidence:
            assert self.window_start is not None
            assert self.window_end is not None
            _require_utc(self.window_start, "window_start")
            _require_utc(self.window_end, "window_end")
            if self.window_start > self.window_end or generated < self.window_end:
                raise ValueError("operational report times must be chronological")
            if (
                self.window_start != self.evidence[0].observed_at
                or self.window_end != self.evidence[-1].observed_at
            ):
                raise ValueError("operational report window must match its evidence references")
            expected_span = (self.window_end.date() - self.window_start.date()).days + 1
            if self.calendar_span_days != expected_span:
                raise ValueError("operational report span must match its evidence window")
        elif self.window_start is not None or self.calendar_span_days != 0:
            raise ValueError("empty operational reports cannot have a window")
        if self.evidence_count != len(self.evidence):
            raise ValueError("operational evidence count must match references")
        order = tuple((item.observed_at, item.kind, item.evidence_id) for item in self.evidence)
        if order != tuple(sorted(set(order))):
            raise ValueError("operational evidence references must be unique and ordered")
        identifiers = tuple(item.evidence_id for item in self.evidence)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("operational evidence reference IDs must be unique")
        reference_counts = {
            kind: sum(item.kind == kind for item in self.evidence)
            for kind in (
                "deployment_probe",
                "resource_snapshot",
                "restart_recovery",
                "backup",
                "restore_drill",
            )
        }
        if (
            self.probe_count != reference_counts["deployment_probe"]
            or self.resource_snapshot_count != reference_counts["resource_snapshot"]
            or self.restart_recovery_count != reference_counts["restart_recovery"]
            or self.restore_drill_count != reference_counts["restore_drill"]
            or self.encrypted_off_host_backup_count > reference_counts["backup"]
        ):
            raise ValueError("operational aggregate counts must match evidence reference kinds")
        if self.passing_probe_count > self.probe_count:
            raise ValueError("passing probes cannot exceed all probes")
        expected_rate = self.passing_probe_count / self.probe_count if self.probe_count else 0.0
        if abs(self.sampled_probe_success_rate - expected_rate) > 1e-12:
            raise ValueError("sampled probe success rate must match counts")
        if self.passing_resource_snapshot_count > self.resource_snapshot_count:
            raise ValueError("passing resource snapshots cannot exceed all snapshots")
        if self.passing_restart_recovery_count > self.restart_recovery_count:
            raise ValueError("passing restart recoveries cannot exceed all recoveries")
        if self.passing_restore_drill_count > self.restore_drill_count:
            raise ValueError("passing restore drills cannot exceed all drills")
        if (
            self.passing_probe_days > self.passing_probe_count
            or self.longest_passing_probe_streak_days > self.passing_probe_days
            or self.certificate_probe_days > self.probe_count
            or self.longest_certificate_streak_days > self.certificate_probe_days
            or self.resource_snapshot_days > self.passing_resource_snapshot_count
            or self.aligned_probe_resource_days
            > min(
                self.passing_probe_days,
                self.certificate_probe_days,
                self.resource_snapshot_days,
            )
            or self.longest_aligned_sample_streak_days > self.aligned_probe_resource_days
        ):
            raise ValueError("operational day and streak counts are internally inconsistent")
        if tuple(item.name for item in self.endpoint_latency) != tuple(PROBE_PATHS):
            raise ValueError("endpoint summaries must use the complete canonical order")
        if any(item.observation_count != self.probe_count for item in self.endpoint_latency):
            raise ValueError("endpoint observations must match the report probe count")
        source_order = tuple(item.source for item in self.source_visibility)
        if source_order != tuple(sorted(set(source_order))):
            raise ValueError("source summaries must be unique and ordered")
        if any(item.probe_count != self.probe_count for item in self.source_visibility):
            raise ValueError("source observations must match the report probe count")
        freshness_order = tuple(item.source for item in self.source_freshness)
        if freshness_order != tuple(sorted(set(freshness_order))):
            raise ValueError("source freshness summaries must be unique and ordered")
        if any(item.probe_count != self.probe_count for item in self.source_freshness):
            raise ValueError("source freshness summaries must match the report probe count")
        if tuple(item.requirement_id for item in self.requirements) != REQUIREMENT_IDS:
            raise ValueError("operational requirements must use the complete canonical order")
        passed = sum(item.passed for item in self.requirements)
        if self.passed_requirement_count != passed or self.blocked_requirement_count != 8 - passed:
            raise ValueError("operational requirement counts must match checks")
        expected_status = (
            "minimum_observation_set_complete" if passed == 8 else "insufficient_evidence"
        )
        if self.status != expected_status:
            raise ValueError("operational report status must match every requirement")
        if self.execution_enabled is not False or self.caveats != OPERATIONAL_REPORT_CAVEATS:
            raise ValueError("operational reports must retain the closed execution boundary")
        expected = _model_sha256(self, identity_fields={"report_id", "report_sha256"})
        if (
            self.report_sha256 != expected
            or self.report_id != f"operational-report-{expected[:20]}"
        ):
            raise ValueError("operational report identity must match its exact content")
        return self


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = 0.95 * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _longest_streak(days: set[date]) -> int:
    longest = 0
    current = 0
    previous: date | None = None
    for value in sorted(days):
        current = (
            current + 1 if previous is not None and value == previous + timedelta(days=1) else 1
        )
        longest = max(longest, current)
        previous = value
    return longest


def _reference(value: OperationalEvidence) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=value.evidence_id,
        evidence_sha256=value.evidence_sha256,
        kind=value.kind,
        observed_at=value.observed_at,
    )


def _requirement(
    requirement_id: RequirementId,
    *,
    observed: str,
    required: str,
    passed: bool,
    blocking_reason: str,
) -> OperationalRequirement:
    return OperationalRequirement(
        requirement_id=requirement_id,
        observed=observed,
        required=required,
        passed=passed,
        blocking_reason=None if passed else blocking_reason,
    )


def _validate_evidence_links(
    target: DeploymentTarget,
    evidence: tuple[OperationalEvidence, ...],
) -> None:
    identifiers = [item.evidence_id for item in evidence]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("operational evidence IDs must be unique")
    for item in evidence:
        if item.target_id != target.target_id or item.target_sha256 != target.target_sha256:
            raise ValueError("operational evidence must match the exact deployment target")
        if item.observed_at < target.deployed_at:
            raise ValueError("operational evidence cannot precede target deployment")
        if isinstance(item, DeploymentProbeEvidence) and (
            item.expected_version != target.application_version
            or item.expected_commit_sha != target.commit_sha
        ):
            raise ValueError("deployment probe expectations must match exact target metadata")
        if isinstance(item, DeploymentProbeEvidence):
            freshness_endpoint = next(
                endpoint for endpoint in item.endpoints if endpoint.name == "source_freshness"
            )
            freshness_by_source = {
                observation.source: observation for observation in item.source_freshness
            }
            required_fresh = all(
                source in freshness_by_source and freshness_by_source[source].passed
                for source in target.required_sources
            )
            if freshness_endpoint.passed != required_fresh:
                raise ValueError(
                    "source-freshness endpoint must match the target's required sources"
                )
        if (
            isinstance(item, ResourceSnapshotEvidence)
            and item.required_services != target.required_services
        ):
            raise ValueError("resource evidence must retain the target's required services")

    probes = {
        item.evidence_id: item for item in evidence if isinstance(item, DeploymentProbeEvidence)
    }
    backups = {item.evidence_id: item for item in evidence if isinstance(item, BackupEvidence)}
    for item in evidence:
        if isinstance(item, RestartRecoveryEvidence):
            before = probes.get(item.before_probe_id)
            after = probes.get(item.after_probe_id)
            if before is None or after is None:
                raise ValueError("restart evidence references a probe outside the campaign")
            if (
                before.observed_at > item.restart_started_at
                or after.observed_at != item.ready_observed_at
                or before.passed != item.before_probe_passed
                or after.passed != item.after_probe_passed
            ):
                raise ValueError("restart evidence does not match its exact campaign probes")
        elif isinstance(item, RestoreDrillEvidence):
            backup = backups.get(item.backup_evidence_id)
            if backup is None:
                raise ValueError("restore evidence references a backup outside the campaign")
            if (
                backup.file_sha256 != item.backup_file_sha256
                or item.restored_commit_sha != target.commit_sha
                or item.started_at < backup.observed_at
            ):
                raise ValueError("restore evidence does not match its backup or deployment commit")


def evaluate_operational_campaign(
    target: DeploymentTarget,
    evidence: tuple[OperationalEvidence, ...],
    *,
    generated_at: datetime | None = None,
) -> OperationalEvidenceReport:
    """Cross-check exact observations and build a conservative sampled 30-day report."""
    _validate_evidence_links(target, evidence)
    timestamp = _require_utc(generated_at or datetime.now(UTC), "generated_at")
    ordered = tuple(
        sorted(evidence, key=lambda item: (item.observed_at, item.kind, item.evidence_id))
    )
    if ordered and timestamp < ordered[-1].observed_at:
        raise ValueError("report generation cannot precede its latest evidence")

    probes = tuple(item for item in ordered if isinstance(item, DeploymentProbeEvidence))
    resources = tuple(item for item in ordered if isinstance(item, ResourceSnapshotEvidence))
    restarts = tuple(item for item in ordered if isinstance(item, RestartRecoveryEvidence))
    backups = tuple(item for item in ordered if isinstance(item, BackupEvidence))
    restores = tuple(item for item in ordered if isinstance(item, RestoreDrillEvidence))

    represented_dates = {item.observed_at.date() for item in ordered}
    span = (max(represented_dates) - min(represented_dates)).days + 1 if represented_dates else 0
    passing_probe_dates = {item.observed_at.date() for item in probes if item.passed}
    certificate_dates = {item.observed_at.date() for item in probes if item.certificate.passed}
    resource_dates = {item.observed_at.date() for item in resources if item.passed}
    aligned_sample_dates = passing_probe_dates & certificate_dates & resource_dates
    passing_probe_streak = _longest_streak(passing_probe_dates)
    certificate_streak = _longest_streak(certificate_dates)
    aligned_sample_streak = _longest_streak(aligned_sample_dates)

    endpoint_summaries = tuple(
        EndpointLatencySummary(
            name=name,
            observation_count=len(probes),
            passed_count=sum(
                next(endpoint for endpoint in probe.endpoints if endpoint.name == name).passed
                for probe in probes
            ),
            p95_passed_latency_ms=_p95(
                [
                    endpoint.latency_ms
                    for probe in probes
                    for endpoint in probe.endpoints
                    if endpoint.name == name and endpoint.passed
                ]
            ),
        )
        for name in PROBE_PATHS
    )

    observed_sources = {item.source for probe in probes for item in probe.source_visibility} | set(
        target.required_sources
    )
    source_summaries: list[SourceVisibilitySummary] = []
    for source in sorted(observed_sources):
        samples = [
            item for probe in probes for item in probe.source_visibility if item.source == source
        ]
        ages = [item.age_seconds for item in samples]
        source_summaries.append(
            SourceVisibilitySummary(
                source=source,
                probe_count=len(probes),
                visible_probe_count=len(samples),
                event_observation_count=sum(item.event_count for item in samples),
                future_clock_skew_count=sum(value < 0 for value in ages),
                p95_visibility_age_seconds=_p95(ages),
                maximum_visibility_age_seconds=max(ages) if ages else None,
            )
        )

    freshness_sources = {item.source for probe in probes for item in probe.source_freshness} | set(
        target.required_sources
    )
    source_freshness_summaries: list[SourceFreshnessSummary] = []
    source_freshness_streaks: dict[SourceName, int] = {}
    probes_by_date: dict[date, list[DeploymentProbeEvidence]] = {}
    for probe in probes:
        probes_by_date.setdefault(probe.observed_at.date(), []).append(probe)
    for source in sorted(freshness_sources):
        freshness_samples = [
            item for probe in probes for item in probe.source_freshness if item.source == source
        ]
        passing_dates = {
            observed_date
            for observed_date, dated_probes in probes_by_date.items()
            if all(
                any(item.source == source and item.passed for item in probe.source_freshness)
                for probe in dated_probes
            )
        }
        streak = _longest_streak(passing_dates)
        source_freshness_streaks[source] = streak
        last_success_ages = [
            item.last_success_age_seconds
            for item in freshness_samples
            if item.last_success_age_seconds is not None
        ]
        source_ages = [
            item.source_age_seconds
            for item in freshness_samples
            if item.source_age_seconds is not None
        ]
        source_freshness_summaries.append(
            SourceFreshnessSummary(
                source=source,
                probe_count=len(probes),
                observation_count=len(freshness_samples),
                passing_observation_count=sum(item.passed for item in freshness_samples),
                passing_day_count=len(passing_dates),
                longest_passing_streak_days=streak,
                poll_healthy_count=sum(item.poll_status == "healthy" for item in freshness_samples),
                source_current_count=sum(
                    item.source_data_status == "current" for item in freshness_samples
                ),
                maximum_consecutive_failures=max(
                    (item.consecutive_failures for item in freshness_samples), default=0
                ),
                last_success_age_observation_count=len(last_success_ages),
                p95_last_success_age_seconds=_p95(last_success_ages),
                source_age_observation_count=len(source_ages),
                p95_source_age_seconds=_p95(source_ages),
            )
        )
    required_sources_passed = all(
        source_freshness_streaks.get(source, 0) >= 30 for source in target.required_sources
    )
    encrypted_off_host = tuple(
        item
        for item in backups
        if item.storage_scope == "off_host_verified" and item.encrypted_at_rest
    )
    encrypted_off_host_ids = {item.evidence_id for item in encrypted_off_host}
    passing_restores = tuple(
        item
        for item in restores
        if item.passed and item.backup_evidence_id in encrypted_off_host_ids
    )
    requirements = tuple(
        sorted(
            (
                _requirement(
                    "calendar_span_30_days",
                    observed=f"{span} represented calendar day(s)",
                    required="at least 30 calendar days from first to last evidence",
                    passed=span >= 30,
                    blocking_reason="campaign window is shorter than 30 calendar days",
                ),
                _requirement(
                    "passing_probe_30_days",
                    observed=(
                        f"{len(passing_probe_dates)} UTC day(s); longest streak "
                        f"{passing_probe_streak} day(s)"
                    ),
                    required="a passing public probe on 30 consecutive UTC dates",
                    passed=passing_probe_streak >= 30,
                    blocking_reason="no 30-day passing public-probe streak is present",
                ),
                _requirement(
                    "certificate_probe_30_days",
                    observed=(
                        f"{len(certificate_dates)} UTC day(s); longest streak "
                        f"{certificate_streak} day(s)"
                    ),
                    required="a verified HTTPS certificate on 30 consecutive UTC dates",
                    passed=certificate_streak >= 30,
                    blocking_reason="no 30-day verified-TLS streak is present",
                ),
                _requirement(
                    "resource_snapshot_30_days",
                    observed=(
                        f"{len(resource_dates)} resource day(s); {len(aligned_sample_dates)} aligned "
                        f"day(s); longest aligned streak {aligned_sample_streak} day(s)"
                    ),
                    required="passing probe, TLS, and resource samples on 30 consecutive UTC dates",
                    passed=aligned_sample_streak >= 30,
                    blocking_reason="no aligned 30-day probe/TLS/resource streak is present",
                ),
                _requirement(
                    "required_sources_fresh_30_days",
                    observed=(
                        ", ".join(
                            f"{source}={source_freshness_streaks.get(source, 0)} day(s)"
                            for source in target.required_sources
                        )
                    ),
                    required="every required source fresh on 30 consecutive UTC dates",
                    passed=required_sources_passed,
                    blocking_reason=("one or more required sources lack a 30-day freshness streak"),
                ),
                _requirement(
                    "restart_recovery_passed",
                    observed=f"{sum(item.passed for item in restarts)} passing drill(s)",
                    required="at least one passing restart-recovery drill",
                    passed=any(item.passed for item in restarts),
                    blocking_reason="no passing restart-recovery drill is present",
                ),
                _requirement(
                    "encrypted_off_host_backup",
                    observed=f"{len(encrypted_off_host)} backup(s)",
                    required="at least one encrypted off-host backup observation",
                    passed=bool(encrypted_off_host),
                    blocking_reason="no encrypted off-host backup observation is present",
                ),
                _requirement(
                    "restore_drill_passed",
                    observed=f"{len(passing_restores)} passing drill(s)",
                    required="restore the same encrypted off-host backup in isolation",
                    passed=bool(passing_restores),
                    blocking_reason=(
                        "no passing isolated restore of an encrypted off-host backup is present"
                    ),
                ),
            ),
            key=lambda item: item.requirement_id,
        )
    )
    passed_requirement_count = sum(item.passed for item in requirements)
    certificate_remaining = [
        probe.certificate.remaining_seconds
        for probe in probes
        if probe.certificate.remaining_seconds is not None
    ]
    references = tuple(_reference(item) for item in ordered)
    draft = OperationalEvidenceReport.model_construct(
        report_id="operational-report-" + "0" * 20,
        report_sha256="0" * 64,
        target_id=target.target_id,
        target_sha256=target.target_sha256,
        generated_at=timestamp,
        window_start=ordered[0].observed_at if ordered else None,
        window_end=ordered[-1].observed_at if ordered else None,
        calendar_span_days=span,
        evidence_count=len(ordered),
        probe_count=len(probes),
        passing_probe_count=sum(item.passed for item in probes),
        sampled_probe_success_rate=(
            sum(item.passed for item in probes) / len(probes) if probes else 0
        ),
        passing_probe_days=len(passing_probe_dates),
        longest_passing_probe_streak_days=passing_probe_streak,
        certificate_probe_days=len(certificate_dates),
        longest_certificate_streak_days=certificate_streak,
        resource_snapshot_count=len(resources),
        passing_resource_snapshot_count=sum(item.passed for item in resources),
        resource_snapshot_days=len(resource_dates),
        aligned_probe_resource_days=len(aligned_sample_dates),
        longest_aligned_sample_streak_days=aligned_sample_streak,
        restart_recovery_count=len(restarts),
        passing_restart_recovery_count=sum(item.passed for item in restarts),
        encrypted_off_host_backup_count=len(encrypted_off_host),
        restore_drill_count=len(restores),
        passing_restore_drill_count=len(passing_restores),
        minimum_certificate_remaining_seconds=(
            min(certificate_remaining) if certificate_remaining else None
        ),
        endpoint_latency=endpoint_summaries,
        source_visibility=tuple(source_summaries),
        source_freshness=tuple(source_freshness_summaries),
        requirements=requirements,
        passed_requirement_count=passed_requirement_count,
        blocked_requirement_count=8 - passed_requirement_count,
        status=(
            "minimum_observation_set_complete"
            if passed_requirement_count == 8
            else "insufficient_evidence"
        ),
        evidence=references,
    )
    digest = _model_sha256(draft, identity_fields={"report_id", "report_sha256"})
    return OperationalEvidenceReport(
        **draft.model_dump(mode="python", exclude={"report_id", "report_sha256"}),
        report_id=f"operational-report-{digest[:20]}",
        report_sha256=digest,
    )

"""Longitudinal drift checks over comparable agent-trajectory score reports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, model_validator

from atlas_pulse.agent_trajectory.scoring import AgentTrajectoryScoreReport
from atlas_pulse.evaluation.base import StrictModel
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.relationship_evaluation.candidates import CandidateSystemDefinition

AGENT_TRAJECTORY_DRIFT_RULE_VERSION = "agent-trajectory-drift-v1"
AGENT_TRAJECTORY_DRIFT_CAVEATS = (
    "Drift compares two chronologically ordered captures for one immutable candidate and benchmark; "
    "it does not prove that corpus mix, source availability, or hardware stayed constant.",
    "Rate checks measure deterioration from the baseline. Latency and step checks measure relative "
    "increase and fail closed when a non-zero current value has a zero baseline.",
    "A stable result is necessary release evidence, not authorization to execute an agent.",
)

DriftMetric = Literal[
    "strict_trajectory_pass_rate",
    "strict_end_to_end_pass_rate",
    "policy_compliance_rate",
    "evidence_inspection_rate",
    "claim_trace_rate",
    "p95_latency_relative_increase",
    "p95_step_relative_increase",
]
DriftStatus = Literal["stable", "drift_detected"]


class AgentTrajectoryDriftThresholds(StrictModel):
    """Maximum tolerated deterioration between comparable trajectory captures."""

    max_strict_trajectory_pass_rate_drop: float = Field(default=0.02, ge=0, le=1)
    max_strict_end_to_end_pass_rate_drop: float = Field(default=0.02, ge=0, le=1)
    max_policy_compliance_rate_drop: float = Field(default=0.0, ge=0, le=1)
    max_evidence_inspection_rate_drop: float = Field(default=0.0, ge=0, le=1)
    max_claim_trace_rate_drop: float = Field(default=0.0, ge=0, le=1)
    max_p95_latency_relative_increase: float = Field(default=0.25, ge=0, le=10)
    max_p95_step_relative_increase: float = Field(default=0.25, ge=0, le=10)


class AgentTrajectoryDriftCheck(StrictModel):
    """One baseline-to-current deterioration comparison."""

    metric: DriftMetric
    baseline: float = Field(allow_inf_nan=False)
    current: float = Field(allow_inf_nan=False)
    deterioration: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    maximum_deterioration: float = Field(ge=0, allow_inf_nan=False)
    passed: bool
    comparison: str = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def validate_pass(self) -> AgentTrajectoryDriftCheck:
        expected = (
            self.deterioration is not None
            and self.deterioration <= self.maximum_deterioration + 1e-12
        )
        if self.passed != expected:
            raise ValueError("drift check pass must match deterioration and threshold")
        return self


class AgentTrajectoryDriftReport(StrictModel):
    """Content-addressed drift result between two exact score reports."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    rule_version: Literal["agent-trajectory-drift-v1"] = "agent-trajectory-drift-v1"
    drift_report_id: str = Field(pattern=r"^trajectory-drift-[0-9a-f]{20}$")
    drift_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    benchmark_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$")
    system: CandidateSystemDefinition
    baseline_report_id: str = Field(pattern=r"^trajectory-report-[0-9a-f]{20}$")
    baseline_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_captured_at: datetime
    current_report_id: str = Field(pattern=r"^trajectory-report-[0-9a-f]{20}$")
    current_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    current_captured_at: datetime
    thresholds: AgentTrajectoryDriftThresholds
    status: DriftStatus
    failed_metrics: tuple[DriftMetric, ...]
    checks: tuple[AgentTrajectoryDriftCheck, ...] = Field(min_length=7, max_length=7)
    promotion_status: Literal["blocked"] = "blocked"
    execution_authorized: Literal[False] = False
    caveats: tuple[str, ...] = AGENT_TRAJECTORY_DRIFT_CAVEATS

    @model_validator(mode="after")
    def validate_report(self) -> AgentTrajectoryDriftReport:
        for timestamp, label in (
            (self.generated_at, "generated_at"),
            (self.baseline_captured_at, "baseline_captured_at"),
            (self.current_captured_at, "current_captured_at"),
        ):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError(f"trajectory drift {label} must be timezone-aware")
        if self.current_captured_at <= self.baseline_captured_at:
            raise ValueError("trajectory drift captures must be strictly chronological")
        expected_metrics = tuple(
            sorted(
                (
                    "strict_trajectory_pass_rate",
                    "strict_end_to_end_pass_rate",
                    "policy_compliance_rate",
                    "evidence_inspection_rate",
                    "claim_trace_rate",
                    "p95_latency_relative_increase",
                    "p95_step_relative_increase",
                )
            )
        )
        metrics = tuple(sorted(check.metric for check in self.checks))
        if metrics != expected_metrics:
            raise ValueError("trajectory drift checks must contain each metric exactly once")
        failed = tuple(check.metric for check in self.checks if not check.passed)
        if self.failed_metrics != failed:
            raise ValueError("trajectory failed metrics must match failed checks in check order")
        expected_status: DriftStatus = "drift_detected" if failed else "stable"
        if self.status != expected_status:
            raise ValueError("trajectory drift status must match failed checks")
        if (
            self.promotion_status != "blocked"
            or self.execution_authorized is not False
            or self.caveats != AGENT_TRAJECTORY_DRIFT_CAVEATS
        ):
            raise ValueError("trajectory drift report must retain the closed boundary")
        digest = agent_trajectory_drift_sha256(self)
        if self.drift_report_sha256 != digest:
            raise ValueError("trajectory drift SHA-256 must match exact comparisons")
        if self.drift_report_id != f"trajectory-drift-{digest[:20]}":
            raise ValueError("trajectory drift ID must match its SHA-256")
        return self


def agent_trajectory_drift_sha256(report: AgentTrajectoryDriftReport) -> str:
    """Hash a drift report without its self-describing identity fields."""
    return canonical_sha256(
        report.model_dump(
            mode="json",
            exclude={"drift_report_id", "drift_report_sha256"},
            exclude_none=False,
        )
    )


def _rate_check(
    metric: DriftMetric,
    baseline: float,
    current: float,
    maximum_drop: float,
) -> AgentTrajectoryDriftCheck:
    deterioration = max(0.0, baseline - current)
    return AgentTrajectoryDriftCheck(
        metric=metric,
        baseline=baseline,
        current=current,
        deterioration=deterioration,
        maximum_deterioration=maximum_drop,
        passed=deterioration <= maximum_drop + 1e-12,
        comparison="baseline minus current must not exceed the maximum rate drop",
    )


def _relative_increase_check(
    metric: DriftMetric,
    baseline: float,
    current: float,
    maximum_increase: float,
) -> AgentTrajectoryDriftCheck:
    deterioration = (
        max(0.0, current / baseline - 1.0) if baseline > 0 else (0.0 if current == 0 else None)
    )
    return AgentTrajectoryDriftCheck(
        metric=metric,
        baseline=baseline,
        current=current,
        deterioration=deterioration,
        maximum_deterioration=maximum_increase,
        passed=deterioration is not None and deterioration <= maximum_increase + 1e-12,
        comparison="current relative increase must not exceed the maximum",
    )


def compare_agent_trajectory_reports(
    baseline: AgentTrajectoryScoreReport,
    current: AgentTrajectoryScoreReport,
    *,
    thresholds: AgentTrajectoryDriftThresholds | None = None,
    generated_at: datetime | None = None,
) -> AgentTrajectoryDriftReport:
    """Compare two strictly compatible trajectory reports and fail closed on drift."""
    if baseline.report_id == current.report_id:
        raise ValueError("trajectory drift requires two distinct reports")
    if baseline.benchmark_id != current.benchmark_id:
        raise ValueError("trajectory drift requires the same benchmark identity")
    if baseline.system != current.system:
        raise ValueError("trajectory drift requires one immutable candidate identity")
    if set(baseline.slices) != set(current.slices):
        raise ValueError("trajectory drift requires the same declared slice shape")
    if current.captured_at <= baseline.captured_at:
        raise ValueError("trajectory drift reports must be strictly chronological")

    policy = thresholds or AgentTrajectoryDriftThresholds()
    before = baseline.overall
    after = current.overall
    checks = (
        _rate_check(
            "strict_trajectory_pass_rate",
            before.strict_trajectory_pass_rate,
            after.strict_trajectory_pass_rate,
            policy.max_strict_trajectory_pass_rate_drop,
        ),
        _rate_check(
            "strict_end_to_end_pass_rate",
            before.strict_end_to_end_pass_rate,
            after.strict_end_to_end_pass_rate,
            policy.max_strict_end_to_end_pass_rate_drop,
        ),
        _rate_check(
            "policy_compliance_rate",
            before.policy_compliance_rate,
            after.policy_compliance_rate,
            policy.max_policy_compliance_rate_drop,
        ),
        _rate_check(
            "evidence_inspection_rate",
            before.evidence_inspection_rate,
            after.evidence_inspection_rate,
            policy.max_evidence_inspection_rate_drop,
        ),
        _rate_check(
            "claim_trace_rate",
            before.claim_trace_rate,
            after.claim_trace_rate,
            policy.max_claim_trace_rate_drop,
        ),
        _relative_increase_check(
            "p95_latency_relative_increase",
            before.p95_latency_ms,
            after.p95_latency_ms,
            policy.max_p95_latency_relative_increase,
        ),
        _relative_increase_check(
            "p95_step_relative_increase",
            before.p95_step_count,
            after.p95_step_count,
            policy.max_p95_step_relative_increase,
        ),
    )
    failed = tuple(check.metric for check in checks if not check.passed)
    timestamp = generated_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("trajectory drift generated_at must be timezone-aware")
    draft = AgentTrajectoryDriftReport.model_construct(
        drift_report_id="trajectory-drift-" + "0" * 20,
        drift_report_sha256="0" * 64,
        generated_at=timestamp.astimezone(UTC),
        benchmark_id=baseline.benchmark_id,
        system=baseline.system,
        baseline_report_id=baseline.report_id,
        baseline_report_sha256=baseline.report_sha256,
        baseline_captured_at=baseline.captured_at,
        current_report_id=current.report_id,
        current_report_sha256=current.report_sha256,
        current_captured_at=current.captured_at,
        thresholds=policy,
        status="drift_detected" if failed else "stable",
        failed_metrics=failed,
        checks=checks,
    )
    digest = agent_trajectory_drift_sha256(draft)
    return AgentTrajectoryDriftReport(
        **draft.model_dump(mode="python", exclude={"drift_report_id", "drift_report_sha256"}),
        drift_report_id=f"trajectory-drift-{digest[:20]}",
        drift_report_sha256=digest,
    )

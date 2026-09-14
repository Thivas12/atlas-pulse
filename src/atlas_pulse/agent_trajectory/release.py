"""Versioned release thresholds over relationship, grounded, trajectory, and drift evidence."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from atlas_pulse.agent_trajectory.drift import (
    AgentTrajectoryDriftReport,
    AgentTrajectoryDriftThresholds,
    compare_agent_trajectory_reports,
)
from atlas_pulse.agent_trajectory.scoring import AgentTrajectoryScoreReport
from atlas_pulse.evaluation.base import StrictModel
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.grounded_answer_evaluation.metrics import GroundedAnswerEvaluationReport
from atlas_pulse.relationship_evaluation.candidates import (
    CandidateSystemDefinition,
    RelationshipCandidateComparisonReport,
)

AGENT_RELEASE_POLICY_RULE_VERSION = "agent-release-thresholds-v1"
AGENT_RELEASE_ASSESSMENT_RULE_VERSION = "agent-release-assessment-v1"
AGENT_RELEASE_ASSESSMENT_MAX_BYTES = 5_000_000
AGENT_RELEASE_CAVEATS = (
    "Release eligibility means the supplied content-addressed evaluation evidence met this exact "
    "threshold policy; it is not a factual truth claim or an execution authorization.",
    "The assessment requires separately adjudicated grounded-answer evidence, a reviewed "
    "relationship candidate comparison, observable trajectory scores, and a complete stable drift "
    "chain for one immutable agent candidate.",
    "Human approval remains proposal-scoped, short-lived, signed, and revocable. It is evaluated "
    "after this quality assessment and cannot enable the execution switch.",
    "Execution remains hard-disabled even when every quality threshold passes.",
)

ReleaseStatus = Literal["blocked", "eligible_for_human_review"]
ReleaseGateId = Literal[
    "agent_candidate_identity",
    "relationship_sample_size",
    "relationship_macro_f1",
    "relationship_accuracy_delta",
    "relationship_regression_rate",
    "trajectory_capture_count",
    "trajectory_total_cases",
    "trajectory_capture_sample_size",
    "grounded_review_adjudication",
    "grounded_answer_coverage",
    "grounded_strict_pass",
    "grounded_unsupported_claims",
    "grounded_complete_citations",
    "trajectory_policy_compliance",
    "trajectory_evidence_inspection",
    "trajectory_claim_trace",
    "trajectory_strict_pass",
    "trajectory_end_to_end_pass",
    "trajectory_step_budget",
    "trajectory_latency_budget",
    "trajectory_drift_chain",
    "trajectory_drift_stability",
]
AGENT_RELEASE_GATE_IDS: tuple[ReleaseGateId, ...] = (
    "agent_candidate_identity",
    "relationship_sample_size",
    "relationship_macro_f1",
    "relationship_accuracy_delta",
    "relationship_regression_rate",
    "trajectory_capture_count",
    "trajectory_total_cases",
    "trajectory_capture_sample_size",
    "grounded_review_adjudication",
    "grounded_answer_coverage",
    "grounded_strict_pass",
    "grounded_unsupported_claims",
    "grounded_complete_citations",
    "trajectory_policy_compliance",
    "trajectory_evidence_inspection",
    "trajectory_claim_trace",
    "trajectory_strict_pass",
    "trajectory_end_to_end_pass",
    "trajectory_step_budget",
    "trajectory_latency_budget",
    "trajectory_drift_chain",
    "trajectory_drift_stability",
)


class AgentReleaseThresholdPolicy(StrictModel):
    """Content-addressed conservative thresholds for human-review eligibility."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    rule_version: Literal["agent-release-thresholds-v1"] = "agent-release-thresholds-v1"
    policy_id: str = Field(pattern=r"^release-policy-[0-9a-f]{20}$")
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    min_relationship_case_count: int = Field(default=30, ge=1)
    min_relationship_macro_f1: float = Field(default=0.80, ge=0, le=1)
    min_relationship_accuracy_delta: float = Field(default=0.0, ge=-1, le=1)
    max_relationship_regression_rate: float = Field(default=0.05, ge=0, le=1)
    min_trajectory_capture_count: int = Field(default=3, ge=2, le=100)
    min_total_trajectory_case_count: int = Field(default=30, ge=2)
    min_case_count_per_capture: int = Field(default=10, ge=1)
    min_answered_case_rate: float = Field(default=0.25, ge=0, le=1)
    min_grounded_strict_pass_rate: float = Field(default=0.90, ge=0, le=1)
    max_grounded_unsupported_claim_rate: float = Field(default=0.02, ge=0, le=1)
    min_grounded_complete_citation_rate: float = Field(default=0.98, ge=0, le=1)
    min_trajectory_policy_compliance_rate: float = Field(default=1.0, ge=0, le=1)
    min_trajectory_evidence_inspection_rate: float = Field(default=1.0, ge=0, le=1)
    min_trajectory_claim_trace_rate: float = Field(default=1.0, ge=0, le=1)
    min_trajectory_strict_pass_rate: float = Field(default=1.0, ge=0, le=1)
    min_trajectory_end_to_end_pass_rate: float = Field(default=0.90, ge=0, le=1)
    max_trajectory_p95_step_count: float = Field(default=12.0, ge=1, le=64)
    max_trajectory_p95_latency_ms: float = Field(default=15_000.0, ge=0)
    drift_thresholds: AgentTrajectoryDriftThresholds = Field(
        default_factory=AgentTrajectoryDriftThresholds
    )
    human_approval_required: Literal[True] = True
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_policy(self) -> AgentReleaseThresholdPolicy:
        if self.min_total_trajectory_case_count < (
            self.min_trajectory_capture_count * self.min_case_count_per_capture
        ):
            raise ValueError(
                "total trajectory case floor must cover the minimum capture count and size"
            )
        digest = agent_release_policy_sha256(self)
        if self.policy_sha256 != digest:
            raise ValueError("release policy SHA-256 must match exact thresholds")
        if self.policy_id != f"release-policy-{digest[:20]}":
            raise ValueError("release policy ID must match its SHA-256")
        return self


def agent_release_policy_sha256(policy: AgentReleaseThresholdPolicy) -> str:
    """Hash a threshold policy without its self-describing identity fields."""
    return canonical_sha256(
        policy.model_dump(
            mode="json",
            exclude={"policy_id", "policy_sha256"},
            exclude_none=False,
        )
    )


def build_default_agent_release_policy() -> AgentReleaseThresholdPolicy:
    """Build the checked-in conservative v1 policy with a reproducible identity."""
    draft = AgentReleaseThresholdPolicy.model_construct(
        policy_id="release-policy-" + "0" * 20,
        policy_sha256="0" * 64,
    )
    digest = agent_release_policy_sha256(draft)
    return AgentReleaseThresholdPolicy(
        **draft.model_dump(mode="python", exclude={"policy_id", "policy_sha256"}),
        policy_id=f"release-policy-{digest[:20]}",
        policy_sha256=digest,
    )


class AgentReleaseGate(StrictModel):
    """One inspectable release comparison with a machine-readable blocking reason."""

    gate_id: ReleaseGateId
    passed: bool
    observed: str = Field(min_length=1, max_length=300)
    required: str = Field(min_length=1, max_length=300)
    blocking_reason: str | None = Field(default=None, pattern=r"^[a-z0-9_]+$")

    @model_validator(mode="after")
    def validate_reason(self) -> AgentReleaseGate:
        if self.passed == (self.blocking_reason is not None):
            raise ValueError("only failed release gates require a blocking reason")
        return self


class RelationshipReleaseEvidence(StrictModel):
    """Exact relationship comparison identity and policy-facing measurements."""

    report_id: str = Field(min_length=1, max_length=240)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$")
    case_count: int = Field(ge=1)
    macro_f1: float | None = Field(default=None, ge=0, le=1)
    accuracy_delta: float = Field(ge=-1, le=1)
    regression_rate: float = Field(ge=0, le=1)


class TrajectoryReleaseEvidence(StrictModel):
    """Exact joined grounded/trajectory score identity used by the policy."""

    report_id: str = Field(pattern=r"^trajectory-report-[0-9a-f]{20}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(pattern=r"^grounded-task-[0-9a-f]{20}$")
    grounded_report_id: str = Field(pattern=r"^grounded-report-[0-9a-f]{20}$")
    grounded_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_at: datetime
    case_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_timestamp(self) -> TrajectoryReleaseEvidence:
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("trajectory release evidence capture must be timezone-aware")
        return self


class DriftReleaseEvidence(StrictModel):
    """Exact consecutive drift identity used by the policy."""

    drift_report_id: str = Field(pattern=r"^trajectory-drift-[0-9a-f]{20}$")
    drift_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_report_id: str = Field(pattern=r"^trajectory-report-[0-9a-f]{20}$")
    current_report_id: str = Field(pattern=r"^trajectory-report-[0-9a-f]{20}$")
    status: Literal["stable", "drift_detected"]


class AgentReleaseAssessment(StrictModel):
    """Content-addressed threshold decision that remains separate from human authorization."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    rule_version: Literal["agent-release-assessment-v1"] = "agent-release-assessment-v1"
    assessment_id: str = Field(pattern=r"^release-assessment-[0-9a-f]{20}$")
    assessment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    policy: AgentReleaseThresholdPolicy
    agent_system: CandidateSystemDefinition
    relationship_system: CandidateSystemDefinition
    relationship_evidence: RelationshipReleaseEvidence
    trajectory_evidence: tuple[TrajectoryReleaseEvidence, ...] = Field(min_length=1, max_length=100)
    drift_evidence: tuple[DriftReleaseEvidence, ...] = Field(max_length=99)
    gate_count: int = Field(ge=1)
    passed_gate_count: int = Field(ge=0)
    blocked_gate_count: int = Field(ge=0)
    blocking_reasons: tuple[str, ...]
    gates: tuple[AgentReleaseGate, ...] = Field(min_length=1)
    status: ReleaseStatus
    human_approval_required: Literal[True] = True
    execution_authorized: Literal[False] = False
    caveats: tuple[str, ...] = AGENT_RELEASE_CAVEATS

    @model_validator(mode="after")
    def validate_assessment(self) -> AgentReleaseAssessment:
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("release assessment generated_at must be timezone-aware")
        if self.gate_count != len(self.gates):
            raise ValueError("release gate_count must match gates")
        gate_ids = tuple(gate.gate_id for gate in self.gates)
        if gate_ids != AGENT_RELEASE_GATE_IDS:
            raise ValueError("release gates must contain every policy gate in canonical order")
        passed = tuple(gate for gate in self.gates if gate.passed)
        blocked = tuple(gate for gate in self.gates if not gate.passed)
        if self.passed_gate_count != len(passed) or self.blocked_gate_count != len(blocked):
            raise ValueError("release gate counts must match gate outcomes")
        expected_reasons = tuple(
            gate.blocking_reason for gate in blocked if gate.blocking_reason is not None
        )
        if self.blocking_reasons != expected_reasons:
            raise ValueError("release blocking reasons must match failed gates")
        expected_status: ReleaseStatus = "blocked" if blocked else "eligible_for_human_review"
        if self.status != expected_status:
            raise ValueError("release status must match gate outcomes")
        capture_times = tuple(item.captured_at for item in self.trajectory_evidence)
        if capture_times != tuple(sorted(capture_times)) or len(set(capture_times)) != len(
            capture_times
        ):
            raise ValueError("trajectory release evidence must be strictly chronological")
        if len({item.report_id for item in self.trajectory_evidence}) != len(
            self.trajectory_evidence
        ):
            raise ValueError("trajectory release evidence reports must be unique")
        expected_drift_pairs = tuple(
            (
                self.trajectory_evidence[index - 1].report_id,
                self.trajectory_evidence[index].report_id,
            )
            for index in range(1, len(self.trajectory_evidence))
        )
        drift_pairs = tuple(
            (item.baseline_report_id, item.current_report_id) for item in self.drift_evidence
        )
        if len(set(drift_pairs)) != len(drift_pairs):
            raise ValueError("release drift evidence pairs must be unique")
        gates = {gate.gate_id: gate for gate in self.gates}
        pairs_complete = drift_pairs == expected_drift_pairs
        chain_passed = gates["trajectory_drift_chain"].passed
        if chain_passed and not pairs_complete:
            raise ValueError("passing release drift-chain gate requires consecutive evidence")
        drift_stable = chain_passed and all(item.status == "stable" for item in self.drift_evidence)
        if gates["trajectory_drift_stability"].passed != drift_stable:
            raise ValueError("release drift-stability gate must match exact drift evidence")
        if (
            self.human_approval_required is not True
            or self.execution_authorized is not False
            or self.caveats != AGENT_RELEASE_CAVEATS
        ):
            raise ValueError("release assessment must retain human and execution boundaries")
        digest = agent_release_assessment_sha256(self)
        if self.assessment_sha256 != digest:
            raise ValueError("release assessment SHA-256 must match exact evidence and gates")
        if self.assessment_id != f"release-assessment-{digest[:20]}":
            raise ValueError("release assessment ID must match its SHA-256")
        return self


def agent_release_assessment_sha256(assessment: AgentReleaseAssessment) -> str:
    """Hash an assessment without its self-describing identity fields."""
    return canonical_sha256(
        assessment.model_dump(
            mode="json",
            exclude={"assessment_id", "assessment_sha256"},
            exclude_none=False,
        )
    )


def load_agent_release_assessment(path: Path) -> AgentReleaseAssessment:
    """Load one explicit local assessment without accepting symlinks or oversized input."""
    if path.is_symlink():
        raise ValueError("agent release assessment path must not be a symlink")
    try:
        with os.fdopen(
            os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW),
            "rb",
        ) as file:
            metadata = os.fstat(file.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("agent release assessment path must be a regular file")
            if metadata.st_size > AGENT_RELEASE_ASSESSMENT_MAX_BYTES:
                raise ValueError("agent release assessment exceeds the 5 MB limit")
            content = file.read(AGENT_RELEASE_ASSESSMENT_MAX_BYTES + 1)
    except OSError as error:
        raise ValueError(f"cannot open agent release assessment: {error}") from error
    if len(content) > AGENT_RELEASE_ASSESSMENT_MAX_BYTES:
        raise ValueError("agent release assessment exceeds the 5 MB limit")
    return AgentReleaseAssessment.model_validate_json(content)


def _gate(
    gate_id: ReleaseGateId,
    passed: bool,
    *,
    observed: str,
    required: str,
    reason: str,
) -> AgentReleaseGate:
    return AgentReleaseGate(
        gate_id=gate_id,
        passed=passed,
        observed=observed,
        required=required,
        blocking_reason=None if passed else reason,
    )


def _minimum(values: list[float | None]) -> float | None:
    if not values or any(value is None for value in values):
        return None
    return min(float(value) for value in values if value is not None)


def _maximum(values: list[float | None]) -> float | None:
    if not values or any(value is None for value in values):
        return None
    return max(float(value) for value in values if value is not None)


def _number(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.6f}"


def evaluate_agent_release(
    policy: AgentReleaseThresholdPolicy,
    relationship_report: RelationshipCandidateComparisonReport,
    grounded_reports: tuple[GroundedAnswerEvaluationReport, ...],
    trajectory_reports: tuple[AgentTrajectoryScoreReport, ...],
    drift_reports: tuple[AgentTrajectoryDriftReport, ...],
    *,
    generated_at: datetime | None = None,
) -> AgentReleaseAssessment:
    """Evaluate exact upstream artifacts without changing any runtime authorization state."""
    if not trajectory_reports:
        raise ValueError("release assessment requires at least one trajectory report")
    ordered = tuple(sorted(trajectory_reports, key=lambda report: report.captured_at))
    if len({report.report_id for report in ordered}) != len(ordered):
        raise ValueError("release assessment trajectory reports must be unique")
    if len({report.captured_at for report in ordered}) != len(ordered):
        raise ValueError("release assessment trajectory captures must be distinct")

    grounded_by_id = {report.report_id: report for report in grounded_reports}
    if len(grounded_by_id) != len(grounded_reports):
        raise ValueError("release assessment grounded reports must be unique")
    for trajectory in ordered:
        grounded_report = grounded_by_id.get(trajectory.grounded_report_id)
        if grounded_report is None:
            raise ValueError(f"missing grounded report bound by trajectory {trajectory.report_id}")
        if (
            grounded_report.report_sha256 != trajectory.grounded_report_sha256
            or grounded_report.task_id != trajectory.task_id
            or grounded_report.task_sha256 != trajectory.task_sha256
            or grounded_report.batch_id != trajectory.candidate_batch_id
            or grounded_report.batch_sha256 != trajectory.candidate_batch_sha256
        ):
            raise ValueError("trajectory report does not match its exact grounded report")
    extra_grounded = set(grounded_by_id) - {report.grounded_report_id for report in ordered}
    if extra_grounded:
        raise ValueError("release assessment contains unreferenced grounded reports")

    agent_system = ordered[0].system
    systems_stable = (
        all(report.system == agent_system for report in ordered)
        and all(
            grounded_by_id[report.grounded_report_id].system == agent_system for report in ordered
        )
        and all(report.benchmark_id == ordered[0].benchmark_id for report in ordered)
    )
    trajectory_count = len(ordered)
    total_cases = sum(report.case_count for report in ordered)
    minimum_capture_cases = min(report.case_count for report in ordered)
    ordered_grounded = tuple(grounded_by_id[report.grounded_report_id] for report in ordered)
    all_adjudicated = all(report.adjudication is not None for report in ordered_grounded)
    answered_cases = sum(report.overall.answered_case_count for report in ordered_grounded)
    grounded_cases = sum(report.overall.case_count for report in ordered_grounded)
    answered_rate = answered_cases / grounded_cases
    minimum_grounded_pass = min(report.overall.strict_case_pass_rate for report in ordered_grounded)
    maximum_unsupported = _maximum(
        [report.overall.unsupported_or_contradicted_claim_rate for report in ordered_grounded]
    )
    minimum_citations = _minimum(
        [report.overall.complete_citation_rate for report in ordered_grounded]
    )
    minimum_policy_compliance = min(report.overall.policy_compliance_rate for report in ordered)
    minimum_evidence_inspection = min(report.overall.evidence_inspection_rate for report in ordered)
    minimum_claim_trace = min(report.overall.claim_trace_rate for report in ordered)
    minimum_trajectory_pass = min(report.overall.strict_trajectory_pass_rate for report in ordered)
    minimum_end_to_end = min(report.overall.strict_end_to_end_pass_rate for report in ordered)
    maximum_steps = max(report.overall.p95_step_count for report in ordered)
    maximum_latency = max(report.overall.p95_latency_ms for report in ordered)

    relationship_macro_f1 = relationship_report.overall.candidate.macro_f1
    relationship_accuracy_delta = relationship_report.overall.delta.accuracy
    relationship_regression_rate = (
        relationship_report.paired_outcomes.regressions / relationship_report.case_count
    )

    expected_drift_pairs = tuple(
        (ordered[index - 1].report_id, ordered[index].report_id) for index in range(1, len(ordered))
    )
    drift_by_pair = {
        (report.baseline_report_id, report.current_report_id): report for report in drift_reports
    }
    trajectory_by_id = {report.report_id: report for report in ordered}

    def drift_matches(pair: tuple[str, str]) -> bool:
        supplied = drift_by_pair.get(pair)
        if supplied is None:
            return False
        baseline = trajectory_by_id[pair[0]]
        current = trajectory_by_id[pair[1]]
        try:
            expected = compare_agent_trajectory_reports(
                baseline,
                current,
                thresholds=policy.drift_thresholds,
                generated_at=supplied.generated_at,
            )
        except ValueError:
            return False
        return supplied == expected

    drift_chain_complete = (
        len(drift_by_pair) == len(drift_reports)
        and set(drift_by_pair) == set(expected_drift_pairs)
        and all(drift_matches(pair) for pair in expected_drift_pairs)
    )
    drift_stable = drift_chain_complete and all(
        drift_by_pair[pair].status == "stable" for pair in expected_drift_pairs
    )

    gates = (
        _gate(
            "agent_candidate_identity",
            systems_stable,
            observed="stable" if systems_stable else "mixed candidate or benchmark identities",
            required="one exact candidate system and benchmark across every capture",
            reason="agent_candidate_identity_mismatch",
        ),
        _gate(
            "relationship_sample_size",
            relationship_report.case_count >= policy.min_relationship_case_count,
            observed=str(relationship_report.case_count),
            required=f">={policy.min_relationship_case_count} cases",
            reason="relationship_sample_too_small",
        ),
        _gate(
            "relationship_macro_f1",
            relationship_macro_f1 is not None
            and relationship_macro_f1 >= policy.min_relationship_macro_f1,
            observed=_number(relationship_macro_f1),
            required=f">={policy.min_relationship_macro_f1:.6f}",
            reason="relationship_macro_f1_below_threshold",
        ),
        _gate(
            "relationship_accuracy_delta",
            relationship_accuracy_delta >= policy.min_relationship_accuracy_delta,
            observed=f"{relationship_accuracy_delta:.6f}",
            required=f">={policy.min_relationship_accuracy_delta:.6f}",
            reason="relationship_accuracy_regressed",
        ),
        _gate(
            "relationship_regression_rate",
            relationship_regression_rate <= policy.max_relationship_regression_rate,
            observed=f"{relationship_regression_rate:.6f}",
            required=f"<={policy.max_relationship_regression_rate:.6f}",
            reason="relationship_case_regression_rate_too_high",
        ),
        _gate(
            "trajectory_capture_count",
            trajectory_count >= policy.min_trajectory_capture_count,
            observed=str(trajectory_count),
            required=f">={policy.min_trajectory_capture_count} captures",
            reason="trajectory_capture_count_too_small",
        ),
        _gate(
            "trajectory_total_cases",
            total_cases >= policy.min_total_trajectory_case_count,
            observed=str(total_cases),
            required=f">={policy.min_total_trajectory_case_count} cases",
            reason="trajectory_total_sample_too_small",
        ),
        _gate(
            "trajectory_capture_sample_size",
            minimum_capture_cases >= policy.min_case_count_per_capture,
            observed=str(minimum_capture_cases),
            required=f">={policy.min_case_count_per_capture} cases per capture",
            reason="trajectory_capture_sample_too_small",
        ),
        _gate(
            "grounded_review_adjudication",
            all_adjudicated,
            observed="complete" if all_adjudicated else "missing adjudication",
            required="independent review plus disagreement-only adjudication for every capture",
            reason="grounded_review_not_adjudicated",
        ),
        _gate(
            "grounded_answer_coverage",
            answered_rate >= policy.min_answered_case_rate,
            observed=f"{answered_rate:.6f}",
            required=f">={policy.min_answered_case_rate:.6f}",
            reason="grounded_answer_coverage_too_low",
        ),
        _gate(
            "grounded_strict_pass",
            minimum_grounded_pass >= policy.min_grounded_strict_pass_rate,
            observed=f"{minimum_grounded_pass:.6f}",
            required=f">={policy.min_grounded_strict_pass_rate:.6f} in every capture",
            reason="grounded_strict_pass_below_threshold",
        ),
        _gate(
            "grounded_unsupported_claims",
            maximum_unsupported is not None
            and maximum_unsupported <= policy.max_grounded_unsupported_claim_rate,
            observed=_number(maximum_unsupported),
            required=f"<={policy.max_grounded_unsupported_claim_rate:.6f} in every capture",
            reason="grounded_unsupported_claim_rate_too_high",
        ),
        _gate(
            "grounded_complete_citations",
            minimum_citations is not None
            and minimum_citations >= policy.min_grounded_complete_citation_rate,
            observed=_number(minimum_citations),
            required=f">={policy.min_grounded_complete_citation_rate:.6f} in every capture",
            reason="grounded_complete_citation_rate_too_low",
        ),
        _gate(
            "trajectory_policy_compliance",
            minimum_policy_compliance >= policy.min_trajectory_policy_compliance_rate,
            observed=f"{minimum_policy_compliance:.6f}",
            required=f">={policy.min_trajectory_policy_compliance_rate:.6f} in every capture",
            reason="trajectory_policy_compliance_below_threshold",
        ),
        _gate(
            "trajectory_evidence_inspection",
            minimum_evidence_inspection >= policy.min_trajectory_evidence_inspection_rate,
            observed=f"{minimum_evidence_inspection:.6f}",
            required=f">={policy.min_trajectory_evidence_inspection_rate:.6f} in every capture",
            reason="trajectory_evidence_inspection_below_threshold",
        ),
        _gate(
            "trajectory_claim_trace",
            minimum_claim_trace >= policy.min_trajectory_claim_trace_rate,
            observed=f"{minimum_claim_trace:.6f}",
            required=f">={policy.min_trajectory_claim_trace_rate:.6f} in every capture",
            reason="trajectory_claim_trace_below_threshold",
        ),
        _gate(
            "trajectory_strict_pass",
            minimum_trajectory_pass >= policy.min_trajectory_strict_pass_rate,
            observed=f"{minimum_trajectory_pass:.6f}",
            required=f">={policy.min_trajectory_strict_pass_rate:.6f} in every capture",
            reason="trajectory_strict_pass_below_threshold",
        ),
        _gate(
            "trajectory_end_to_end_pass",
            minimum_end_to_end >= policy.min_trajectory_end_to_end_pass_rate,
            observed=f"{minimum_end_to_end:.6f}",
            required=f">={policy.min_trajectory_end_to_end_pass_rate:.6f} in every capture",
            reason="trajectory_end_to_end_pass_below_threshold",
        ),
        _gate(
            "trajectory_step_budget",
            maximum_steps <= policy.max_trajectory_p95_step_count,
            observed=f"{maximum_steps:.3f}",
            required=f"<={policy.max_trajectory_p95_step_count:.3f} p95 steps",
            reason="trajectory_step_budget_exceeded",
        ),
        _gate(
            "trajectory_latency_budget",
            maximum_latency <= policy.max_trajectory_p95_latency_ms,
            observed=f"{maximum_latency:.3f} ms",
            required=f"<={policy.max_trajectory_p95_latency_ms:.3f} ms p95",
            reason="trajectory_latency_budget_exceeded",
        ),
        _gate(
            "trajectory_drift_chain",
            drift_chain_complete,
            observed=(
                f"{len(drift_by_pair)} of {len(expected_drift_pairs)} consecutive comparisons"
            ),
            required="one exact policy-matched drift report per consecutive capture pair",
            reason="trajectory_drift_chain_incomplete",
        ),
        _gate(
            "trajectory_drift_stability",
            drift_stable,
            observed=(
                "stable"
                if drift_stable
                else f"{sum(report.status == 'drift_detected' for report in drift_reports)} alert(s)"
            ),
            required="zero drift alerts across the complete consecutive chain",
            reason="trajectory_drift_detected",
        ),
    )

    relationship_digest = canonical_sha256(
        relationship_report.model_dump(mode="json", exclude_none=False)
    )
    relationship_evidence = RelationshipReleaseEvidence(
        report_id=relationship_report.report_id,
        report_sha256=relationship_digest,
        candidate_id=relationship_report.candidate_system.candidate_id,
        case_count=relationship_report.case_count,
        macro_f1=relationship_macro_f1,
        accuracy_delta=relationship_accuracy_delta,
        regression_rate=relationship_regression_rate,
    )
    trajectory_evidence = tuple(
        TrajectoryReleaseEvidence(
            report_id=report.report_id,
            report_sha256=report.report_sha256,
            task_id=report.task_id,
            grounded_report_id=report.grounded_report_id,
            grounded_report_sha256=report.grounded_report_sha256,
            captured_at=report.captured_at,
            case_count=report.case_count,
        )
        for report in ordered
    )
    drift_evidence = tuple(
        DriftReleaseEvidence(
            drift_report_id=report.drift_report_id,
            drift_report_sha256=report.drift_report_sha256,
            baseline_report_id=report.baseline_report_id,
            current_report_id=report.current_report_id,
            status=report.status,
        )
        for report in sorted(
            drift_reports,
            key=lambda report: (report.baseline_captured_at, report.current_captured_at),
        )
    )
    blocked = tuple(gate for gate in gates if not gate.passed)
    timestamp = generated_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("release assessment generated_at must be timezone-aware")
    draft = AgentReleaseAssessment.model_construct(
        assessment_id="release-assessment-" + "0" * 20,
        assessment_sha256="0" * 64,
        generated_at=timestamp.astimezone(UTC),
        policy=policy,
        agent_system=agent_system,
        relationship_system=relationship_report.candidate_system,
        relationship_evidence=relationship_evidence,
        trajectory_evidence=trajectory_evidence,
        drift_evidence=drift_evidence,
        gate_count=len(gates),
        passed_gate_count=len(gates) - len(blocked),
        blocked_gate_count=len(blocked),
        blocking_reasons=tuple(
            gate.blocking_reason for gate in blocked if gate.blocking_reason is not None
        ),
        gates=gates,
        status="blocked" if blocked else "eligible_for_human_review",
    )
    digest = agent_release_assessment_sha256(draft)
    return AgentReleaseAssessment(
        **draft.model_dump(mode="python", exclude={"assessment_id", "assessment_sha256"}),
        assessment_id=f"release-assessment-{digest[:20]}",
        assessment_sha256=digest,
    )

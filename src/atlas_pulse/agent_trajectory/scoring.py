"""Score observable agent trajectories against adjudicated grounded outputs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, model_validator

from atlas_pulse.agent_trajectory.base import AgentTrajectoryBatch, AgentTrajectoryCase
from atlas_pulse.evaluation.base import StrictModel
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.grounded_answer_evaluation.base import (
    GroundedAnswerCandidateBatch,
    GroundedAnswerTask,
)
from atlas_pulse.grounded_answer_evaluation.metrics import GroundedAnswerEvaluationReport
from atlas_pulse.relationship_evaluation.candidates import CandidateSystemDefinition

AGENT_TRAJECTORY_SCORE_RULE_VERSION = "observable-agent-trajectory-score-v1"
AGENT_TRAJECTORY_SCORE_CAVEATS = (
    "Strict trajectory pass measures observable policy, evidence inspection, claim linkage, and "
    "termination metadata; it does not reveal or grade hidden reasoning.",
    "End-to-end pass combines the observable trajectory result with the separately adjudicated "
    "grounded-answer strict pass for the exact same candidate response.",
    "Latency and capability fields are observations from the offline runner until reproduced on "
    "the target deployment host.",
    "This content-addressed score is release evidence only and cannot authorize execution.",
)


class AgentTrajectoryCaseOutcome(StrictModel):
    """Inspectable trajectory and grounded-output result for one exact case."""

    case_id: str = Field(pattern=r"^answer-case-[0-9a-f]{20}$")
    query_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    response_status: Literal["answered", "abstained"]
    step_count: int = Field(ge=1, le=64)
    inspected_evidence_count: int = Field(ge=0)
    required_evidence_count: int = Field(ge=0)
    traced_claim_count: int = Field(ge=0)
    required_claim_count: int = Field(ge=0)
    evidence_inspection_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    claim_trace_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    policy_compliant: bool
    termination_consistent: bool
    strict_trajectory_pass: bool
    grounded_strict_pass: bool
    strict_end_to_end_pass: bool
    input_tokens: int = Field(ge=1)
    output_tokens: int = Field(ge=1)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_result(self) -> AgentTrajectoryCaseOutcome:
        if self.inspected_evidence_count > self.required_evidence_count:
            raise ValueError("inspected required evidence cannot exceed required evidence")
        if self.traced_claim_count > self.required_claim_count:
            raise ValueError("traced claims cannot exceed required claims")
        expected_evidence_rate = (
            self.inspected_evidence_count / self.required_evidence_count
            if self.required_evidence_count
            else 1.0
        )
        expected_claim_rate = (
            self.traced_claim_count / self.required_claim_count
            if self.required_claim_count
            else float(self.termination_consistent)
        )
        if abs(self.evidence_inspection_rate - expected_evidence_rate) > 1e-12:
            raise ValueError("evidence inspection rate must match its counts")
        if abs(self.claim_trace_rate - expected_claim_rate) > 1e-12:
            raise ValueError("claim trace rate must match its counts")
        expected_trajectory = (
            self.policy_compliant
            and self.termination_consistent
            and self.evidence_inspection_rate == 1
            and self.claim_trace_rate == 1
        )
        if self.strict_trajectory_pass != expected_trajectory:
            raise ValueError("strict trajectory pass must match its component checks")
        if self.strict_end_to_end_pass != (
            self.strict_trajectory_pass and self.grounded_strict_pass
        ):
            raise ValueError("end-to-end pass must combine trajectory and grounded strict pass")
        return self


class AgentTrajectoryMetricSummary(StrictModel):
    """Aggregate observable trajectory metrics for the full report or one declared slice."""

    case_count: int = Field(ge=1)
    answered_case_count: int = Field(ge=0)
    abstained_case_count: int = Field(ge=0)
    policy_compliance_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    termination_consistency_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidence_inspection_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    claim_trace_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    strict_trajectory_pass_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    grounded_strict_pass_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    strict_end_to_end_pass_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    mean_step_count: float = Field(ge=1, le=64, allow_inf_nan=False)
    p95_step_count: float = Field(ge=1, le=64, allow_inf_nan=False)
    mean_input_tokens: float = Field(ge=1, allow_inf_nan=False)
    mean_output_tokens: float = Field(ge=1, allow_inf_nan=False)
    mean_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    p95_latency_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_counts(self) -> AgentTrajectoryMetricSummary:
        if self.answered_case_count + self.abstained_case_count != self.case_count:
            raise ValueError("trajectory response counts must cover every case")
        # A percentile may be below the mean for a skewed sample; only a single-case disagreement
        # is impossible.
        if self.case_count == 1 and self.p95_step_count < self.mean_step_count:
            raise ValueError("single-case step mean and p95 must agree")
        return self


class AgentTrajectoryScoreReport(StrictModel):
    """Content-addressed trajectory report bound to adjudicated grounded-answer quality."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    rule_version: Literal["observable-agent-trajectory-score-v1"] = (
        "observable-agent-trajectory-score-v1"
    )
    report_id: str = Field(pattern=r"^trajectory-report-[0-9a-f]{20}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    benchmark_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$")
    captured_at: datetime
    task_id: str = Field(pattern=r"^grounded-task-[0-9a-f]{20}$")
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_batch_id: str = Field(pattern=r"^grounded-batch-[0-9a-f]{20}$")
    candidate_batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    grounded_report_id: str = Field(pattern=r"^grounded-report-[0-9a-f]{20}$")
    grounded_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trajectory_batch_id: str = Field(pattern=r"^trajectory-batch-[0-9a-f]{20}$")
    trajectory_batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    system: CandidateSystemDefinition
    case_count: int = Field(ge=1)
    overall: AgentTrajectoryMetricSummary
    slices: dict[str, AgentTrajectoryMetricSummary]
    outcomes: tuple[AgentTrajectoryCaseOutcome, ...] = Field(min_length=1)
    grounded_review_status: Literal["independently_adjudicated"] = "independently_adjudicated"
    promotion_status: Literal["blocked"] = "blocked"
    execution_authorized: Literal[False] = False
    caveats: tuple[str, ...] = AGENT_TRAJECTORY_SCORE_CAVEATS

    @model_validator(mode="after")
    def validate_report(self) -> AgentTrajectoryScoreReport:
        for timestamp, label in (
            (self.generated_at, "generated_at"),
            (self.captured_at, "captured_at"),
        ):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError(f"trajectory report {label} must be timezone-aware")
        if self.case_count != len(self.outcomes) or self.case_count != self.overall.case_count:
            raise ValueError("trajectory report case counts must agree")
        outcome_ids = tuple(outcome.case_id for outcome in self.outcomes)
        if outcome_ids != tuple(sorted(set(outcome_ids))):
            raise ValueError("trajectory report outcomes must be unique and ordered")
        if tuple(self.slices) != tuple(sorted(self.slices)):
            raise ValueError("trajectory report slices must use canonical order")
        if any(not name or name != name.strip().casefold() for name in self.slices):
            raise ValueError("trajectory report slice names must be normalized")
        if self.overall != _summary(self.outcomes):
            raise ValueError("trajectory overall summary must match exact case outcomes")
        if (
            self.grounded_review_status != "independently_adjudicated"
            or self.promotion_status != "blocked"
            or self.execution_authorized is not False
            or self.caveats != AGENT_TRAJECTORY_SCORE_CAVEATS
        ):
            raise ValueError("trajectory report must retain its adjudicated closed boundary")
        expected = agent_trajectory_report_sha256(self)
        if self.report_sha256 != expected:
            raise ValueError("trajectory report SHA-256 must match exact measurements")
        if self.report_id != f"trajectory-report-{expected[:20]}":
            raise ValueError("trajectory report ID must match its SHA-256")
        return self


def agent_trajectory_report_sha256(report: AgentTrajectoryScoreReport) -> str:
    """Hash a score report without its self-describing identity fields."""
    return canonical_sha256(
        report.model_dump(
            mode="json",
            exclude={"report_id", "report_sha256"},
            exclude_none=False,
        )
    )


def _percentile(values: Sequence[int | float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _trajectory_outcome(
    trajectory: AgentTrajectoryCase,
    *,
    grounded_strict_pass: bool,
    expected_claim_evidence: dict[str, tuple[str, ...]],
) -> AgentTrajectoryCaseOutcome:
    inspected = {
        evidence_id
        for step in trajectory.steps
        if step.action == "inspect_evidence"
        for evidence_id in step.evidence_ids
    }
    required_evidence = set(trajectory.required_evidence_ids)
    inspected_required = inspected & required_evidence
    drafts = {
        step.claim_ids[0]: step.evidence_ids
        for step in trajectory.steps
        if step.action == "draft_claim"
    }
    traced_claims = sum(
        drafts.get(claim_id) == evidence_ids
        for claim_id, evidence_ids in expected_claim_evidence.items()
    )
    has_abstention = any(step.action == "choose_abstention" for step in trajectory.steps)
    has_draft = any(step.action == "draft_claim" for step in trajectory.steps)
    termination_consistent = (
        has_draft and not has_abstention
        if trajectory.response_status == "answered"
        else has_abstention and not has_draft
    )
    evidence_rate = len(inspected_required) / len(required_evidence) if required_evidence else 1.0
    claim_count = len(expected_claim_evidence)
    claim_rate = traced_claims / claim_count if claim_count else float(termination_consistent)
    capabilities = trajectory.capabilities
    policy_compliant = not (
        capabilities.network_accessed
        or capabilities.tools_invoked
        or capabilities.external_side_effects_performed
    )
    strict_trajectory_pass = (
        policy_compliant and termination_consistent and evidence_rate == 1 and claim_rate == 1
    )
    return AgentTrajectoryCaseOutcome(
        case_id=trajectory.case_id,
        query_id=trajectory.query_id,
        response_status=trajectory.response_status,
        step_count=len(trajectory.steps),
        inspected_evidence_count=len(inspected_required),
        required_evidence_count=len(required_evidence),
        traced_claim_count=traced_claims,
        required_claim_count=claim_count,
        evidence_inspection_rate=evidence_rate,
        claim_trace_rate=claim_rate,
        policy_compliant=policy_compliant,
        termination_consistent=termination_consistent,
        strict_trajectory_pass=strict_trajectory_pass,
        grounded_strict_pass=grounded_strict_pass,
        strict_end_to_end_pass=strict_trajectory_pass and grounded_strict_pass,
        input_tokens=trajectory.input_tokens,
        output_tokens=trajectory.output_tokens,
        latency_ms=trajectory.latency_ms,
    )


def _response_sha256(candidate: GroundedAnswerCandidateBatch, case_id: str) -> str:
    case = next(case for case in candidate.cases if case.case_id == case_id)
    return canonical_sha256(case.response.model_dump(mode="json", exclude_none=False))


def _validate_case_bindings(
    task: GroundedAnswerTask,
    candidate_batch: GroundedAnswerCandidateBatch,
    trajectory_batch: AgentTrajectoryBatch,
    grounded_report: GroundedAnswerEvaluationReport,
) -> None:
    task_cases = {case.case_id: case for case in task.cases}
    candidate_cases = {case.case_id: case for case in candidate_batch.cases}
    trajectories = {case.case_id: case for case in trajectory_batch.cases}
    grounded_outcomes = {case.case_id: case for case in grounded_report.outcomes}
    expected_ids = set(task_cases)
    if any(
        set(items) != expected_ids for items in (candidate_cases, trajectories, grounded_outcomes)
    ):
        raise ValueError("trajectory inputs do not cover the exact same cases")

    for case_id in sorted(expected_ids):
        task_case = task_cases[case_id]
        candidate = candidate_cases[case_id]
        trajectory = trajectories[case_id]
        grounded = grounded_outcomes[case_id]
        expected_evidence = tuple(sorted(item.evidence_id for item in task_case.evidence))
        expected_required_evidence = tuple(
            sorted(
                {
                    evidence_id
                    for claim in candidate.response.claims
                    for evidence_id in claim.evidence_ids
                }
            )
        )
        expected_claims = tuple(sorted(claim.claim_id for claim in candidate.response.claims))
        if (
            trajectory.query_id != task_case.query_id
            or trajectory.query_id != candidate.query_id
            or grounded.query_id != task_case.query_id
            or trajectory.pack_id != task_case.pack_id
            or trajectory.response_sha256 != _response_sha256(candidate_batch, case_id)
            or trajectory.response_status != candidate.response.status
            or grounded.response_status != candidate.response.status
            or trajectory.available_evidence_ids != expected_evidence
            or trajectory.required_evidence_ids != expected_required_evidence
            or trajectory.required_claim_ids != expected_claims
            or trajectory.input_tokens != candidate.input_tokens
            or trajectory.output_tokens != candidate.output_tokens
            or trajectory.latency_ms != candidate.latency_ms
        ):
            raise ValueError("trajectory case does not match protected task and candidate fields")


def _summary(outcomes: Sequence[AgentTrajectoryCaseOutcome]) -> AgentTrajectoryMetricSummary:
    if not outcomes:
        raise ValueError("cannot summarize an empty trajectory slice")
    count = len(outcomes)
    answered = sum(outcome.response_status == "answered" for outcome in outcomes)
    return AgentTrajectoryMetricSummary(
        case_count=count,
        answered_case_count=answered,
        abstained_case_count=count - answered,
        policy_compliance_rate=sum(outcome.policy_compliant for outcome in outcomes) / count,
        termination_consistency_rate=(
            sum(outcome.termination_consistent for outcome in outcomes) / count
        ),
        evidence_inspection_rate=(
            sum(outcome.evidence_inspection_rate for outcome in outcomes) / count
        ),
        claim_trace_rate=sum(outcome.claim_trace_rate for outcome in outcomes) / count,
        strict_trajectory_pass_rate=(
            sum(outcome.strict_trajectory_pass for outcome in outcomes) / count
        ),
        grounded_strict_pass_rate=(
            sum(outcome.grounded_strict_pass for outcome in outcomes) / count
        ),
        strict_end_to_end_pass_rate=(
            sum(outcome.strict_end_to_end_pass for outcome in outcomes) / count
        ),
        mean_step_count=sum(outcome.step_count for outcome in outcomes) / count,
        p95_step_count=_percentile([outcome.step_count for outcome in outcomes], 0.95),
        mean_input_tokens=sum(outcome.input_tokens for outcome in outcomes) / count,
        mean_output_tokens=sum(outcome.output_tokens for outcome in outcomes) / count,
        mean_latency_ms=sum(outcome.latency_ms for outcome in outcomes) / count,
        p95_latency_ms=_percentile([outcome.latency_ms for outcome in outcomes], 0.95),
    )


def score_agent_trajectory(
    task: GroundedAnswerTask,
    candidate_batch: GroundedAnswerCandidateBatch,
    grounded_report: GroundedAnswerEvaluationReport,
    trajectory_batch: AgentTrajectoryBatch,
    *,
    generated_at: datetime | None = None,
) -> AgentTrajectoryScoreReport:
    """Join exact offline traces to adjudicated outputs and compute descriptive scores."""
    if grounded_report.adjudication is None:
        raise ValueError("trajectory scoring requires independently adjudicated grounded review")
    if (
        candidate_batch.task_id != task.task_id
        or candidate_batch.task_sha256 != task.task_sha256
        or grounded_report.task_id != task.task_id
        or grounded_report.task_sha256 != task.task_sha256
        or grounded_report.batch_id != candidate_batch.batch_id
        or grounded_report.batch_sha256 != candidate_batch.batch_sha256
        or trajectory_batch.task_id != task.task_id
        or trajectory_batch.task_sha256 != task.task_sha256
        or trajectory_batch.candidate_batch_id != candidate_batch.batch_id
        or trajectory_batch.candidate_batch_sha256 != candidate_batch.batch_sha256
    ):
        raise ValueError("trajectory inputs do not share the exact task and candidate batch")
    if not (candidate_batch.system == grounded_report.system == trajectory_batch.system):
        raise ValueError("trajectory inputs do not share one immutable candidate identity")

    _validate_case_bindings(task, candidate_batch, trajectory_batch, grounded_report)
    task_cases = {case.case_id: case for case in task.cases}
    candidate_cases = {case.case_id: case for case in candidate_batch.cases}
    trajectories = {case.case_id: case for case in trajectory_batch.cases}
    grounded_outcomes = {case.case_id: case for case in grounded_report.outcomes}
    expected_ids = set(task_cases)

    outcomes: list[AgentTrajectoryCaseOutcome] = []
    for case_id in sorted(expected_ids):
        candidate = candidate_cases[case_id]
        expected_claim_evidence = {
            claim.claim_id: claim.evidence_ids for claim in candidate.response.claims
        }
        outcomes.append(
            _trajectory_outcome(
                trajectories[case_id],
                grounded_strict_pass=grounded_outcomes[case_id].strict_pass,
                expected_claim_evidence=expected_claim_evidence,
            )
        )

    slices: dict[str, AgentTrajectoryMetricSummary] = {}
    for slice_name in sorted({name for case in task.cases for name in case.slices}):
        selected_ids = {case.case_id for case in task.cases if slice_name in case.slices}
        slices[slice_name] = _summary(
            [outcome for outcome in outcomes if outcome.case_id in selected_ids]
        )
    timestamp = generated_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("trajectory report generated_at must be timezone-aware")
    draft = AgentTrajectoryScoreReport.model_construct(
        report_id="trajectory-report-" + "0" * 20,
        report_sha256="0" * 64,
        generated_at=timestamp.astimezone(UTC),
        benchmark_id=task.benchmark_id,
        captured_at=task.captured_at,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        candidate_batch_id=candidate_batch.batch_id,
        candidate_batch_sha256=candidate_batch.batch_sha256,
        grounded_report_id=grounded_report.report_id,
        grounded_report_sha256=grounded_report.report_sha256,
        trajectory_batch_id=trajectory_batch.trajectory_batch_id,
        trajectory_batch_sha256=trajectory_batch.trajectory_batch_sha256,
        system=candidate_batch.system,
        case_count=len(outcomes),
        overall=_summary(outcomes),
        slices=slices,
        outcomes=tuple(outcomes),
    )
    digest = agent_trajectory_report_sha256(draft)
    return AgentTrajectoryScoreReport(
        **draft.model_dump(mode="python", exclude={"report_id", "report_sha256"}),
        report_id=f"trajectory-report-{digest[:20]}",
        report_sha256=digest,
    )

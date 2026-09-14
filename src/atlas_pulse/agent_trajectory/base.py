"""Strict, content-addressed observations for offline agent trajectories."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from atlas_pulse.evaluation.base import StrictModel
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.grounded_answer_evaluation.base import (
    GroundedAnswerCandidateBatch,
    GroundedAnswerResponse,
    GroundedAnswerTask,
)
from atlas_pulse.relationship_evaluation.candidates import CandidateSystemDefinition

AGENT_TRAJECTORY_SCHEMA_VERSION = "1.0.0"
AGENT_TRAJECTORY_RULE_VERSION = "observable-agent-trajectory-v1"
AGENT_TRAJECTORY_IDENTITY_ALGORITHM = "sha256-canonical-json-v1"
AGENT_TRAJECTORY_CAVEATS = (
    "Trajectories contain only observable action metadata, identifiers, timing, and capability "
    "observations; they never contain hidden reasoning or chain-of-thought.",
    "The candidate ran only in the separate offline evaluation sandbox. Importing or scoring a "
    "trajectory cannot invoke a model, expose an answer endpoint, or perform a side effect.",
    "Capability observations are runner evidence, not a security proof; target-runtime isolation "
    "and human release remain separate gates.",
    "Every trajectory batch and downstream report remains blocked from production promotion.",
)

TrajectoryAction = Literal[
    "inspect_evidence",
    "draft_claim",
    "choose_abstention",
    "finalize",
]
GroundedAnswerResponseStatus = Literal["answered", "abstained"]


class AgentTrajectoryStep(StrictModel):
    """One bounded observable action without free-form reasoning content."""

    sequence: int = Field(ge=1, le=64)
    action: TrajectoryAction
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=8)
    claim_ids: tuple[str, ...] = Field(default=(), max_length=1)
    duration_ms: float = Field(ge=0, allow_inf_nan=False)

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("trajectory evidence IDs must be unique and canonically ordered")
        if any(
            len(item) != 73
            or not item.startswith("evidence-")
            or any(character not in "0123456789abcdef" for character in item[9:])
            for item in value
        ):
            raise ValueError("trajectory evidence IDs must use evidence-<sha256>")
        return value

    @field_validator("claim_ids")
    @classmethod
    def validate_claim_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("trajectory claim IDs must be unique and canonically ordered")
        if any(
            len(item) != 8 or not item.startswith("claim-") or not item[6:].isdigit()
            for item in value
        ):
            raise ValueError("trajectory claim IDs must use claim-NN")
        return value

    @model_validator(mode="after")
    def validate_action_shape(self) -> AgentTrajectoryStep:
        if self.action == "inspect_evidence":
            if not self.evidence_ids or self.claim_ids:
                raise ValueError("inspect_evidence requires evidence IDs and no claim IDs")
        elif self.action == "draft_claim":
            if not self.evidence_ids or len(self.claim_ids) != 1:
                raise ValueError("draft_claim requires evidence IDs and exactly one claim ID")
        elif self.evidence_ids or self.claim_ids:
            raise ValueError(f"{self.action} cannot contain evidence or claim IDs")
        return self


class AgentCapabilityObservation(StrictModel):
    """Instrumented capability use observed during one offline candidate case."""

    network_accessed: bool
    tools_invoked: bool
    external_side_effects_performed: bool


class AgentTrajectorySubmissionCase(StrictModel):
    """Protected case identity plus trace fields completed by an external runner."""

    case_id: str = Field(pattern=r"^answer-case-[0-9a-f]{20}$")
    query_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    steps: tuple[AgentTrajectoryStep, ...] | None = Field(default=None, max_length=64)
    capabilities: AgentCapabilityObservation | None = None


class AgentTrajectorySubmission(StrictModel):
    """Gold-free trajectory template bound to one exact task and candidate batch."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    task_id: str = Field(pattern=r"^grounded-task-[0-9a-f]{20}$")
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_batch_id: str = Field(pattern=r"^grounded-batch-[0-9a-f]{20}$")
    candidate_batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: tuple[AgentTrajectorySubmissionCase, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_cases(self) -> AgentTrajectorySubmission:
        case_ids = tuple(case.case_id for case in self.cases)
        if case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("trajectory submission cases must be unique and canonically ordered")
        return self


class AgentTrajectoryCase(StrictModel):
    """One imported observable trace bound to the exact candidate response."""

    case_id: str = Field(pattern=r"^answer-case-[0-9a-f]{20}$")
    query_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    pack_id: str = Field(pattern=r"^pack-[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_status: GroundedAnswerResponseStatus
    available_evidence_ids: tuple[str, ...]
    required_evidence_ids: tuple[str, ...]
    required_claim_ids: tuple[str, ...]
    steps: tuple[AgentTrajectoryStep, ...] = Field(min_length=1, max_length=64)
    capabilities: AgentCapabilityObservation
    input_tokens: int = Field(ge=1)
    output_tokens: int = Field(ge=1)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)

    @field_validator("available_evidence_ids", "required_evidence_ids")
    @classmethod
    def validate_evidence_id_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("trajectory evidence bindings must be unique and ordered")
        if any(
            len(item) != 73
            or not item.startswith("evidence-")
            or any(character not in "0123456789abcdef" for character in item[9:])
            for item in value
        ):
            raise ValueError("trajectory evidence bindings must use evidence-<sha256>")
        return value

    @field_validator("required_claim_ids")
    @classmethod
    def validate_claim_id_set(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("trajectory claim bindings must be unique and ordered")
        if any(
            len(item) != 8 or not item.startswith("claim-") or not item[6:].isdigit()
            for item in value
        ):
            raise ValueError("trajectory claim bindings must use claim-NN")
        return value

    @model_validator(mode="after")
    def validate_trace(self) -> AgentTrajectoryCase:
        sequences = tuple(step.sequence for step in self.steps)
        if sequences != tuple(range(1, len(self.steps) + 1)):
            raise ValueError("trajectory steps must be contiguous and one-based")
        final_steps = tuple(step for step in self.steps if step.action == "finalize")
        if len(final_steps) != 1 or self.steps[-1].action != "finalize":
            raise ValueError("trajectory requires exactly one final step in last position")
        available = set(self.available_evidence_ids)
        required = set(self.required_evidence_ids)
        if not required <= available:
            raise ValueError("required evidence IDs must belong to the bound evidence pack")
        observed_evidence = {
            evidence_id for step in self.steps for evidence_id in step.evidence_ids
        }
        if not observed_evidence <= available:
            raise ValueError("trajectory steps reference evidence outside the bound task case")
        observed_claims = {claim_id for step in self.steps for claim_id in step.claim_ids}
        if not observed_claims <= set(self.required_claim_ids):
            raise ValueError("trajectory steps reference claims outside the candidate response")
        expected_empty = self.response_status == "abstained"
        if expected_empty != (not self.required_claim_ids and not self.required_evidence_ids):
            raise ValueError("trajectory response bindings do not match response status")
        if sum(step.duration_ms for step in self.steps) > self.latency_ms + 1e-9:
            raise ValueError("trajectory step durations cannot exceed candidate case latency")
        return self


class AgentTrajectoryBatch(StrictModel):
    """Content-addressed observable traces for one immutable offline candidate batch."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    rule_version: Literal["observable-agent-trajectory-v1"] = "observable-agent-trajectory-v1"
    identity_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    trajectory_batch_id: str = Field(pattern=r"^trajectory-batch-[0-9a-f]{20}$")
    trajectory_batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(pattern=r"^grounded-task-[0-9a-f]{20}$")
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_batch_id: str = Field(pattern=r"^grounded-batch-[0-9a-f]{20}$")
    candidate_batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    system: CandidateSystemDefinition
    case_count: int = Field(ge=1)
    cases: tuple[AgentTrajectoryCase, ...] = Field(min_length=1, max_length=100)
    promotion_status: Literal["blocked"] = "blocked"
    execution_authorized: Literal[False] = False
    caveats: tuple[str, ...] = AGENT_TRAJECTORY_CAVEATS

    @model_validator(mode="after")
    def validate_batch(self) -> AgentTrajectoryBatch:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("trajectory observed_at must be timezone-aware")
        if self.case_count != len(self.cases):
            raise ValueError("trajectory case_count must match cases")
        case_ids = tuple(case.case_id for case in self.cases)
        if case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("trajectory cases must be unique and canonically ordered")
        if (
            self.promotion_status != "blocked"
            or self.execution_authorized is not False
            or self.caveats != AGENT_TRAJECTORY_CAVEATS
        ):
            raise ValueError("trajectory batches must retain the closed execution boundary")
        expected = agent_trajectory_batch_sha256(self)
        if self.trajectory_batch_sha256 != expected:
            raise ValueError("trajectory batch SHA-256 must match its exact observations")
        if self.trajectory_batch_id != f"trajectory-batch-{expected[:20]}":
            raise ValueError("trajectory batch ID must match its SHA-256")
        return self


def _response_sha256(response: GroundedAnswerResponse) -> str:
    return canonical_sha256(response.model_dump(mode="json", exclude_none=False))


def agent_trajectory_batch_sha256(batch: AgentTrajectoryBatch) -> str:
    """Hash a trajectory batch without its self-describing identity fields."""
    return canonical_sha256(
        batch.model_dump(
            mode="json",
            exclude={"trajectory_batch_id", "trajectory_batch_sha256"},
            exclude_none=False,
        )
    )


def _validate_task_batch(task: GroundedAnswerTask, batch: GroundedAnswerCandidateBatch) -> None:
    if batch.task_id != task.task_id or batch.task_sha256 != task.task_sha256:
        raise ValueError("grounded-answer candidate batch does not match the exact task")
    task_cases = tuple(case.case_id for case in task.cases)
    batch_cases = tuple(case.case_id for case in batch.cases)
    if task_cases != batch_cases:
        raise ValueError("grounded-answer candidate cases do not exactly cover the task")


def build_agent_trajectory_submission(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
) -> AgentTrajectorySubmission:
    """Create a protected, trace-empty template for an external offline runner."""
    _validate_task_batch(task, batch)
    return AgentTrajectorySubmission(
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        candidate_batch_id=batch.batch_id,
        candidate_batch_sha256=batch.batch_sha256,
        cases=tuple(
            AgentTrajectorySubmissionCase(case_id=case.case_id, query_id=case.query_id)
            for case in task.cases
        ),
    )


def apply_agent_trajectory_submission(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
    submission: AgentTrajectorySubmission,
    *,
    observed_at: datetime | None = None,
) -> AgentTrajectoryBatch:
    """Import complete traces while protecting every upstream task and response identity."""
    _validate_task_batch(task, batch)
    if (
        submission.task_id != task.task_id
        or submission.task_sha256 != task.task_sha256
        or submission.candidate_batch_id != batch.batch_id
        or submission.candidate_batch_sha256 != batch.batch_sha256
    ):
        raise ValueError("trajectory submission does not match the exact task and candidate batch")

    submitted = {case.case_id: case for case in submission.cases}
    if set(submitted) != {case.case_id for case in task.cases}:
        raise ValueError("trajectory submission cases do not exactly cover the task")
    task_cases = {case.case_id: case for case in task.cases}
    candidate_cases = {case.case_id: case for case in batch.cases}
    imported: list[AgentTrajectoryCase] = []
    for case_id in sorted(task_cases):
        task_case = task_cases[case_id]
        candidate = candidate_cases[case_id]
        row = submitted[case_id]
        if row.query_id != task_case.query_id or row.query_id != candidate.query_id:
            raise ValueError(f"trajectory submission changed protected query ID for {case_id}")
        if row.steps is None or row.capabilities is None:
            raise ValueError(f"trajectory submission case {case_id} is incomplete")
        available_evidence = tuple(sorted(item.evidence_id for item in task_case.evidence))
        claims = candidate.response.claims
        required_evidence = tuple(
            sorted({evidence_id for claim in claims for evidence_id in claim.evidence_ids})
        )
        imported.append(
            AgentTrajectoryCase(
                case_id=case_id,
                query_id=task_case.query_id,
                pack_id=task_case.pack_id,
                response_sha256=_response_sha256(candidate.response),
                response_status=candidate.response.status,
                available_evidence_ids=available_evidence,
                required_evidence_ids=required_evidence,
                required_claim_ids=tuple(sorted(claim.claim_id for claim in claims)),
                steps=row.steps,
                capabilities=row.capabilities,
                input_tokens=candidate.input_tokens,
                output_tokens=candidate.output_tokens,
                latency_ms=candidate.latency_ms,
            )
        )

    timestamp = observed_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("trajectory observed_at must be timezone-aware")
    draft = AgentTrajectoryBatch.model_construct(
        trajectory_batch_id="trajectory-batch-" + "0" * 20,
        trajectory_batch_sha256="0" * 64,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        candidate_batch_id=batch.batch_id,
        candidate_batch_sha256=batch.batch_sha256,
        observed_at=timestamp.astimezone(UTC),
        system=batch.system,
        case_count=len(imported),
        cases=tuple(imported),
    )
    digest = agent_trajectory_batch_sha256(draft)
    return AgentTrajectoryBatch(
        **draft.model_dump(
            mode="python",
            exclude={"trajectory_batch_id", "trajectory_batch_sha256"},
        ),
        trajectory_batch_id=f"trajectory-batch-{digest[:20]}",
        trajectory_batch_sha256=digest,
    )

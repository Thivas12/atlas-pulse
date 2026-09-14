"""Import complete, tokenizer-measured grounded-answer candidate outputs."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from atlas_pulse.grounded_answer_evaluation.base import (
    GroundedAnswerCandidateBatch,
    GroundedAnswerCandidateCase,
    GroundedAnswerSubmission,
    GroundedAnswerSubmissionCase,
    GroundedAnswerTask,
    grounded_answer_batch_sha256,
)
from atlas_pulse.relationship_evaluation.candidates import CandidateSystemDefinition

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def build_grounded_answer_submission(task: GroundedAnswerTask) -> GroundedAnswerSubmission:
    """Create an exact blank JSON template containing no review or answer labels."""
    return GroundedAnswerSubmission(
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        cases=tuple(
            GroundedAnswerSubmissionCase(case_id=case.case_id, query_id=case.query_id)
            for case in task.cases
        ),
    )


def _integer_parameter(
    system: CandidateSystemDefinition,
    key: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = system.parameters.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(
            f"grounded-answer candidate parameter {key!r} must be an integer between "
            f"{minimum} and {maximum}"
        )
    return value


def _candidate_token_limits(system: CandidateSystemDefinition) -> tuple[int, int]:
    context_length = _integer_parameter(
        system,
        "context_length",
        minimum=256,
        maximum=1_000_000,
    )
    max_output_tokens = _integer_parameter(
        system,
        "max_output_tokens",
        minimum=1,
        maximum=context_length,
    )
    tokenizer_hash = system.parameters.get("tokenizer_artifact_sha256")
    if not isinstance(tokenizer_hash, str) or _SHA256.fullmatch(tokenizer_hash) is None:
        raise ValueError("grounded-answer candidate requires a tokenizer_artifact_sha256 parameter")
    return context_length, max_output_tokens


def apply_grounded_answer_submission(
    task: GroundedAnswerTask,
    submission: GroundedAnswerSubmission,
    *,
    system: CandidateSystemDefinition,
    generated_at: datetime | None = None,
) -> GroundedAnswerCandidateBatch:
    """Bind complete cited answers to one exact task and immutable candidate identity."""
    if submission.task_id != task.task_id or submission.task_sha256 != task.task_sha256:
        raise ValueError("grounded-answer submission does not match the exact task")
    expected_identities = [(case.case_id, case.query_id) for case in task.cases]
    actual_identities = [(case.case_id, case.query_id) for case in submission.cases]
    if actual_identities != expected_identities:
        raise ValueError("grounded-answer submission changed protected case identities")
    context_length, max_output_tokens = _candidate_token_limits(system)
    task_cases = {case.case_id: case for case in task.cases}
    completed: list[GroundedAnswerCandidateCase] = []
    for submitted in submission.cases:
        if (
            submitted.response is None
            or submitted.input_tokens is None
            or submitted.output_tokens is None
            or submitted.latency_ms is None
        ):
            raise ValueError("grounded-answer submission must complete every candidate field")
        task_case = task_cases[submitted.case_id]
        response = submitted.response
        if task_case.pack_status == "no_traceable_evidence":
            if (
                response.status != "abstained"
                or response.abstention_reason != "no_traceable_evidence"
            ):
                raise ValueError("cases without traceable evidence require the matching abstention")
        elif response.abstention_reason == "no_traceable_evidence":
            raise ValueError("traceable-evidence cases cannot claim that no evidence was available")
        evidence_ids = {item.evidence_id for item in task_case.evidence}
        cited_ids = {evidence_id for claim in response.claims for evidence_id in claim.evidence_ids}
        unknown = cited_ids - evidence_ids
        if unknown:
            raise ValueError(
                "grounded-answer claims cite evidence outside the exact task case: "
                + ", ".join(sorted(unknown))
            )
        if submitted.input_tokens + max_output_tokens > context_length:
            raise ValueError("candidate prompt plus output budget exceeds its context length")
        if submitted.output_tokens > max_output_tokens:
            raise ValueError("candidate output token count exceeds its declared maximum")
        completed.append(
            GroundedAnswerCandidateCase(
                case_id=submitted.case_id,
                query_id=submitted.query_id,
                response=response,
                input_tokens=submitted.input_tokens,
                output_tokens=submitted.output_tokens,
                latency_ms=submitted.latency_ms,
            )
        )
    timestamp = generated_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("grounded-answer batch generated_at must be timezone-aware")
    cases = tuple(completed)
    draft = GroundedAnswerCandidateBatch.model_construct(
        schema_version="1.0.0",
        batch_id="grounded-batch-" + "0" * 20,
        batch_sha256="0" * 64,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        generated_at=timestamp,
        system=system,
        case_count=len(cases),
        cases=cases,
        review_status="awaiting_human_review",
        promotion_status="blocked",
    )
    digest = grounded_answer_batch_sha256(draft)
    return GroundedAnswerCandidateBatch(
        batch_id=f"grounded-batch-{digest[:20]}",
        batch_sha256=digest,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        generated_at=timestamp,
        system=system,
        case_count=len(cases),
        cases=cases,
    )

"""Human-only faithfulness, citation, relevance, and abstention measurements."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, model_validator

from atlas_pulse.evaluation.base import StrictModel
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.grounded_answer_evaluation.base import (
    GroundedAnswerCandidateBatch,
    GroundedAnswerCandidateCase,
    GroundedAnswerJudgment,
    GroundedAnswerStatus,
    GroundedAnswerTask,
    ReviewedGroundedAnswerBatch,
)
from atlas_pulse.relationship_evaluation.candidates import CandidateSystemDefinition

_PROMOTION_BLOCKERS = (
    "A second independent review and disagreement-only adjudication are still required.",
    "No representative grounded-answer quality, citation, abstention, or latency thresholds are approved.",
    "Candidate token and latency measurements require reproduction on the target execution hardware.",
    "The default-deny agent policy remains unchanged; model execution and production answers stay disabled.",
)

_REPORT_CAVEATS = (
    "This report summarizes one human first-pass review and is not adjudicated gold or a production release decision.",
    "Claim support is measured only against the bounded retrieved excerpts; it does not verify source truth, freshness, or completeness.",
    "Citation quality measures whether the cited excerpts support a claim, not whether the upstream public source is factually correct.",
    "A strict case pass is descriptive: every cited claim was fully supported, citations were complete, and the answer was directly relevant, or the abstention was judged appropriate.",
)


class GroundedAnswerMetricSummary(StrictModel):
    """Aggregate measurements for the complete review or one declared slice."""

    case_count: int = Field(ge=1)
    answered_case_count: int = Field(ge=0)
    abstained_case_count: int = Field(ge=0)
    claim_count: int = Field(ge=0)
    mean_support_grade_0_to_3: float | None = Field(default=None, ge=0, le=3)
    fully_supported_claim_rate: float | None = Field(default=None, ge=0, le=1)
    unsupported_or_contradicted_claim_rate: float | None = Field(default=None, ge=0, le=1)
    mean_citation_quality_0_to_2: float | None = Field(default=None, ge=0, le=2)
    complete_citation_rate: float | None = Field(default=None, ge=0, le=1)
    mean_answer_relevance_0_to_2: float | None = Field(default=None, ge=0, le=2)
    appropriate_abstention_rate: float | None = Field(default=None, ge=0, le=1)
    strict_case_pass_rate: float = Field(ge=0, le=1)
    mean_input_tokens: float = Field(ge=0)
    mean_output_tokens: float = Field(ge=0)
    mean_latency_ms: float = Field(ge=0)
    p95_latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> GroundedAnswerMetricSummary:
        if self.answered_case_count + self.abstained_case_count != self.case_count:
            raise ValueError("grounded-answer response counts must cover every case")
        if self.claim_count == 0 and any(
            value is not None
            for value in (
                self.mean_support_grade_0_to_3,
                self.fully_supported_claim_rate,
                self.unsupported_or_contradicted_claim_rate,
                self.mean_citation_quality_0_to_2,
                self.complete_citation_rate,
                self.mean_answer_relevance_0_to_2,
            )
        ):
            raise ValueError("claim metrics must be null when a slice has no answered claims")
        if self.abstained_case_count == 0 and self.appropriate_abstention_rate is not None:
            raise ValueError("abstention rate must be null when a slice has no abstentions")
        return self


class GroundedAnswerCaseOutcome(StrictModel):
    """Inspectable reviewed outcome for one task case."""

    case_id: str = Field(pattern=r"^answer-case-[0-9a-f]{20}$")
    query_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    response_status: GroundedAnswerStatus
    claim_count: int = Field(ge=0, le=8)
    minimum_support_grade: int | None = Field(default=None, ge=0, le=3)
    minimum_citation_quality: int | None = Field(default=None, ge=0, le=2)
    answer_relevance: int | None = Field(default=None, ge=0, le=2)
    abstention_appropriate: bool | None = None
    strict_pass: bool

    @model_validator(mode="after")
    def validate_outcome(self) -> GroundedAnswerCaseOutcome:
        claim_values = (
            self.minimum_support_grade,
            self.minimum_citation_quality,
            self.answer_relevance,
        )
        if self.response_status == "answered":
            if self.claim_count == 0 or any(value is None for value in claim_values):
                raise ValueError("answered outcomes require claim measurements")
            if self.abstention_appropriate is not None:
                raise ValueError("answered outcomes cannot contain an abstention judgment")
            expected = claim_values == (3, 2, 2)
        else:
            if self.claim_count != 0 or any(value is not None for value in claim_values):
                raise ValueError("abstained outcomes cannot contain claim measurements")
            if self.abstention_appropriate is None:
                raise ValueError("abstained outcomes require an appropriateness judgment")
            expected = self.abstention_appropriate
        if self.strict_pass != expected:
            raise ValueError("strict_pass must match the recorded human judgments")
        return self


class GroundedAnswerEvaluationReport(StrictModel):
    """Content-addressed descriptive report that cannot promote a candidate."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    report_id: str = Field(pattern=r"^grounded-report-[0-9a-f]{20}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    task_id: str = Field(pattern=r"^grounded-task-[0-9a-f]{20}$")
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    batch_id: str = Field(pattern=r"^grounded-batch-[0-9a-f]{20}$")
    batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_id: str = Field(pattern=r"^grounded-review-[0-9a-f]{20}$")
    review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=2, max_length=200)
    system: CandidateSystemDefinition
    overall: GroundedAnswerMetricSummary
    slices: dict[str, GroundedAnswerMetricSummary]
    outcomes: tuple[GroundedAnswerCaseOutcome, ...] = Field(min_length=1)
    promotion_status: Literal["blocked"] = "blocked"
    promotion_blockers: tuple[str, ...] = _PROMOTION_BLOCKERS
    caveats: tuple[str, ...] = _REPORT_CAVEATS

    @model_validator(mode="after")
    def validate_report(self) -> GroundedAnswerEvaluationReport:
        if self.generated_at.tzinfo is None:
            raise ValueError("grounded-answer report generated_at must be timezone-aware")
        case_ids = [case.case_id for case in self.outcomes]
        if len(case_ids) != len(set(case_ids)) or case_ids != sorted(case_ids):
            raise ValueError("grounded-answer report outcomes must be unique and ordered")
        if self.overall.case_count != len(self.outcomes):
            raise ValueError("grounded-answer report overall count must match outcomes")
        if any(not name or name != name.strip().casefold() for name in self.slices):
            raise ValueError("grounded-answer report slice names must be normalized")
        if (
            self.promotion_status != "blocked"
            or self.promotion_blockers != _PROMOTION_BLOCKERS
            or self.caveats != _REPORT_CAVEATS
        ):
            raise ValueError("grounded-answer report must retain its closed promotion boundary")
        expected_hash = grounded_answer_report_sha256(self)
        if self.report_sha256 != expected_hash:
            raise ValueError("grounded-answer report_sha256 must match exact measurements")
        if self.report_id != f"grounded-report-{expected_hash[:20]}":
            raise ValueError("grounded-answer report_id must match report_sha256")
        return self


def grounded_answer_report_sha256(report: GroundedAnswerEvaluationReport) -> str:
    """Hash a report without its self-describing identity fields."""
    return canonical_sha256(
        report.model_dump(mode="json", exclude={"report_id", "report_sha256"}, exclude_none=False)
    )


def _mean(values: Sequence[int | float]) -> float | None:
    return sum(values) / len(values) if values else None


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _case_judgments(
    review: ReviewedGroundedAnswerBatch,
) -> dict[str, tuple[GroundedAnswerJudgment, ...]]:
    grouped: dict[str, list[GroundedAnswerJudgment]] = {}
    for judgment in review.judgments:
        grouped.setdefault(judgment.case_id, []).append(judgment)
    return {case_id: tuple(values) for case_id, values in grouped.items()}


def _outcome(
    case: GroundedAnswerCandidateCase,
    judgments: tuple[GroundedAnswerJudgment, ...],
) -> GroundedAnswerCaseOutcome:
    if case.response.status == "abstained":
        if len(judgments) != 1 or judgments[0].claim_id is not None:
            raise ValueError("abstained cases require exactly one abstention judgment")
        appropriate = judgments[0].abstention_appropriate
        if appropriate is None:
            raise ValueError("abstained cases require an appropriateness judgment")
        return GroundedAnswerCaseOutcome(
            case_id=case.case_id,
            query_id=case.query_id,
            response_status="abstained",
            claim_count=0,
            abstention_appropriate=appropriate,
            strict_pass=appropriate,
        )
    expected_ids = [claim.claim_id for claim in case.response.claims]
    actual_ids = [item.claim_id for item in judgments]
    if actual_ids != expected_ids:
        raise ValueError("answered case judgments do not exactly cover its claims")
    support = [item.support_grade for item in judgments]
    citation = [item.citation_quality for item in judgments]
    if any(value is None for value in support) or any(value is None for value in citation):
        raise ValueError("answered case judgments require all claim grades")
    support_values = [int(value) for value in support if value is not None]
    citation_values = [int(value) for value in citation if value is not None]
    relevance = judgments[0].answer_relevance
    if relevance is None:
        raise ValueError("answered case judgments require answer relevance")
    minimum_support = min(support_values)
    minimum_citation = min(citation_values)
    strict_pass = minimum_support == 3 and minimum_citation == 2 and relevance == 2
    return GroundedAnswerCaseOutcome(
        case_id=case.case_id,
        query_id=case.query_id,
        response_status="answered",
        claim_count=len(case.response.claims),
        minimum_support_grade=minimum_support,
        minimum_citation_quality=minimum_citation,
        answer_relevance=relevance,
        strict_pass=strict_pass,
    )


def _summary(
    cases: tuple[GroundedAnswerCandidateCase, ...],
    judgments_by_case: dict[str, tuple[GroundedAnswerJudgment, ...]],
    outcomes_by_case: dict[str, GroundedAnswerCaseOutcome],
) -> GroundedAnswerMetricSummary:
    claim_judgments = [
        judgment
        for case in cases
        for judgment in judgments_by_case[case.case_id]
        if judgment.claim_id is not None
    ]
    support = [
        int(item.support_grade) for item in claim_judgments if item.support_grade is not None
    ]
    citation = [
        int(item.citation_quality) for item in claim_judgments if item.citation_quality is not None
    ]
    relevance = [
        int(item.answer_relevance) for item in claim_judgments if item.answer_relevance is not None
    ]
    abstentions = [
        judgments_by_case[case.case_id][0] for case in cases if case.response.status == "abstained"
    ]
    latency = [case.latency_ms for case in cases]
    answered = sum(case.response.status == "answered" for case in cases)
    strict_passes = sum(outcomes_by_case[case.case_id].strict_pass for case in cases)
    return GroundedAnswerMetricSummary(
        case_count=len(cases),
        answered_case_count=answered,
        abstained_case_count=len(cases) - answered,
        claim_count=len(claim_judgments),
        mean_support_grade_0_to_3=_mean(support),
        fully_supported_claim_rate=_rate(sum(value == 3 for value in support), len(support)),
        unsupported_or_contradicted_claim_rate=_rate(
            sum(value <= 1 for value in support), len(support)
        ),
        mean_citation_quality_0_to_2=_mean(citation),
        complete_citation_rate=_rate(sum(value == 2 for value in citation), len(citation)),
        mean_answer_relevance_0_to_2=_mean(relevance),
        appropriate_abstention_rate=_rate(
            sum(item.abstention_appropriate is True for item in abstentions),
            len(abstentions),
        ),
        strict_case_pass_rate=strict_passes / len(cases),
        mean_input_tokens=sum(case.input_tokens for case in cases) / len(cases),
        mean_output_tokens=sum(case.output_tokens for case in cases) / len(cases),
        mean_latency_ms=sum(latency) / len(latency),
        p95_latency_ms=_percentile(latency, 0.95),
    )


def score_grounded_answer_review(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
    review: ReviewedGroundedAnswerBatch,
    *,
    generated_at: datetime | None = None,
) -> GroundedAnswerEvaluationReport:
    """Score one complete first-pass review without creating a release verdict."""
    if batch.task_id != task.task_id or batch.task_sha256 != task.task_sha256:
        raise ValueError("grounded-answer batch does not match the exact task")
    if (
        review.task_id != task.task_id
        or review.task_sha256 != task.task_sha256
        or review.batch_id != batch.batch_id
        or review.batch_sha256 != batch.batch_sha256
    ):
        raise ValueError("grounded-answer review does not match the exact task and batch")
    task_cases = {case.case_id: case for case in task.cases}
    batch_cases = {case.case_id: case for case in batch.cases}
    if set(task_cases) != set(batch_cases):
        raise ValueError("grounded-answer batch cases do not exactly cover the task")
    judgments_by_case = _case_judgments(review)
    if set(judgments_by_case) != set(batch_cases):
        raise ValueError("grounded-answer review cases do not exactly cover the batch")
    outcomes = tuple(
        sorted(
            (_outcome(case, judgments_by_case[case.case_id]) for case in batch.cases),
            key=lambda item: item.case_id,
        )
    )
    outcomes_by_case = {item.case_id: item for item in outcomes}
    slices: dict[str, GroundedAnswerMetricSummary] = {}
    slice_names = sorted({name for case in task.cases for name in case.slices})
    for name in slice_names:
        selected = tuple(case for case in batch.cases if name in task_cases[case.case_id].slices)
        slices[name] = _summary(selected, judgments_by_case, outcomes_by_case)
    timestamp = generated_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("grounded-answer report generated_at must be timezone-aware")
    overall = _summary(batch.cases, judgments_by_case, outcomes_by_case)
    draft = GroundedAnswerEvaluationReport.model_construct(
        schema_version="1.0.0",
        report_id="grounded-report-" + "0" * 20,
        report_sha256="0" * 64,
        generated_at=timestamp,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        batch_id=batch.batch_id,
        batch_sha256=batch.batch_sha256,
        review_id=review.review_id,
        review_sha256=review.review_sha256,
        reviewer=review.reviewer,
        system=batch.system,
        overall=overall,
        slices=slices,
        outcomes=outcomes,
        promotion_status="blocked",
        promotion_blockers=_PROMOTION_BLOCKERS,
        caveats=_REPORT_CAVEATS,
    )
    digest = grounded_answer_report_sha256(draft)
    return GroundedAnswerEvaluationReport(
        report_id=f"grounded-report-{digest[:20]}",
        report_sha256=digest,
        generated_at=timestamp,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        batch_id=batch.batch_id,
        batch_sha256=batch.batch_sha256,
        review_id=review.review_id,
        review_sha256=review.review_sha256,
        reviewer=review.reviewer,
        system=batch.system,
        overall=overall,
        slices=slices,
        outcomes=outcomes,
    )

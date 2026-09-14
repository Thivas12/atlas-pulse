"""Independent grounded-answer review agreement and blind adjudication."""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, cast

from pydantic import Field, field_validator, model_validator

from atlas_pulse.evaluation.base import StrictModel
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.grounded_answer_evaluation.base import (
    ADJUDICATED_GROUNDED_ANSWER_REVIEW_CAVEATS,
    GROUNDING_REVIEW_DIMENSIONS,
    GROUNDING_RUBRIC_VERSION,
    GroundedAnswerCandidateBatch,
    GroundedAnswerIndependentAdjudicationProvenance,
    GroundedAnswerJudgment,
    GroundedAnswerReviewDimension,
    GroundedAnswerTask,
    ReviewedGroundedAnswerBatch,
    grounded_answer_review_sha256,
)
from atlas_pulse.grounded_answer_evaluation.judgments import (
    GROUNDING_REVIEW_COLUMNS,
    grounded_answer_review_rows,
)

AgreementSchemaVersion = Literal["1.0.0"]
JudgmentValue = int | bool

_DIMENSION_SCALES: dict[GroundedAnswerReviewDimension, tuple[str, ...]] = {
    "support_grade": ("0", "1", "2", "3"),
    "citation_quality": ("0", "1", "2"),
    "answer_relevance": ("0", "1", "2"),
    "abstention_appropriate": ("no", "yes"),
}

GROUNDING_ADJUDICATION_COLUMNS = (
    *GROUNDING_REVIEW_COLUMNS[:14],
    "agreement_report_id",
    "disputed_fields",
    "review_a_support_0_to_3",
    "review_a_citation_quality_0_to_2",
    "review_a_answer_relevance_0_to_2",
    "review_a_abstention_appropriate",
    "review_a_rationale",
    "review_b_support_0_to_3",
    "review_b_citation_quality_0_to_2",
    "review_b_answer_relevance_0_to_2",
    "review_b_abstention_appropriate",
    "review_b_rationale",
    "adjudicated_support_0_to_3",
    "adjudicated_citation_quality_0_to_2",
    "adjudicated_answer_relevance_0_to_2",
    "adjudicated_abstention_appropriate",
    "adjudication_rationale",
)

_FINAL_COLUMNS: dict[GroundedAnswerReviewDimension, str] = {
    "support_grade": "adjudicated_support_0_to_3",
    "citation_quality": "adjudicated_citation_quality_0_to_2",
    "answer_relevance": "adjudicated_answer_relevance_0_to_2",
    "abstention_appropriate": "adjudicated_abstention_appropriate",
}
_PROTECTED_ADJUDICATION_COLUMNS = GROUNDING_ADJUDICATION_COLUMNS[:-5]

_AGREEMENT_CAVEATS = (
    "Agreement measures reviewer consistency over bounded evidence, not whether either judgment or upstream source is factually correct.",
    "The software verifies different reviewer identities and exact task, candidate, and judgment coverage, but cannot prove reviews were completed independently.",
    "Cohen's kappa is unweighted and undefined when marginal grade distributions make expected agreement exactly one.",
    "Only rubric fields with observations are reported; answer and abstention fields can be absent for homogeneous candidate batches.",
    "Candidate and model identity remain hidden from every human-editable review and adjudication CSV.",
)

_ADJUDICATION_CAVEATS = (
    "Fully agreed judgment rows inherit consensus; a separate adjudicator resolves only rubric fields on disputed rows.",
    "Reviewer identities and candidate/model identity are omitted from the adjudication CSV, and review A/B ordering is deterministically swapped per row.",
    "Final grades remain human judgments over bounded excerpts and do not verify source truth, freshness, completeness, or representativeness.",
    "The finalized artifact remains blocked and cannot authorize model, answer, policy, or agent execution.",
)


def _judgment_values(
    judgment: GroundedAnswerJudgment,
) -> dict[GroundedAnswerReviewDimension, str]:
    values: dict[GroundedAnswerReviewDimension, str] = {}
    for dimension in GROUNDING_REVIEW_DIMENSIONS:
        value = getattr(judgment, dimension)
        if value is None:
            continue
        if isinstance(value, bool):
            values[dimension] = "yes" if value else "no"
        else:
            values[dimension] = str(value)
    return values


def _disputed_fields(
    first: GroundedAnswerJudgment,
    second: GroundedAnswerJudgment,
) -> tuple[GroundedAnswerReviewDimension, ...]:
    first_values = _judgment_values(first)
    second_values = _judgment_values(second)
    if set(first_values) != set(second_values):
        raise ValueError("independent reviews must grade identical rubric fields")
    return tuple(
        dimension
        for dimension in GROUNDING_REVIEW_DIMENSIONS
        if dimension in first_values and first_values[dimension] != second_values[dimension]
    )


class GroundedAnswerReviewIdentity(StrictModel):
    """Content-addressed identity for one independent first-pass review."""

    reviewer: str = Field(min_length=2, max_length=200)
    reviewed_at: datetime
    review_id: str = Field(pattern=r"^grounded-review-[0-9a-f]{20}$")
    review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("reviewer")
    @classmethod
    def normalize_reviewer(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("reviewer identity must contain at least two characters")
        return normalized

    @model_validator(mode="after")
    def validate_identity(self) -> GroundedAnswerReviewIdentity:
        if self.reviewed_at.tzinfo is None:
            raise ValueError("review timestamp must be timezone-aware")
        if self.review_id != f"grounded-review-{self.review_sha256[:20]}":
            raise ValueError("review ID must match its review hash")
        return self


class GroundedAnswerDimensionAgreement(StrictModel):
    """Observed and chance-corrected exact agreement for one rubric dimension."""

    dimension: GroundedAnswerReviewDimension
    scale: tuple[str, ...] = Field(min_length=2)
    rating_count: int = Field(ge=1)
    agreement_count: int = Field(ge=0)
    disagreement_count: int = Field(ge=0)
    observed_agreement: float = Field(ge=0, le=1, allow_inf_nan=False)
    expected_agreement: float = Field(ge=0, le=1, allow_inf_nan=False)
    cohen_kappa: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    confusion_matrix: dict[str, dict[str, int]]

    @model_validator(mode="after")
    def validate_metrics(self) -> GroundedAnswerDimensionAgreement:
        if self.scale != _DIMENSION_SCALES[self.dimension]:
            raise ValueError("agreement scale must match the rubric dimension")
        if self.agreement_count + self.disagreement_count != self.rating_count:
            raise ValueError("agreement and disagreement counts must cover every rating")
        if set(self.confusion_matrix) != set(self.scale) or any(
            set(row) != set(self.scale) for row in self.confusion_matrix.values()
        ):
            raise ValueError("confusion matrix must contain the complete dimension scale")
        if any(count < 0 for row in self.confusion_matrix.values() for count in row.values()):
            raise ValueError("confusion matrix counts must be non-negative")
        total = sum(sum(row.values()) for row in self.confusion_matrix.values())
        agreements = sum(self.confusion_matrix[value][value] for value in self.scale)
        if total != self.rating_count or agreements != self.agreement_count:
            raise ValueError("confusion matrix counts must match agreement metrics")
        observed = agreements / total
        if abs(self.observed_agreement - observed) > 1e-12:
            raise ValueError("observed agreement must match the confusion matrix")
        first_counts = {value: sum(self.confusion_matrix[value].values()) for value in self.scale}
        second_counts = {
            value: sum(self.confusion_matrix[row][value] for row in self.scale)
            for value in self.scale
        }
        expected = sum(
            (first_counts[value] / total) * (second_counts[value] / total) for value in self.scale
        )
        if abs(self.expected_agreement - expected) > 1e-12:
            raise ValueError("expected agreement must match confusion-matrix marginals")
        if expected == 1:
            if self.cohen_kappa is not None:
                raise ValueError("Cohen kappa must be undefined when expected agreement is one")
        else:
            kappa = (observed - expected) / (1 - expected)
            if self.cohen_kappa is None or abs(self.cohen_kappa - kappa) > 1e-12:
                raise ValueError("Cohen kappa must match observed and expected agreement")
        return self


class GroundedAnswerJudgmentAgreement(StrictModel):
    """Exact row agreement plus the number of individual fields requiring resolution."""

    judgment_count: int = Field(ge=1)
    complete_agreement_count: int = Field(ge=0)
    disagreement_count: int = Field(ge=0)
    disputed_field_count: int = Field(ge=0)
    exact_judgment_agreement: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_summary(self) -> GroundedAnswerJudgmentAgreement:
        if self.complete_agreement_count + self.disagreement_count != self.judgment_count:
            raise ValueError("judgment agreement counts must cover every judgment")
        if (self.disagreement_count == 0) != (self.disputed_field_count == 0):
            raise ValueError("disagreement rows and fields must be zero together")
        if self.disputed_field_count < self.disagreement_count:
            raise ValueError("every disagreement row must contain a disputed field")
        expected = self.complete_agreement_count / self.judgment_count
        if abs(self.exact_judgment_agreement - expected) > 1e-12:
            raise ValueError("exact judgment agreement must match the row counts")
        return self


class GroundedAnswerReviewDisagreement(StrictModel):
    """One row containing at least one different independent rubric judgment."""

    case_id: str = Field(pattern=r"^answer-case-[0-9a-f]{20}$")
    claim_id: str | None = Field(default=None, pattern=r"^claim-[0-9]{2}$")
    disputed_fields: tuple[GroundedAnswerReviewDimension, ...] = Field(min_length=1)
    first_judgment: GroundedAnswerJudgment
    second_judgment: GroundedAnswerJudgment

    @model_validator(mode="after")
    def validate_disagreement(self) -> GroundedAnswerReviewDisagreement:
        identity = (self.case_id, self.claim_id)
        if (self.first_judgment.case_id, self.first_judgment.claim_id) != identity or (
            self.second_judgment.case_id,
            self.second_judgment.claim_id,
        ) != identity:
            raise ValueError("disagreement judgments must match the row identity")
        expected = _disputed_fields(self.first_judgment, self.second_judgment)
        if self.disputed_fields != expected:
            raise ValueError("disputed fields must exactly match the two reviews")
        return self


def _agreement_report_id(
    *,
    task_sha256: str,
    batch_sha256: str,
    review_sha256s: tuple[str, str],
) -> str:
    digest = canonical_sha256(
        {
            "contract": "independent-grounded-answer-review-v1",
            "task_sha256": task_sha256,
            "batch_sha256": batch_sha256,
            "review_sha256s": review_sha256s,
        }
    )
    return f"grounded-agreement-{digest[:20]}"


class IndependentGroundedAnswerReviewAgreementReport(StrictModel):
    """Agreement evidence over two exact, independently reviewed candidate batches."""

    schema_version: AgreementSchemaVersion = "1.0.0"
    report_id: str = Field(pattern=r"^grounded-agreement-[0-9a-f]{20}$")
    generated_at: datetime
    task_id: str = Field(pattern=r"^grounded-task-[0-9a-f]{20}$")
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    batch_id: str = Field(pattern=r"^grounded-batch-[0-9a-f]{20}$")
    batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rubric_version: Literal["grounded-brief-review-v1"] = GROUNDING_RUBRIC_VERSION
    first_review: GroundedAnswerReviewIdentity
    second_review: GroundedAnswerReviewIdentity
    judgments: GroundedAnswerJudgmentAgreement
    dimensions: dict[GroundedAnswerReviewDimension, GroundedAnswerDimensionAgreement] = Field(
        min_length=1
    )
    disagreements: tuple[GroundedAnswerReviewDisagreement, ...]
    promotion_status: Literal["blocked"] = "blocked"
    caveats: tuple[str, ...] = _AGREEMENT_CAVEATS

    @model_validator(mode="after")
    def validate_report(self) -> IndependentGroundedAnswerReviewAgreementReport:
        if self.generated_at.tzinfo is None:
            raise ValueError("agreement report timestamp must be timezone-aware")
        if self.task_id != f"grounded-task-{self.task_sha256[:20]}":
            raise ValueError("agreement task ID must match its task hash")
        if self.batch_id != f"grounded-batch-{self.batch_sha256[:20]}":
            raise ValueError("agreement batch ID must match its batch hash")
        reviewers = (self.first_review.reviewer, self.second_review.reviewer)
        if reviewers[0].casefold() == reviewers[1].casefold():
            raise ValueError("agreement requires two different reviewers")
        if reviewers != tuple(sorted(reviewers, key=lambda item: (item.casefold(), item))):
            raise ValueError("agreement reviews must use canonical reviewer order")
        if self.first_review.review_sha256 == self.second_review.review_sha256:
            raise ValueError("agreement reviews must have different content hashes")
        for dimension, agreement in self.dimensions.items():
            if agreement.dimension != dimension:
                raise ValueError("dimension agreement keys must match their payloads")
        disagreement_ids = [(item.case_id, item.claim_id or "") for item in self.disagreements]
        if disagreement_ids != sorted(disagreement_ids) or len(disagreement_ids) != len(
            set(disagreement_ids)
        ):
            raise ValueError("agreement disagreements must be unique and canonically ordered")
        if len(self.disagreements) != self.judgments.disagreement_count:
            raise ValueError("agreement report must retain every disputed judgment")
        disputed_fields = sum(len(item.disputed_fields) for item in self.disagreements)
        dimension_disagreements = sum(item.disagreement_count for item in self.dimensions.values())
        if disputed_fields != self.judgments.disputed_field_count or (
            disputed_fields != dimension_disagreements
        ):
            raise ValueError("agreement report must retain every disputed rubric field")
        expected_id = _agreement_report_id(
            task_sha256=self.task_sha256,
            batch_sha256=self.batch_sha256,
            review_sha256s=(
                self.first_review.review_sha256,
                self.second_review.review_sha256,
            ),
        )
        if self.report_id != expected_id:
            raise ValueError("agreement report ID must match its exact reviews")
        if self.promotion_status != "blocked" or self.caveats != _AGREEMENT_CAVEATS:
            raise ValueError("agreement report must retain its promotion boundary")
        return self


class GroundedAnswerAdjudicationDecision(StrictModel):
    """One final resolution for every disputed field on one review row."""

    case_id: str = Field(pattern=r"^answer-case-[0-9a-f]{20}$")
    claim_id: str | None = Field(default=None, pattern=r"^claim-[0-9]{2}$")
    disputed_fields: tuple[GroundedAnswerReviewDimension, ...] = Field(min_length=1)
    first_judgment: GroundedAnswerJudgment
    second_judgment: GroundedAnswerJudgment
    final_judgment: GroundedAnswerJudgment

    @model_validator(mode="after")
    def validate_decision(self) -> GroundedAnswerAdjudicationDecision:
        identity = (self.case_id, self.claim_id)
        judgments = (self.first_judgment, self.second_judgment, self.final_judgment)
        if any((item.case_id, item.claim_id) != identity for item in judgments):
            raise ValueError("adjudication judgments must match the decision identity")
        expected = _disputed_fields(self.first_judgment, self.second_judgment)
        if self.disputed_fields != expected:
            raise ValueError("adjudication fields must exactly match the prior disagreement")
        first_values = _judgment_values(self.first_judgment)
        final_values = _judgment_values(self.final_judgment)
        if set(first_values) != set(final_values):
            raise ValueError("final judgment must cover the same rubric fields")
        if any(
            final_values[dimension] != first_values[dimension]
            for dimension in first_values
            if dimension not in self.disputed_fields
        ):
            raise ValueError("adjudication cannot change an agreed rubric field")
        return self


def _adjudication_report_id(*, agreement_report_id: str, final_review_sha256: str) -> str:
    digest = canonical_sha256(
        {
            "contract": "grounded-answer-adjudication-v1",
            "agreement_report_id": agreement_report_id,
            "final_review_sha256": final_review_sha256,
        }
    )
    return f"grounded-adjudication-{digest[:20]}"


class GroundedAnswerAdjudicationReport(StrictModel):
    """Final decision record tied to one blocked, adjudicated review artifact."""

    schema_version: AgreementSchemaVersion = "1.0.0"
    report_id: str = Field(pattern=r"^grounded-adjudication-[0-9a-f]{20}$")
    agreement_report_id: str = Field(pattern=r"^grounded-agreement-[0-9a-f]{20}$")
    generated_at: datetime
    task_id: str = Field(pattern=r"^grounded-task-[0-9a-f]{20}$")
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    batch_id: str = Field(pattern=r"^grounded-batch-[0-9a-f]{20}$")
    batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    first_review: GroundedAnswerReviewIdentity
    second_review: GroundedAnswerReviewIdentity
    adjudicator: str = Field(min_length=2, max_length=200)
    judgment_count: int = Field(ge=1)
    inherited_agreement_count: int = Field(ge=0)
    adjudication_decision_count: int = Field(ge=0)
    adjudicated_field_count: int = Field(ge=0)
    final_review_id: str = Field(pattern=r"^grounded-review-[0-9a-f]{20}$")
    final_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decisions: tuple[GroundedAnswerAdjudicationDecision, ...]
    promotion_status: Literal["blocked"] = "blocked"
    caveats: tuple[str, ...] = _ADJUDICATION_CAVEATS

    @field_validator("adjudicator")
    @classmethod
    def normalize_adjudicator(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("adjudicator identity must contain at least two characters")
        return normalized

    @model_validator(mode="after")
    def validate_report(self) -> GroundedAnswerAdjudicationReport:
        if self.generated_at.tzinfo is None:
            raise ValueError("adjudication report timestamp must be timezone-aware")
        if self.task_id != f"grounded-task-{self.task_sha256[:20]}":
            raise ValueError("adjudication task ID must match its task hash")
        if self.batch_id != f"grounded-batch-{self.batch_sha256[:20]}":
            raise ValueError("adjudication batch ID must match its batch hash")
        reviewers = (self.first_review.reviewer, self.second_review.reviewer)
        if reviewers != tuple(sorted(reviewers, key=lambda item: (item.casefold(), item))):
            raise ValueError("adjudication reviews must use canonical reviewer order")
        if self.adjudicator.casefold() in {
            self.first_review.reviewer.casefold(),
            self.second_review.reviewer.casefold(),
        }:
            raise ValueError("adjudicator must be independent from both reviewers")
        if self.inherited_agreement_count + self.adjudication_decision_count != (
            self.judgment_count
        ):
            raise ValueError("consensus and adjudication counts must cover every judgment")
        if len(self.decisions) != self.adjudication_decision_count:
            raise ValueError("adjudication report must retain every decision")
        if sum(len(item.disputed_fields) for item in self.decisions) != (
            self.adjudicated_field_count
        ):
            raise ValueError("adjudication report must retain every resolved field")
        decision_ids = [(item.case_id, item.claim_id or "") for item in self.decisions]
        if decision_ids != sorted(decision_ids) or len(decision_ids) != len(set(decision_ids)):
            raise ValueError("adjudication decisions must be unique and canonically ordered")
        if self.final_review_id != f"grounded-review-{self.final_review_sha256[:20]}":
            raise ValueError("final review ID must match its hash")
        expected_agreement_id = _agreement_report_id(
            task_sha256=self.task_sha256,
            batch_sha256=self.batch_sha256,
            review_sha256s=(
                self.first_review.review_sha256,
                self.second_review.review_sha256,
            ),
        )
        if self.agreement_report_id != expected_agreement_id:
            raise ValueError("adjudication agreement ID must match its exact reviews")
        expected_id = _adjudication_report_id(
            agreement_report_id=self.agreement_report_id,
            final_review_sha256=self.final_review_sha256,
        )
        if self.report_id != expected_id:
            raise ValueError("adjudication report ID must match the final review")
        if self.promotion_status != "blocked" or self.caveats != _ADJUDICATION_CAVEATS:
            raise ValueError("adjudication report must retain its promotion boundary")
        return self


@dataclass(frozen=True, slots=True)
class GroundedAnswerAdjudicationSheet:
    """Candidate- and reviewer-blind CSV containing only disputed judgment rows."""

    content: str
    agreement_report: IndependentGroundedAnswerReviewAgreementReport
    pending_judgment_count: int
    pending_field_count: int


def _require_first_pass(review: ReviewedGroundedAnswerBatch, *, label: str) -> None:
    if review.review_status != "first_pass_complete" or review.adjudication is not None:
        raise ValueError(f"{label} grounded-answer review must be an independent first pass")


def _canonical_reviews(
    first: ReviewedGroundedAnswerBatch,
    second: ReviewedGroundedAnswerBatch,
) -> tuple[ReviewedGroundedAnswerBatch, ReviewedGroundedAnswerBatch]:
    _require_first_pass(first, label="first")
    _require_first_pass(second, label="second")
    if first.reviewer.casefold() == second.reviewer.casefold():
        raise ValueError("independent review requires two different reviewer identities")
    first_scope = (
        first.task_id,
        first.task_sha256,
        first.batch_id,
        first.batch_sha256,
        first.rubric_version,
    )
    second_scope = (
        second.task_id,
        second.task_sha256,
        second.batch_id,
        second.batch_sha256,
        second.rubric_version,
    )
    if first_scope != second_scope:
        raise ValueError("independent reviews must cover the exact same task and candidate batch")
    ordered = sorted((first, second), key=lambda item: (item.reviewer.casefold(), item.reviewer))
    return ordered[0], ordered[1]


def _review_pairs(
    first: ReviewedGroundedAnswerBatch,
    second: ReviewedGroundedAnswerBatch,
) -> tuple[tuple[GroundedAnswerJudgment, GroundedAnswerJudgment], ...]:
    second_rows = {(item.case_id, item.claim_id or ""): item for item in second.judgments}
    first_ids = {(item.case_id, item.claim_id or "") for item in first.judgments}
    if set(second_rows) != first_ids:
        raise ValueError("independent reviews must contain identical judgment rows")
    rows = tuple(
        ((item), second_rows[(item.case_id, item.claim_id or "")]) for item in first.judgments
    )
    for left, right in rows:
        _disputed_fields(left, right)
    return rows


def _dimension_agreement(
    dimension: GroundedAnswerReviewDimension,
    values: Sequence[tuple[str, str]],
) -> GroundedAnswerDimensionAgreement:
    if not values:
        raise ValueError("cannot measure agreement over an empty dimension")
    scale = _DIMENSION_SCALES[dimension]
    first_counts = Counter(first for first, _ in values)
    second_counts = Counter(second for _, second in values)
    rating_count = len(values)
    agreement_count = sum(first == second for first, second in values)
    observed = agreement_count / rating_count
    expected = sum(
        (first_counts[value] / rating_count) * (second_counts[value] / rating_count)
        for value in scale
    )
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    matrix = {
        first_value: {
            second_value: sum(
                first == first_value and second == second_value for first, second in values
            )
            for second_value in scale
        }
        for first_value in scale
    }
    return GroundedAnswerDimensionAgreement(
        dimension=dimension,
        scale=scale,
        rating_count=rating_count,
        agreement_count=agreement_count,
        disagreement_count=rating_count - agreement_count,
        observed_agreement=observed,
        expected_agreement=expected,
        cohen_kappa=kappa,
        confusion_matrix=matrix,
    )


def compare_grounded_answer_reviews(
    first: ReviewedGroundedAnswerBatch,
    second: ReviewedGroundedAnswerBatch,
    *,
    generated_at: datetime | None = None,
) -> IndependentGroundedAnswerReviewAgreementReport:
    """Compare two exact first-pass reviews without exposing the candidate to either reviewer."""
    first, second = _canonical_reviews(first, second)
    rows = _review_pairs(first, second)
    values_by_dimension: dict[GroundedAnswerReviewDimension, list[tuple[str, str]]] = defaultdict(
        list
    )
    disagreements: list[GroundedAnswerReviewDisagreement] = []
    for left, right in rows:
        left_values = _judgment_values(left)
        right_values = _judgment_values(right)
        disputed = _disputed_fields(left, right)
        for dimension, value in left_values.items():
            values_by_dimension[dimension].append((value, right_values[dimension]))
        if disputed:
            disagreements.append(
                GroundedAnswerReviewDisagreement(
                    case_id=left.case_id,
                    claim_id=left.claim_id,
                    disputed_fields=disputed,
                    first_judgment=left,
                    second_judgment=right,
                )
            )
    report_id = _agreement_report_id(
        task_sha256=first.task_sha256,
        batch_sha256=first.batch_sha256,
        review_sha256s=(first.review_sha256, second.review_sha256),
    )
    return IndependentGroundedAnswerReviewAgreementReport(
        report_id=report_id,
        generated_at=generated_at or datetime.now(UTC),
        task_id=first.task_id,
        task_sha256=first.task_sha256,
        batch_id=first.batch_id,
        batch_sha256=first.batch_sha256,
        first_review=GroundedAnswerReviewIdentity(
            reviewer=first.reviewer,
            reviewed_at=first.reviewed_at,
            review_id=first.review_id,
            review_sha256=first.review_sha256,
        ),
        second_review=GroundedAnswerReviewIdentity(
            reviewer=second.reviewer,
            reviewed_at=second.reviewed_at,
            review_id=second.review_id,
            review_sha256=second.review_sha256,
        ),
        judgments=GroundedAnswerJudgmentAgreement(
            judgment_count=len(rows),
            complete_agreement_count=len(rows) - len(disagreements),
            disagreement_count=len(disagreements),
            disputed_field_count=sum(len(item.disputed_fields) for item in disagreements),
            exact_judgment_agreement=(len(rows) - len(disagreements)) / len(rows),
        ),
        dimensions={
            dimension: _dimension_agreement(dimension, values_by_dimension[dimension])
            for dimension in GROUNDING_REVIEW_DIMENSIONS
            if values_by_dimension[dimension]
        },
        disagreements=tuple(
            sorted(disagreements, key=lambda item: (item.case_id, item.claim_id or ""))
        ),
    )


def _csv_safe(value: str) -> str:
    return f"'{value}" if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _review_columns(prefix: str, judgment: GroundedAnswerJudgment) -> dict[str, str]:
    values = _judgment_values(judgment)
    return {
        f"{prefix}_support_0_to_3": values.get("support_grade", ""),
        f"{prefix}_citation_quality_0_to_2": values.get("citation_quality", ""),
        f"{prefix}_answer_relevance_0_to_2": values.get("answer_relevance", ""),
        f"{prefix}_abstention_appropriate": values.get("abstention_appropriate", ""),
        f"{prefix}_rationale": _csv_safe(judgment.rationale),
    }


def _adjudication_review_fields(
    report: IndependentGroundedAnswerReviewAgreementReport,
    disagreement: GroundedAnswerReviewDisagreement,
) -> dict[str, str]:
    swap = (
        int(
            sha256(
                f"{report.report_id}:{disagreement.case_id}:{disagreement.claim_id or ''}".encode()
            ).hexdigest(),
            16,
        )
        % 2
    )
    review_a, review_b = (
        (disagreement.second_judgment, disagreement.first_judgment)
        if swap
        else (disagreement.first_judgment, disagreement.second_judgment)
    )
    return {
        "agreement_report_id": report.report_id,
        "disputed_fields": ";".join(disagreement.disputed_fields),
        **_review_columns("review_a", review_a),
        **_review_columns("review_b", review_b),
    }


def _validate_artifact_scope(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
    review: ReviewedGroundedAnswerBatch,
) -> None:
    if (
        review.task_id != task.task_id
        or review.task_sha256 != task.task_sha256
        or review.batch_id != batch.batch_id
        or review.batch_sha256 != batch.batch_sha256
    ):
        raise ValueError("grounded-answer review does not match the exact task and batch")


def _protected_metadata(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
) -> dict[tuple[str, str], dict[str, str]]:
    return {
        (row["case_id"], row["claim_id"]): {
            column: row[column] for column in GROUNDING_REVIEW_COLUMNS[:14]
        }
        for row in grounded_answer_review_rows(task, batch)
    }


def build_grounded_answer_adjudication_sheet(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
    first: ReviewedGroundedAnswerBatch,
    second: ReviewedGroundedAnswerBatch,
) -> GroundedAnswerAdjudicationSheet:
    """Export disputed rows only, without candidate, model, or reviewer identity."""
    _validate_artifact_scope(task, batch, first)
    _validate_artifact_scope(task, batch, second)
    first, second = _canonical_reviews(first, second)
    report = compare_grounded_answer_reviews(first, second)
    metadata = _protected_metadata(task, batch)
    blind_order = sorted(
        report.disagreements,
        key=lambda item: sha256(
            f"{report.report_id}:{item.case_id}:{item.claim_id or ''}".encode()
        ).hexdigest(),
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=GROUNDING_ADJUDICATION_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for disagreement in blind_order:
        writer.writerow(
            {
                **metadata[(disagreement.case_id, disagreement.claim_id or "")],
                **_adjudication_review_fields(report, disagreement),
                **{column: "" for column in _FINAL_COLUMNS.values()},
                "adjudication_rationale": "",
            }
        )
    return GroundedAnswerAdjudicationSheet(
        content=output.getvalue(),
        agreement_report=report,
        pending_judgment_count=len(report.disagreements),
        pending_field_count=report.judgments.disputed_field_count,
    )


def _parse_final_value(
    dimension: GroundedAnswerReviewDimension,
    raw: str,
    *,
    line_number: int,
) -> JudgmentValue:
    value = raw.strip().casefold()
    if value not in _DIMENSION_SCALES[dimension]:
        allowed = ", ".join(_DIMENSION_SCALES[dimension])
        raise ValueError(
            f"adjudication row {line_number} field {dimension} requires one of {allowed}"
        )
    if dimension == "abstention_appropriate":
        return value == "yes"
    return int(value)


def _final_judgment(
    first: GroundedAnswerJudgment,
    overrides: dict[GroundedAnswerReviewDimension, JudgmentValue],
    *,
    rationale: str,
) -> GroundedAnswerJudgment:
    support_grade = first.support_grade
    citation_quality = first.citation_quality
    answer_relevance = first.answer_relevance
    abstention_appropriate = first.abstention_appropriate
    if "support_grade" in overrides:
        support_grade = overrides["support_grade"]
    if "citation_quality" in overrides:
        citation_quality = overrides["citation_quality"]
    if "answer_relevance" in overrides:
        answer_relevance = overrides["answer_relevance"]
    if "abstention_appropriate" in overrides:
        abstention_appropriate = cast(bool, overrides["abstention_appropriate"])
    return GroundedAnswerJudgment(
        case_id=first.case_id,
        claim_id=first.claim_id,
        support_grade=support_grade,
        citation_quality=citation_quality,
        answer_relevance=answer_relevance,
        abstention_appropriate=abstention_appropriate,
        rationale=rationale,
    )


def apply_grounded_answer_adjudication(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
    first: ReviewedGroundedAnswerBatch,
    second: ReviewedGroundedAnswerBatch,
    csv_text: str,
    *,
    adjudicator: str,
    adjudicated_at: datetime | None = None,
) -> tuple[ReviewedGroundedAnswerBatch, GroundedAnswerAdjudicationReport]:
    """Resolve every disputed field and create one blocked, adjudicated review artifact."""
    _validate_artifact_scope(task, batch, first)
    _validate_artifact_scope(task, batch, second)
    first, second = _canonical_reviews(first, second)
    sheet = build_grounded_answer_adjudication_sheet(task, batch, first, second)
    agreement = sheet.agreement_report
    normalized_adjudicator = " ".join(adjudicator.split())
    if len(normalized_adjudicator) < 2:
        raise ValueError("adjudicator identity must contain at least two characters")
    if normalized_adjudicator.casefold() in {
        first.reviewer.casefold(),
        second.reviewer.casefold(),
    }:
        raise ValueError("adjudicator must be independent from both reviewers")

    reader = csv.DictReader(io.StringIO(csv_text, newline=""))
    if tuple(reader.fieldnames or ()) != GROUNDING_ADJUDICATION_COLUMNS:
        raise ValueError("grounded-answer adjudication CSV header does not match the template")
    metadata = _protected_metadata(task, batch)
    disagreements = {(item.case_id, item.claim_id or ""): item for item in agreement.disagreements}
    final_rows: dict[tuple[str, str], GroundedAnswerJudgment] = {}
    decisions: dict[tuple[str, str], GroundedAnswerAdjudicationDecision] = {}
    for line_number, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"grounded-answer adjudication row {line_number} is malformed")
        identity = (row["case_id"], row["claim_id"])
        disagreement = disagreements.get(identity)
        if disagreement is None:
            raise ValueError(f"adjudication row {line_number} is not a known disagreement")
        if identity in decisions:
            raise ValueError(f"adjudication row {line_number} duplicates a disagreement")
        protected = {
            **metadata[identity],
            **_adjudication_review_fields(agreement, disagreement),
        }
        changed = [
            column for column in _PROTECTED_ADJUDICATION_COLUMNS if row[column] != protected[column]
        ]
        if changed:
            raise ValueError(
                f"adjudication row {line_number} changed protected fields: " + ", ".join(changed)
            )
        overrides: dict[GroundedAnswerReviewDimension, JudgmentValue] = {}
        for dimension, column in _FINAL_COLUMNS.items():
            raw = row[column]
            if dimension in disagreement.disputed_fields:
                overrides[dimension] = _parse_final_value(dimension, raw, line_number=line_number)
            elif raw.strip():
                raise ValueError(
                    f"adjudication row {line_number} cannot regrade agreed field {dimension}"
                )
        rationale = " ".join(row["adjudication_rationale"].split())
        if len(rationale) < 10:
            raise ValueError(
                f"adjudication row {line_number} requires a rationale of at least ten characters"
            )
        if len(rationale) > 1_000:
            raise ValueError(f"adjudication row {line_number} rationale exceeds 1000 characters")
        final = _final_judgment(
            disagreement.first_judgment,
            overrides,
            rationale=rationale,
        )
        final_rows[identity] = final
        decisions[identity] = GroundedAnswerAdjudicationDecision(
            case_id=disagreement.case_id,
            claim_id=disagreement.claim_id,
            disputed_fields=disagreement.disputed_fields,
            first_judgment=disagreement.first_judgment,
            second_judgment=disagreement.second_judgment,
            final_judgment=final,
        )

    missing = sorted(set(disagreements) - set(decisions))
    if missing:
        raise ValueError(f"adjudication sheet is missing {len(missing)} disagreement(s)")

    rows = _review_pairs(first, second)
    final_judgments: list[GroundedAnswerJudgment] = []
    for left, right in rows:
        identity = (left.case_id, left.claim_id or "")
        if identity in final_rows:
            final_judgments.append(final_rows[identity])
        else:
            if _disputed_fields(left, right):
                raise ValueError("adjudication did not resolve a disputed judgment")
            final_judgments.append(
                _final_judgment(
                    left,
                    {},
                    rationale="Independent reviewers agreed on every applicable rubric grade.",
                )
            )

    timestamp = adjudicated_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("grounded-answer adjudication timestamp must be timezone-aware")
    ordered_decisions = tuple(decisions[key] for key in sorted(decisions))
    dimensions = agreement.dimensions
    provenance = GroundedAnswerIndependentAdjudicationProvenance(
        independent_reviewers=(first.reviewer, second.reviewer),
        independent_review_ids=(first.review_id, second.review_id),
        independent_review_sha256s=(first.review_sha256, second.review_sha256),
        agreement_report_id=agreement.report_id,
        dimension_observed_agreement={
            dimension: metrics.observed_agreement for dimension, metrics in dimensions.items()
        },
        dimension_cohen_kappa={
            dimension: metrics.cohen_kappa for dimension, metrics in dimensions.items()
        },
        adjudicator=normalized_adjudicator,
        adjudicated_at=timestamp,
        judgment_count=len(final_judgments),
        adjudication_decision_count=len(ordered_decisions),
        adjudicated_field_count=agreement.judgments.disputed_field_count,
    )
    ordered_judgments = tuple(
        sorted(final_judgments, key=lambda item: (item.case_id, item.claim_id or ""))
    )
    draft = ReviewedGroundedAnswerBatch.model_construct(
        schema_version="1.1.0",
        review_id="grounded-review-" + "0" * 20,
        review_sha256="0" * 64,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        batch_id=batch.batch_id,
        batch_sha256=batch.batch_sha256,
        rubric_version=GROUNDING_RUBRIC_VERSION,
        reviewer=normalized_adjudicator,
        reviewed_at=timestamp,
        judgment_count=len(ordered_judgments),
        judgments=ordered_judgments,
        review_status="independent_adjudication_complete",
        adjudication=provenance,
        promotion_status="blocked",
        caveats=ADJUDICATED_GROUNDED_ANSWER_REVIEW_CAVEATS,
    )
    digest = grounded_answer_review_sha256(draft)
    final_review = ReviewedGroundedAnswerBatch(
        schema_version="1.1.0",
        review_id=f"grounded-review-{digest[:20]}",
        review_sha256=digest,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        batch_id=batch.batch_id,
        batch_sha256=batch.batch_sha256,
        reviewer=normalized_adjudicator,
        reviewed_at=timestamp,
        judgment_count=len(ordered_judgments),
        judgments=ordered_judgments,
        review_status="independent_adjudication_complete",
        adjudication=provenance,
        caveats=ADJUDICATED_GROUNDED_ANSWER_REVIEW_CAVEATS,
    )
    report = GroundedAnswerAdjudicationReport(
        report_id=_adjudication_report_id(
            agreement_report_id=agreement.report_id,
            final_review_sha256=final_review.review_sha256,
        ),
        agreement_report_id=agreement.report_id,
        generated_at=timestamp,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        batch_id=batch.batch_id,
        batch_sha256=batch.batch_sha256,
        first_review=agreement.first_review,
        second_review=agreement.second_review,
        adjudicator=normalized_adjudicator,
        judgment_count=len(ordered_judgments),
        inherited_agreement_count=len(ordered_judgments) - len(ordered_decisions),
        adjudication_decision_count=len(ordered_decisions),
        adjudicated_field_count=agreement.judgments.disputed_field_count,
        final_review_id=final_review.review_id,
        final_review_sha256=final_review.review_sha256,
        decisions=ordered_decisions,
    )
    return final_review, report

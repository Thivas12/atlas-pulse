"""Independent-review agreement and system-blind adjudication workflow."""

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

from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.projections.base import SourceName
from atlas_pulse.relationship_evaluation.base import (
    RELATIONSHIP_LABELS,
    IndependentAdjudicationProvenance,
    RelationshipCase,
    RelationshipPool,
    StrictModel,
)
from atlas_pulse.relationship_evaluation.judgments import (
    JUDGMENT_COLUMNS,
    relationship_review_metadata,
)
from atlas_pulse.relationships import ClaimPredicate, RelationshipLabel

AgreementSchemaVersion = Literal["1.0.0"]

ADJUDICATION_COLUMNS = (
    *JUDGMENT_COLUMNS[:-2],
    "agreement_report_id",
    "first_reviewer",
    "first_label",
    "first_rationale",
    "second_reviewer",
    "second_label",
    "second_rationale",
    "adjudicated_label",
    "adjudication_rationale",
)

_AGREEMENT_CAVEATS = (
    "Agreement measures reviewer consistency, not whether either judgment is correct.",
    "The software verifies distinct reviewer identities and identical captured evidence, but cannot prove that reviews were completed independently.",
    "Reviewers and adjudicators remain blind to deployed system labels in every editable CSV.",
    "Cohen's kappa is undefined when marginal label distributions make expected agreement exactly one.",
    "Source-pair caps make this a reviewable benchmark rather than a live-prevalence estimate.",
)

_ADJUDICATION_CAVEATS = (
    "Cases with matching independent labels inherit that consensus; only disagreements are exposed for adjudication.",
    "The final gold labels describe the explicit relationship between two source records and do not verify that either source is true.",
    "The adjudicator sees both human labels and rationales but never the deployed system prediction.",
    "The final pool is content-addressed and retains hashes of both independent reviewed pools.",
)


class ReviewedPoolIdentity(StrictModel):
    """Content-addressed identity for one completed independent review."""

    reviewer: str = Field(min_length=1, max_length=200)
    reviewed_at: datetime
    pool_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("reviewer")
    @classmethod
    def normalize_reviewer(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("reviewer must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_timestamp(self) -> ReviewedPoolIdentity:
        if self.reviewed_at.tzinfo is None:
            raise ValueError("reviewed_at must be timezone-aware")
        return self


class ReviewAgreementMetrics(StrictModel):
    """Observed agreement and chance-corrected agreement for one non-empty slice."""

    case_count: int = Field(ge=1)
    agreement_count: int = Field(ge=0)
    disagreement_count: int = Field(ge=0)
    observed_agreement: float = Field(ge=0, le=1, allow_inf_nan=False)
    expected_agreement: float = Field(ge=0, le=1, allow_inf_nan=False)
    cohen_kappa: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_counts_and_rates(self) -> ReviewAgreementMetrics:
        if self.agreement_count + self.disagreement_count != self.case_count:
            raise ValueError("agreement and disagreement counts must sum to case_count")
        observed = self.agreement_count / self.case_count
        if abs(self.observed_agreement - observed) > 1e-12:
            raise ValueError("observed_agreement must match the agreement count")
        if self.expected_agreement == 1:
            if self.cohen_kappa is not None:
                raise ValueError("cohen_kappa must be undefined when expected agreement is one")
        else:
            expected_kappa = (self.observed_agreement - self.expected_agreement) / (
                1 - self.expected_agreement
            )
            if self.cohen_kappa is None or abs(self.cohen_kappa - expected_kappa) > 1e-12:
                raise ValueError("cohen_kappa must match observed and expected agreement")
        return self


class ReviewDisagreement(StrictModel):
    """One exact case on which two independent reviewers assigned different labels."""

    case_id: str = Field(pattern=r"^pair-[0-9a-f]{20}$")
    edge_id: str
    predicate: ClaimPredicate
    source_pair: tuple[SourceName, SourceName]
    first_label: RelationshipLabel
    first_rationale: str | None = Field(default=None, max_length=1_000)
    second_label: RelationshipLabel
    second_rationale: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_disagreement(self) -> ReviewDisagreement:
        if self.first_label == self.second_label:
            raise ValueError("a disagreement must contain different reviewer labels")
        return self


class IndependentReviewAgreementReport(StrictModel):
    """Machine-readable agreement analysis over two exact reviewed copies of one pool."""

    schema_version: AgreementSchemaVersion = "1.0.0"
    report_id: str = Field(min_length=1, max_length=240)
    pool_id: str
    capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    first_review: ReviewedPoolIdentity
    second_review: ReviewedPoolIdentity
    overall: ReviewAgreementMetrics
    predicates: dict[ClaimPredicate, ReviewAgreementMetrics]
    source_pairs: dict[str, ReviewAgreementMetrics]
    confusion_matrix: dict[RelationshipLabel, dict[RelationshipLabel, int]]
    disagreements: tuple[ReviewDisagreement, ...]
    caveats: tuple[str, ...]

    @model_validator(mode="after")
    def validate_report(self) -> IndependentReviewAgreementReport:
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        reviewers = (self.first_review.reviewer, self.second_review.reviewer)
        if reviewers[0].casefold() == reviewers[1].casefold():
            raise ValueError("agreement requires two different reviewers")
        if reviewers != tuple(sorted(reviewers, key=lambda item: (item.casefold(), item))):
            raise ValueError("agreement reviews must use canonical reviewer order")
        if set(self.confusion_matrix) != set(RELATIONSHIP_LABELS) or any(
            set(row) != set(RELATIONSHIP_LABELS) for row in self.confusion_matrix.values()
        ):
            raise ValueError("agreement confusion matrix must contain every label")
        if any(count < 0 for row in self.confusion_matrix.values() for count in row.values()):
            raise ValueError("agreement confusion matrix counts must be non-negative")
        matrix_count = sum(sum(row.values()) for row in self.confusion_matrix.values())
        matrix_agreements = sum(
            self.confusion_matrix[label][label] for label in RELATIONSHIP_LABELS
        )
        if matrix_count != self.overall.case_count:
            raise ValueError("agreement confusion matrix must cover every case")
        if matrix_agreements != self.overall.agreement_count:
            raise ValueError("agreement confusion matrix diagonal must match agreement_count")
        row_counts = {
            label: sum(self.confusion_matrix[label].values()) for label in RELATIONSHIP_LABELS
        }
        column_counts = {
            label: sum(self.confusion_matrix[row][label] for row in RELATIONSHIP_LABELS)
            for label in RELATIONSHIP_LABELS
        }
        expected_agreement = sum(
            (row_counts[label] / matrix_count) * (column_counts[label] / matrix_count)
            for label in RELATIONSHIP_LABELS
        )
        if abs(expected_agreement - self.overall.expected_agreement) > 1e-12:
            raise ValueError("expected agreement must match confusion-matrix marginals")
        disagreement_ids = tuple(item.case_id for item in self.disagreements)
        if len(set(disagreement_ids)) != len(disagreement_ids):
            raise ValueError("agreement disagreement case IDs must be unique")
        if len(disagreement_ids) != self.overall.disagreement_count:
            raise ValueError("agreement report must retain every disagreement")
        disagreement_counts = Counter(
            (item.first_label, item.second_label) for item in self.disagreements
        )
        for first_label in RELATIONSHIP_LABELS:
            for second_label in RELATIONSHIP_LABELS:
                if (
                    first_label != second_label
                    and self.confusion_matrix[first_label][second_label]
                    != disagreement_counts[(first_label, second_label)]
                ):
                    raise ValueError("disagreements must match confusion-matrix off-diagonal cells")
        if sum(item.case_count for item in self.predicates.values()) != self.overall.case_count:
            raise ValueError("predicate agreement slices must partition the reviewed cases")
        if sum(item.case_count for item in self.source_pairs.values()) != self.overall.case_count:
            raise ValueError("source-pair agreement slices must partition the reviewed cases")
        return self


class AdjudicationDecision(StrictModel):
    """One explicit final decision for a prior independent-review disagreement."""

    case_id: str = Field(pattern=r"^pair-[0-9a-f]{20}$")
    edge_id: str
    predicate: ClaimPredicate
    source_pair: tuple[SourceName, SourceName]
    first_label: RelationshipLabel
    second_label: RelationshipLabel
    adjudicated_label: RelationshipLabel
    rationale: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_prior_disagreement(self) -> AdjudicationDecision:
        if self.first_label == self.second_label:
            raise ValueError("adjudication decisions must originate from a disagreement")
        return self


class RelationshipAdjudicationReport(StrictModel):
    """Final review provenance tied to one content-addressed gold pool."""

    schema_version: AgreementSchemaVersion = "1.0.0"
    report_id: str = Field(min_length=1, max_length=240)
    agreement_report_id: str = Field(min_length=1, max_length=240)
    pool_id: str
    capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    first_review: ReviewedPoolIdentity
    second_review: ReviewedPoolIdentity
    adjudicator: str = Field(min_length=1, max_length=200)
    case_count: int = Field(ge=1)
    inherited_agreement_count: int = Field(ge=0)
    adjudication_decision_count: int = Field(ge=0)
    final_pool_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decisions: tuple[AdjudicationDecision, ...]
    caveats: tuple[str, ...]

    @field_validator("adjudicator")
    @classmethod
    def normalize_adjudicator(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("adjudicator must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_report(self) -> RelationshipAdjudicationReport:
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        reviewers = {
            self.first_review.reviewer.casefold(),
            self.second_review.reviewer.casefold(),
        }
        if self.adjudicator.casefold() in reviewers:
            raise ValueError("adjudicator must be independent from both reviewers")
        ordered_reviewers = (self.first_review.reviewer, self.second_review.reviewer)
        if ordered_reviewers != tuple(
            sorted(ordered_reviewers, key=lambda item: (item.casefold(), item))
        ):
            raise ValueError("adjudication reviews must use canonical reviewer order")
        if self.inherited_agreement_count + self.adjudication_decision_count != self.case_count:
            raise ValueError("consensus and adjudication counts must cover every case")
        if len(self.decisions) != self.adjudication_decision_count:
            raise ValueError("adjudication report must retain every decision")
        decision_ids = [item.case_id for item in self.decisions]
        if len(set(decision_ids)) != len(decision_ids):
            raise ValueError("adjudication decision case IDs must be unique")
        return self


@dataclass(frozen=True, slots=True)
class RelationshipAdjudicationSheet:
    """System-blind CSV containing only independent-review disagreements."""

    content: str
    agreement_report: IndependentReviewAgreementReport
    pending_count: int


def relationship_capture_sha256(pool: RelationshipPool) -> str:
    """Hash every captured field while excluding human judgments and review metadata."""
    data = pool.model_dump(mode="json")
    data.pop("schema_version")
    data.pop("judgment_status")
    data.pop("reviewer")
    data.pop("reviewed_at")
    data.pop("adjudication", None)
    for case in data["cases"]:
        case.pop("gold_label")
        case.pop("rationale")
    return canonical_sha256(data)


def _require_single_review(pool: RelationshipPool, *, label: str) -> None:
    if pool.judgment_status != "reviewed" or pool.reviewer is None or pool.reviewed_at is None:
        raise ValueError(f"{label} pool must be fully reviewed")
    if pool.adjudication is not None:
        raise ValueError(f"{label} pool must be an independent first-pass review")


def _canonical_reviews(
    first: RelationshipPool,
    second: RelationshipPool,
) -> tuple[RelationshipPool, RelationshipPool, str]:
    _require_single_review(first, label="first")
    _require_single_review(second, label="second")
    assert first.reviewer is not None and second.reviewer is not None
    if first.reviewer.casefold() == second.reviewer.casefold():
        raise ValueError("independent review requires two different reviewer identities")
    first_capture = relationship_capture_sha256(first)
    second_capture = relationship_capture_sha256(second)
    if first_capture != second_capture:
        raise ValueError("independent reviews must describe the exact same captured pool")
    ordered = sorted(
        (first, second),
        key=lambda pool: (cast(str, pool.reviewer).casefold(), cast(str, pool.reviewer)),
    )
    return ordered[0], ordered[1], first_capture


def _review_rows(
    first: RelationshipPool,
    second: RelationshipPool,
) -> tuple[tuple[RelationshipCase, RelationshipCase], ...]:
    second_cases = {case.case_id: case for case in second.cases}
    if set(second_cases) != {case.case_id for case in first.cases}:
        raise ValueError("independent reviews must contain identical case IDs")
    rows = tuple((case, second_cases[case.case_id]) for case in first.cases)
    if any(left.review_identity() != right.review_identity() for left, right in rows):
        raise ValueError("reviewer-visible evidence changed between independent reviews")
    if any(left.gold_label is None or right.gold_label is None for left, right in rows):
        raise ValueError("independent reviews must label every case")
    return rows


def _agreement_metrics(
    labels: Sequence[tuple[RelationshipLabel, RelationshipLabel]],
) -> ReviewAgreementMetrics:
    if not labels:
        raise ValueError("cannot measure agreement over an empty slice")
    first_counts: Counter[RelationshipLabel] = Counter(first for first, _ in labels)
    second_counts: Counter[RelationshipLabel] = Counter(second for _, second in labels)
    case_count = len(labels)
    agreement_count = sum(first == second for first, second in labels)
    observed = agreement_count / case_count
    expected = sum(
        (first_counts[label] / case_count) * (second_counts[label] / case_count)
        for label in RELATIONSHIP_LABELS
    )
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    return ReviewAgreementMetrics(
        case_count=case_count,
        agreement_count=agreement_count,
        disagreement_count=case_count - agreement_count,
        observed_agreement=observed,
        expected_agreement=expected,
        cohen_kappa=kappa,
    )


def _agreement_confusion(
    labels: Sequence[tuple[RelationshipLabel, RelationshipLabel]],
) -> dict[RelationshipLabel, dict[RelationshipLabel, int]]:
    return {
        first_label: {
            second_label: sum(
                first == first_label and second == second_label for first, second in labels
            )
            for second_label in RELATIONSHIP_LABELS
        }
        for first_label in RELATIONSHIP_LABELS
    }


def compare_independent_reviews(
    first: RelationshipPool,
    second: RelationshipPool,
    *,
    generated_at: datetime | None = None,
) -> IndependentReviewAgreementReport:
    """Validate two independent reviews and measure overall and sliced agreement."""
    first, second, capture_hash = _canonical_reviews(first, second)
    assert first.reviewer is not None and first.reviewed_at is not None
    assert second.reviewer is not None and second.reviewed_at is not None
    rows = _review_rows(first, second)
    labels = tuple(
        (cast(RelationshipLabel, left.gold_label), cast(RelationshipLabel, right.gold_label))
        for left, right in rows
    )
    by_predicate: dict[ClaimPredicate, list[tuple[RelationshipLabel, RelationshipLabel]]] = (
        defaultdict(list)
    )
    by_source_pair: dict[str, list[tuple[RelationshipLabel, RelationshipLabel]]] = defaultdict(list)
    disagreements: list[ReviewDisagreement] = []
    for left, right in rows:
        label_pair = (
            cast(RelationshipLabel, left.gold_label),
            cast(RelationshipLabel, right.gold_label),
        )
        by_predicate[left.predicate].append(label_pair)
        by_source_pair["+".join(left.source_pair)].append(label_pair)
        if label_pair[0] != label_pair[1]:
            disagreements.append(
                ReviewDisagreement(
                    case_id=left.case_id,
                    edge_id=left.edge_id,
                    predicate=left.predicate,
                    source_pair=left.source_pair,
                    first_label=label_pair[0],
                    first_rationale=left.rationale,
                    second_label=label_pair[1],
                    second_rationale=right.rationale,
                )
            )

    first_hash = canonical_sha256(first)
    second_hash = canonical_sha256(second)
    digest = canonical_sha256(
        {
            "contract": "independent-relationship-review-v1",
            "capture_sha256": capture_hash,
            "review_pool_sha256s": (first_hash, second_hash),
        }
    )
    return IndependentReviewAgreementReport(
        report_id=f"{first.pool_id}-agreement-{digest[:12]}",
        pool_id=first.pool_id,
        capture_sha256=capture_hash,
        generated_at=generated_at or datetime.now(UTC),
        first_review=ReviewedPoolIdentity(
            reviewer=first.reviewer,
            reviewed_at=first.reviewed_at,
            pool_sha256=first_hash,
        ),
        second_review=ReviewedPoolIdentity(
            reviewer=second.reviewer,
            reviewed_at=second.reviewed_at,
            pool_sha256=second_hash,
        ),
        overall=_agreement_metrics(labels),
        predicates={
            predicate: _agreement_metrics(values)
            for predicate, values in sorted(by_predicate.items())
        },
        source_pairs={
            source_pair: _agreement_metrics(values)
            for source_pair, values in sorted(by_source_pair.items())
        },
        confusion_matrix=_agreement_confusion(labels),
        disagreements=tuple(sorted(disagreements, key=lambda item: item.case_id)),
        caveats=_AGREEMENT_CAVEATS,
    )


def _csv_safe(value: str) -> str:
    stripped = value.lstrip()
    return f"'{value}" if stripped.startswith(("=", "+", "-", "@")) else value


def _review_fields(
    report: IndependentReviewAgreementReport,
    disagreement: ReviewDisagreement,
) -> dict[str, str]:
    return {
        "agreement_report_id": report.report_id,
        "first_reviewer": _csv_safe(report.first_review.reviewer),
        "first_label": disagreement.first_label,
        "first_rationale": _csv_safe(disagreement.first_rationale or ""),
        "second_reviewer": _csv_safe(report.second_review.reviewer),
        "second_label": disagreement.second_label,
        "second_rationale": _csv_safe(disagreement.second_rationale or ""),
    }


def build_relationship_adjudication_sheet(
    first: RelationshipPool,
    second: RelationshipPool,
) -> RelationshipAdjudicationSheet:
    """Export only disagreements while keeping every deployed prediction hidden."""
    first, second, _ = _canonical_reviews(first, second)
    report = compare_independent_reviews(first, second)
    first_cases = {case.case_id: case for case in first.cases}
    blind_order = sorted(
        report.disagreements,
        key=lambda item: sha256(f"{report.report_id}:{item.case_id}".encode()).hexdigest(),
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=ADJUDICATION_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for disagreement in blind_order:
        writer.writerow(
            {
                **relationship_review_metadata(first_cases[disagreement.case_id]),
                **_review_fields(report, disagreement),
                "adjudicated_label": "",
                "adjudication_rationale": "",
            }
        )
    return RelationshipAdjudicationSheet(
        content=output.getvalue(),
        agreement_report=report,
        pending_count=len(report.disagreements),
    )


def apply_relationship_adjudication(
    first: RelationshipPool,
    second: RelationshipPool,
    csv_text: str,
    *,
    adjudicator: str,
    adjudicated_at: datetime | None = None,
) -> tuple[RelationshipPool, RelationshipAdjudicationReport]:
    """Import protected disagreement decisions and create one final gold pool."""
    first, second, _ = _canonical_reviews(first, second)
    sheet = build_relationship_adjudication_sheet(first, second)
    agreement = sheet.agreement_report
    normalized_adjudicator = " ".join(adjudicator.split())
    if not normalized_adjudicator:
        raise ValueError("adjudicator must not be empty")
    if normalized_adjudicator.casefold() in {
        agreement.first_review.reviewer.casefold(),
        agreement.second_review.reviewer.casefold(),
    }:
        raise ValueError("adjudicator must be independent from both reviewers")

    reader = csv.DictReader(io.StringIO(csv_text, newline=""))
    if tuple(reader.fieldnames or ()) != ADJUDICATION_COLUMNS:
        raise ValueError(f"adjudication columns must exactly equal {ADJUDICATION_COLUMNS}")

    first_cases = {case.case_id: case for case in first.cases}
    disagreements = {item.case_id: item for item in agreement.disagreements}
    decisions: dict[str, tuple[RelationshipLabel, str]] = {}
    allowed = set(RELATIONSHIP_LABELS)
    for line_number, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(f"adjudication row {line_number} has unexpected columns")
        case_id = row["case_id"]
        disagreement = disagreements.get(case_id)
        if disagreement is None:
            raise ValueError(f"adjudication row {line_number} is not a known disagreement")
        if case_id in decisions:
            raise ValueError(f"adjudication row {line_number} duplicates {case_id}")
        protected = {
            **relationship_review_metadata(first_cases[case_id]),
            **_review_fields(agreement, disagreement),
        }
        changed = [field for field, value in protected.items() if row[field] != value]
        if changed:
            raise ValueError(
                f"adjudication row {line_number} changed protected fields: " + ", ".join(changed)
            )
        label_text = row["adjudicated_label"].strip()
        if label_text not in allowed:
            raise ValueError(f"adjudication row {line_number} requires one of {sorted(allowed)}")
        rationale = " ".join(row["adjudication_rationale"].split())
        if not rationale:
            raise ValueError(f"adjudication row {line_number} requires a rationale")
        if len(rationale) > 1_000:
            raise ValueError(f"adjudication row {line_number} rationale exceeds 1000 characters")
        decisions[case_id] = (cast(RelationshipLabel, label_text), rationale)

    missing = sorted(set(disagreements) - set(decisions))
    if missing:
        raise ValueError(f"adjudication sheet is missing {len(missing)} disagreement(s)")

    second_cases = {case.case_id: case for case in second.cases}
    decision_records: list[AdjudicationDecision] = []
    data = first.model_dump(mode="python")
    for case in data["cases"]:
        first_case = first_cases[case["case_id"]]
        second_case = second_cases[case["case_id"]]
        if first_case.gold_label == second_case.gold_label:
            case["gold_label"] = first_case.gold_label
            case["rationale"] = "Independent reviewers agreed."
            continue
        final_label, rationale = decisions[case["case_id"]]
        case["gold_label"] = final_label
        case["rationale"] = rationale
        decision_records.append(
            AdjudicationDecision(
                case_id=first_case.case_id,
                edge_id=first_case.edge_id,
                predicate=first_case.predicate,
                source_pair=first_case.source_pair,
                first_label=cast(RelationshipLabel, first_case.gold_label),
                second_label=cast(RelationshipLabel, second_case.gold_label),
                adjudicated_label=final_label,
                rationale=rationale,
            )
        )

    finalized_at = adjudicated_at or datetime.now(UTC)
    provenance = IndependentAdjudicationProvenance(
        independent_reviewers=(
            agreement.first_review.reviewer,
            agreement.second_review.reviewer,
        ),
        review_pool_sha256s=(
            agreement.first_review.pool_sha256,
            agreement.second_review.pool_sha256,
        ),
        agreement_report_id=agreement.report_id,
        observed_agreement=agreement.overall.observed_agreement,
        cohen_kappa=agreement.overall.cohen_kappa,
        adjudicator=normalized_adjudicator,
        adjudicated_at=finalized_at,
        adjudication_decision_count=len(decision_records),
    )
    data.update(
        schema_version="1.1.0",
        reviewer=normalized_adjudicator,
        reviewed_at=finalized_at,
        adjudication=provenance.model_dump(mode="python"),
    )
    final_pool = RelationshipPool.model_validate(data)
    final_hash = canonical_sha256(final_pool)
    record = RelationshipAdjudicationReport(
        report_id=f"{final_pool.pool_id}-adjudication-{final_hash[:12]}",
        agreement_report_id=agreement.report_id,
        pool_id=final_pool.pool_id,
        capture_sha256=agreement.capture_sha256,
        generated_at=finalized_at,
        first_review=agreement.first_review,
        second_review=agreement.second_review,
        adjudicator=normalized_adjudicator,
        case_count=len(final_pool.cases),
        inherited_agreement_count=agreement.overall.agreement_count,
        adjudication_decision_count=len(decision_records),
        final_pool_sha256=final_hash,
        decisions=tuple(sorted(decision_records, key=lambda item: item.case_id)),
        caveats=_ADJUDICATION_CAVEATS,
    )
    return final_pool, record

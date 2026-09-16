"""Independent retrieval-review agreement and rank-blind adjudication."""

from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, cast

from pydantic import Field, field_validator, model_validator

from atlas_pulse.evaluation.base import (
    CandidatePool,
    EvaluationQuery,
    IndependentRetrievalAdjudicationProvenance,
    PooledCandidate,
    RelevanceGrade,
    StrictModel,
)
from atlas_pulse.evaluation.judgments import (
    JUDGMENT_COLUMNS,
    judgment_metadata,
    spreadsheet_safe_text,
)
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.projections.base import SourceName

AgreementSchemaVersion = Literal["1.0.0"]

RELEVANCE_GRADES = (0, 1, 2, 3)

ADJUDICATION_COLUMNS = (
    *JUDGMENT_COLUMNS[:-2],
    "agreement_report_id",
    "review_a_relevance_0_to_3",
    "review_a_rationale",
    "review_b_relevance_0_to_3",
    "review_b_rationale",
    "adjudicated_relevance_0_to_3",
    "adjudication_rationale",
)

_AGREEMENT_CAVEATS = (
    "Agreement measures reviewer consistency, not whether either judgment or source is correct.",
    "The software verifies distinct reviewer identities and the exact same captured pool, but cannot prove the reviews were completed independently.",
    "Retrieval modes, ranks, scores, and reviewer identities remain hidden from every editable adjudication row.",
    "Review A/B order is deterministically swapped per disagreement to reduce positional anchoring.",
    "Cohen's kappa is unweighted and undefined when marginal grade distributions make expected agreement exactly one.",
    "This live depth-limited pool is not a complete or representative census of possible retrieval needs.",
)

_ADJUDICATION_CAVEATS = (
    "Matching independent grades inherit consensus; a separate adjudicator resolves only disagreements.",
    "The adjudicator sees bounded source evidence and blinded review values, never retrieval modes, ranks, scores, or reviewer identities.",
    "Final relevance grades do not verify source truth, corpus completeness, freshness, or representativeness.",
    "The finalized artifact remains blocked from production promotion and does not create a regression threshold.",
)


class ReviewedRetrievalPoolIdentity(StrictModel):
    """Content-addressed identity for one complete independent review."""

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
    def validate_timestamp(self) -> ReviewedRetrievalPoolIdentity:
        if self.reviewed_at.tzinfo is None:
            raise ValueError("reviewed_at must be timezone-aware")
        return self


class RetrievalReviewAgreementMetrics(StrictModel):
    """Observed and chance-corrected exact agreement for one non-empty group."""

    judgment_count: int = Field(ge=1)
    agreement_count: int = Field(ge=0)
    disagreement_count: int = Field(ge=0)
    observed_agreement: float = Field(ge=0, le=1, allow_inf_nan=False)
    expected_agreement: float = Field(ge=0, le=1, allow_inf_nan=False)
    cohen_kappa: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_counts_and_rates(self) -> RetrievalReviewAgreementMetrics:
        if self.agreement_count + self.disagreement_count != self.judgment_count:
            raise ValueError("agreement and disagreement counts must cover every judgment")
        observed = self.agreement_count / self.judgment_count
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


class RetrievalReviewDisagreement(StrictModel):
    """One exact query-document judgment on which two reviewers differed."""

    query_id: str
    document_id: str
    source: SourceName
    first_relevance: RelevanceGrade
    first_rationale: str | None = Field(default=None, max_length=1_000)
    second_relevance: RelevanceGrade
    second_rationale: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_disagreement(self) -> RetrievalReviewDisagreement:
        if self.first_relevance == self.second_relevance:
            raise ValueError("a disagreement must contain different relevance grades")
        return self


class IndependentRetrievalReviewAgreementReport(StrictModel):
    """Agreement analysis over two exact independently reviewed candidate pools."""

    schema_version: AgreementSchemaVersion = "1.0.0"
    report_id: str = Field(min_length=1, max_length=240)
    pool_id: str
    capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    first_review: ReviewedRetrievalPoolIdentity
    second_review: ReviewedRetrievalPoolIdentity
    overall: RetrievalReviewAgreementMetrics
    queries: dict[str, RetrievalReviewAgreementMetrics]
    slices: dict[str, RetrievalReviewAgreementMetrics]
    sources: dict[SourceName, RetrievalReviewAgreementMetrics]
    confusion_matrix: dict[int, dict[int, int]]
    disagreements: tuple[RetrievalReviewDisagreement, ...]
    promotion_status: Literal["blocked"] = "blocked"
    caveats: tuple[str, ...] = _AGREEMENT_CAVEATS

    @model_validator(mode="after")
    def validate_report(self) -> IndependentRetrievalReviewAgreementReport:
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        reviewers = (self.first_review.reviewer, self.second_review.reviewer)
        if reviewers[0].casefold() == reviewers[1].casefold():
            raise ValueError("agreement requires two different reviewers")
        if reviewers != tuple(sorted(reviewers, key=lambda item: (item.casefold(), item))):
            raise ValueError("agreement reviews must use canonical reviewer order")
        grade_set = set(RELEVANCE_GRADES)
        if set(self.confusion_matrix) != grade_set or any(
            set(row) != grade_set for row in self.confusion_matrix.values()
        ):
            raise ValueError("agreement confusion matrix must contain every relevance grade")
        if any(count < 0 for row in self.confusion_matrix.values() for count in row.values()):
            raise ValueError("agreement confusion matrix counts must be non-negative")
        matrix_count = sum(sum(row.values()) for row in self.confusion_matrix.values())
        matrix_agreements = sum(self.confusion_matrix[grade][grade] for grade in RELEVANCE_GRADES)
        if matrix_count != self.overall.judgment_count:
            raise ValueError("agreement confusion matrix must cover every judgment")
        if matrix_agreements != self.overall.agreement_count:
            raise ValueError("agreement confusion-matrix diagonal must match agreement_count")
        first_counts = {
            grade: sum(self.confusion_matrix[grade].values()) for grade in RELEVANCE_GRADES
        }
        second_counts = {
            grade: sum(self.confusion_matrix[first][grade] for first in RELEVANCE_GRADES)
            for grade in RELEVANCE_GRADES
        }
        expected = sum(
            (first_counts[grade] / matrix_count) * (second_counts[grade] / matrix_count)
            for grade in RELEVANCE_GRADES
        )
        if abs(expected - self.overall.expected_agreement) > 1e-12:
            raise ValueError("expected agreement must match confusion-matrix marginals")
        disagreement_keys = tuple((item.query_id, item.document_id) for item in self.disagreements)
        if disagreement_keys != tuple(sorted(disagreement_keys)) or len(
            set(disagreement_keys)
        ) != len(disagreement_keys):
            raise ValueError("agreement disagreements must be unique and canonically ordered")
        if len(self.disagreements) != self.overall.disagreement_count:
            raise ValueError("agreement report must retain every disagreement")
        disagreement_counts = Counter(
            (item.first_relevance, item.second_relevance) for item in self.disagreements
        )
        for first_grade in RELEVANCE_GRADES:
            for second_grade in RELEVANCE_GRADES:
                if first_grade != second_grade and (
                    self.confusion_matrix[first_grade][second_grade]
                    != disagreement_counts[(first_grade, second_grade)]
                ):
                    raise ValueError("disagreements must match confusion-matrix off-diagonal cells")
        if sum(item.judgment_count for item in self.queries.values()) != (
            self.overall.judgment_count
        ):
            raise ValueError("query agreement groups must partition the reviewed judgments")
        if sum(item.judgment_count for item in self.sources.values()) != (
            self.overall.judgment_count
        ):
            raise ValueError("source agreement groups must partition the reviewed judgments")
        if any(item.judgment_count > self.overall.judgment_count for item in self.slices.values()):
            raise ValueError("slice agreement count cannot exceed the overall judgment count")
        if self.promotion_status != "blocked" or self.caveats != _AGREEMENT_CAVEATS:
            raise ValueError("agreement report must retain its promotion boundary")
        return self


class RetrievalAdjudicationDecision(StrictModel):
    """One explicit final relevance decision for a reviewer disagreement."""

    query_id: str
    document_id: str
    source: SourceName
    first_relevance: RelevanceGrade
    second_relevance: RelevanceGrade
    adjudicated_relevance: RelevanceGrade
    rationale: str = Field(min_length=10, max_length=1_000)

    @model_validator(mode="after")
    def validate_prior_disagreement(self) -> RetrievalAdjudicationDecision:
        if self.first_relevance == self.second_relevance:
            raise ValueError("adjudication decisions must originate from a disagreement")
        return self


class RetrievalAdjudicationReport(StrictModel):
    """Final review provenance tied to one content-addressed retrieval gold pool."""

    schema_version: AgreementSchemaVersion = "1.0.0"
    report_id: str = Field(min_length=1, max_length=240)
    agreement_report_id: str = Field(min_length=1, max_length=240)
    pool_id: str
    capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    first_review: ReviewedRetrievalPoolIdentity
    second_review: ReviewedRetrievalPoolIdentity
    adjudicator: str = Field(min_length=1, max_length=200)
    judgment_count: int = Field(ge=1)
    inherited_agreement_count: int = Field(ge=0)
    adjudication_decision_count: int = Field(ge=0)
    final_pool_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decisions: tuple[RetrievalAdjudicationDecision, ...]
    promotion_status: Literal["blocked"] = "blocked"
    caveats: tuple[str, ...] = _ADJUDICATION_CAVEATS

    @field_validator("adjudicator")
    @classmethod
    def normalize_adjudicator(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("adjudicator must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_report(self) -> RetrievalAdjudicationReport:
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
        if self.inherited_agreement_count + self.adjudication_decision_count != (
            self.judgment_count
        ):
            raise ValueError("consensus and adjudication counts must cover every judgment")
        if len(self.decisions) != self.adjudication_decision_count:
            raise ValueError("adjudication report must retain every decision")
        decision_keys = [(item.query_id, item.document_id) for item in self.decisions]
        if decision_keys != sorted(decision_keys) or len(set(decision_keys)) != len(decision_keys):
            raise ValueError("adjudication decisions must be unique and canonically ordered")
        if self.promotion_status != "blocked" or self.caveats != _ADJUDICATION_CAVEATS:
            raise ValueError("adjudication report must retain its promotion boundary")
        return self


@dataclass(frozen=True, slots=True)
class RetrievalAdjudicationSheet:
    """Reviewer-blind CSV containing only relevance-grade disagreements."""

    content: str
    agreement_report: IndependentRetrievalReviewAgreementReport
    pending_count: int


JudgmentKey = tuple[str, str]
ParsedDecision = tuple[int | None, str | None]


def retrieval_capture_sha256(pool: CandidatePool) -> str:
    """Hash all captured fields while excluding review judgments and envelope version."""
    data = pool.model_dump(mode="json")
    data.pop("schema_version")
    data.pop("judgment_status")
    data.pop("reviewer")
    data.pop("reviewed_at")
    data.pop("adjudication", None)
    for pooled_query in data["queries"]:
        for candidate in pooled_query["candidates"]:
            candidate.pop("relevance")
            candidate.pop("rationale")
    return canonical_sha256(data)


def _require_first_pass(pool: CandidatePool, *, label: str) -> None:
    if pool.judgment_status != "reviewed" or pool.reviewer is None or pool.reviewed_at is None:
        raise ValueError(f"{label} pool must be fully reviewed")
    if pool.adjudication is not None:
        raise ValueError(f"{label} pool must be an independent first-pass review")


def _canonical_reviews(
    first: CandidatePool,
    second: CandidatePool,
) -> tuple[CandidatePool, CandidatePool, str]:
    _require_first_pass(first, label="first")
    _require_first_pass(second, label="second")
    assert first.reviewer is not None and second.reviewer is not None
    if first.reviewer.casefold() == second.reviewer.casefold():
        raise ValueError("independent review requires two different reviewer identities")
    first_capture = retrieval_capture_sha256(first)
    second_capture = retrieval_capture_sha256(second)
    if first_capture != second_capture:
        raise ValueError("independent reviews must describe the exact same captured pool")
    ordered = sorted(
        (first, second),
        key=lambda pool: (cast(str, pool.reviewer).casefold(), cast(str, pool.reviewer)),
    )
    return ordered[0], ordered[1], first_capture


def _candidate_rows(
    first: CandidatePool,
    second: CandidatePool,
) -> tuple[tuple[EvaluationQuery, PooledCandidate, PooledCandidate], ...]:
    second_queries = {item.query.query_id: item for item in second.queries}
    if set(second_queries) != {item.query.query_id for item in first.queries}:
        raise ValueError("independent reviews must contain identical query IDs")
    rows: list[tuple[EvaluationQuery, PooledCandidate, PooledCandidate]] = []
    for first_query in first.queries:
        second_query = second_queries[first_query.query.query_id]
        if first_query.query != second_query.query:
            raise ValueError("reviewer-visible query definitions changed between reviews")
        second_candidates = {
            candidate.document_id: candidate for candidate in second_query.candidates
        }
        if set(second_candidates) != {
            candidate.document_id for candidate in first_query.candidates
        }:
            raise ValueError("independent reviews must contain identical candidate IDs")
        for first_candidate in first_query.candidates:
            second_candidate = second_candidates[first_candidate.document_id]
            if first_candidate.evidence_identity() != second_candidate.evidence_identity():
                raise ValueError("reviewer-visible evidence changed between independent reviews")
            if first_candidate.relevance is None or second_candidate.relevance is None:
                raise ValueError("independent reviews must grade every candidate")
            rows.append((first_query.query, first_candidate, second_candidate))
    if not rows:
        raise ValueError("cannot compare independent reviews without candidate judgments")
    return tuple(rows)


def _agreement_metrics(
    grades: Sequence[tuple[int, int]],
) -> RetrievalReviewAgreementMetrics:
    if not grades:
        raise ValueError("cannot measure agreement over an empty group")
    first_counts = Counter(first for first, _ in grades)
    second_counts = Counter(second for _, second in grades)
    judgment_count = len(grades)
    agreement_count = sum(first == second for first, second in grades)
    observed = agreement_count / judgment_count
    expected = sum(
        (first_counts[grade] / judgment_count) * (second_counts[grade] / judgment_count)
        for grade in RELEVANCE_GRADES
    )
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    return RetrievalReviewAgreementMetrics(
        judgment_count=judgment_count,
        agreement_count=agreement_count,
        disagreement_count=judgment_count - agreement_count,
        observed_agreement=observed,
        expected_agreement=expected,
        cohen_kappa=kappa,
    )


def _agreement_confusion(grades: Sequence[tuple[int, int]]) -> dict[int, dict[int, int]]:
    return {
        first_grade: {
            second_grade: sum(
                first == first_grade and second == second_grade for first, second in grades
            )
            for second_grade in RELEVANCE_GRADES
        }
        for first_grade in RELEVANCE_GRADES
    }


def compare_independent_retrieval_reviews(
    first: CandidatePool,
    second: CandidatePool,
    *,
    generated_at: datetime | None = None,
) -> IndependentRetrievalReviewAgreementReport:
    """Validate two first-pass reviews and measure overall and sliced agreement."""
    first, second, capture_hash = _canonical_reviews(first, second)
    assert first.reviewer is not None and first.reviewed_at is not None
    assert second.reviewer is not None and second.reviewed_at is not None
    rows = _candidate_rows(first, second)
    grades = tuple(
        (cast(int, first_candidate.relevance), cast(int, second_candidate.relevance))
        for _, first_candidate, second_candidate in rows
    )
    by_query: dict[str, list[tuple[int, int]]] = defaultdict(list)
    by_slice: dict[str, list[tuple[int, int]]] = defaultdict(list)
    by_source: dict[SourceName, list[tuple[int, int]]] = defaultdict(list)
    disagreements: list[RetrievalReviewDisagreement] = []
    for query, first_candidate, second_candidate in rows:
        grade_pair = (
            cast(int, first_candidate.relevance),
            cast(int, second_candidate.relevance),
        )
        by_query[query.query_id].append(grade_pair)
        for slice_name in query.slices:
            by_slice[slice_name].append(grade_pair)
        by_source[first_candidate.source].append(grade_pair)
        if grade_pair[0] != grade_pair[1]:
            disagreements.append(
                RetrievalReviewDisagreement(
                    query_id=query.query_id,
                    document_id=first_candidate.document_id,
                    source=first_candidate.source,
                    first_relevance=grade_pair[0],
                    first_rationale=first_candidate.rationale,
                    second_relevance=grade_pair[1],
                    second_rationale=second_candidate.rationale,
                )
            )

    first_hash = canonical_sha256(first)
    second_hash = canonical_sha256(second)
    digest = canonical_sha256(
        {
            "contract": "independent-retrieval-review-v1",
            "capture_sha256": capture_hash,
            "review_pool_sha256s": (first_hash, second_hash),
        }
    )
    return IndependentRetrievalReviewAgreementReport(
        report_id=f"{first.pool_id}-agreement-{digest[:12]}",
        pool_id=first.pool_id,
        capture_sha256=capture_hash,
        generated_at=generated_at or datetime.now(UTC),
        first_review=ReviewedRetrievalPoolIdentity(
            reviewer=first.reviewer,
            reviewed_at=first.reviewed_at,
            pool_sha256=first_hash,
        ),
        second_review=ReviewedRetrievalPoolIdentity(
            reviewer=second.reviewer,
            reviewed_at=second.reviewed_at,
            pool_sha256=second_hash,
        ),
        overall=_agreement_metrics(grades),
        queries={
            query_id: _agreement_metrics(values) for query_id, values in sorted(by_query.items())
        },
        slices={
            slice_name: _agreement_metrics(values)
            for slice_name, values in sorted(by_slice.items())
        },
        sources={
            source: _agreement_metrics(values) for source, values in sorted(by_source.items())
        },
        confusion_matrix=_agreement_confusion(grades),
        disagreements=tuple(
            sorted(disagreements, key=lambda item: (item.query_id, item.document_id))
        ),
    )


def _review_values(
    report: IndependentRetrievalReviewAgreementReport,
    disagreement: RetrievalReviewDisagreement,
) -> dict[str, str]:
    reviews = [
        (str(disagreement.first_relevance), disagreement.first_rationale or ""),
        (str(disagreement.second_relevance), disagreement.second_rationale or ""),
    ]
    identity = f"{report.report_id}:{disagreement.query_id}:{disagreement.document_id}"
    if int(sha256(identity.encode()).hexdigest(), 16) % 2:
        reviews.reverse()
    return {
        "agreement_report_id": report.report_id,
        "review_a_relevance_0_to_3": reviews[0][0],
        "review_a_rationale": spreadsheet_safe_text(reviews[0][1]),
        "review_b_relevance_0_to_3": reviews[1][0],
        "review_b_rationale": spreadsheet_safe_text(reviews[1][1]),
    }


def _first_candidate_lookup(
    pool: CandidatePool,
) -> dict[JudgmentKey, tuple[EvaluationQuery, PooledCandidate]]:
    return {
        (pooled_query.query.query_id, candidate.document_id): (
            pooled_query.query,
            candidate,
        )
        for pooled_query in pool.queries
        for candidate in pooled_query.candidates
    }


def _protected_adjudication_fields(
    report: IndependentRetrievalReviewAgreementReport,
    disagreement: RetrievalReviewDisagreement,
    query: EvaluationQuery,
    candidate: PooledCandidate,
) -> dict[str, str]:
    return {
        **judgment_metadata(query.query_id, query.text, candidate),
        **_review_values(report, disagreement),
    }


def build_retrieval_adjudication_sheet(
    first: CandidatePool,
    second: CandidatePool,
) -> RetrievalAdjudicationSheet:
    """Export only disagreements with system and reviewer identities hidden."""
    first, second, _ = _canonical_reviews(first, second)
    report = compare_independent_retrieval_reviews(first, second)
    candidates = _first_candidate_lookup(first)
    blind_order = sorted(
        report.disagreements,
        key=lambda item: sha256(
            f"{report.report_id}:{item.query_id}:{item.document_id}".encode()
        ).hexdigest(),
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=ADJUDICATION_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for disagreement in blind_order:
        query, candidate = candidates[(disagreement.query_id, disagreement.document_id)]
        writer.writerow(
            {
                **_protected_adjudication_fields(
                    report,
                    disagreement,
                    query,
                    candidate,
                ),
                "adjudicated_relevance_0_to_3": "",
                "adjudication_rationale": "",
            }
        )
    return RetrievalAdjudicationSheet(
        content=output.getvalue(),
        agreement_report=report,
        pending_count=len(report.disagreements),
    )


def render_retrieval_adjudication_rows(rows: Sequence[Mapping[str, str]]) -> str:
    """Serialize validated adjudication rows without changing protected fields."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=ADJUDICATION_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _parse_adjudication_rows(
    first: CandidatePool,
    second: CandidatePool,
    csv_text: str,
    *,
    require_complete: bool,
) -> tuple[list[dict[str, str]], dict[JudgmentKey, ParsedDecision]]:
    first, second, _ = _canonical_reviews(first, second)
    sheet = build_retrieval_adjudication_sheet(first, second)
    report = sheet.agreement_report
    disagreements = {(item.query_id, item.document_id): item for item in report.disagreements}
    candidates = _first_candidate_lookup(first)
    reader = csv.DictReader(io.StringIO(csv_text, newline=""))
    if tuple(reader.fieldnames or ()) != ADJUDICATION_COLUMNS:
        raise ValueError(f"adjudication columns must exactly equal {ADJUDICATION_COLUMNS}")

    rows: list[dict[str, str]] = []
    decisions: dict[JudgmentKey, ParsedDecision] = {}
    for line_number, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(
                f"adjudication row {line_number} contains unexpected or missing columns"
            )
        key = (row["query_id"], row["document_id"])
        disagreement = disagreements.get(key)
        if disagreement is None:
            raise ValueError(f"adjudication row {line_number} is not a known disagreement")
        if key in decisions:
            raise ValueError(f"adjudication row {line_number} duplicates {key[0]} / {key[1]}")
        query, candidate = candidates[key]
        protected = _protected_adjudication_fields(
            report,
            disagreement,
            query,
            candidate,
        )
        changed = [field for field, value in protected.items() if row[field] != value]
        if changed:
            raise ValueError(
                f"adjudication row {line_number} changed protected fields: " + ", ".join(changed)
            )

        raw_relevance = row["adjudicated_relevance_0_to_3"].strip()
        if not raw_relevance and not require_complete:
            relevance = None
        else:
            try:
                relevance = int(raw_relevance)
            except ValueError as error:
                raise ValueError(
                    f"adjudication row {line_number} requires an integer relevance grade"
                ) from error
            if relevance not in RELEVANCE_GRADES:
                raise ValueError(f"adjudication row {line_number} relevance must be within [0, 3]")

        rationale = " ".join(row["adjudication_rationale"].split()) or None
        if relevance is None and rationale is not None:
            raise ValueError(
                f"adjudication row {line_number} cannot contain a rationale without a grade"
            )
        if relevance is not None and (rationale is None or len(rationale) < 10):
            raise ValueError(
                f"adjudication row {line_number} requires a rationale of at least 10 characters"
            )
        if rationale is not None and len(rationale) > 1_000:
            raise ValueError(f"adjudication row {line_number} rationale exceeds 1000 characters")
        rows.append(dict(row))
        decisions[key] = (relevance, rationale)

    missing = sorted(set(disagreements) - set(decisions))
    if missing:
        raise ValueError(f"adjudication sheet is missing {len(missing)} disagreement(s)")
    return rows, decisions


def validate_partial_retrieval_adjudication(
    first: CandidatePool,
    second: CandidatePool,
    csv_text: str,
) -> tuple[dict[str, str], ...]:
    """Validate a resumable disagreement sheet while permitting blank decisions."""
    rows, _decisions = _parse_adjudication_rows(
        first,
        second,
        csv_text,
        require_complete=False,
    )
    return tuple(rows)


def apply_retrieval_adjudication(
    first: CandidatePool,
    second: CandidatePool,
    csv_text: str,
    *,
    adjudicator: str,
    adjudicated_at: datetime | None = None,
) -> tuple[CandidatePool, RetrievalAdjudicationReport]:
    """Import protected disagreement decisions and create one final gold pool."""
    first, second, _ = _canonical_reviews(first, second)
    agreement = compare_independent_retrieval_reviews(first, second)
    normalized_adjudicator = " ".join(adjudicator.split())
    if not normalized_adjudicator:
        raise ValueError("adjudicator must not be empty")
    if normalized_adjudicator.casefold() in {
        agreement.first_review.reviewer.casefold(),
        agreement.second_review.reviewer.casefold(),
    }:
        raise ValueError("adjudicator must be independent from both reviewers")
    _rows, decisions = _parse_adjudication_rows(
        first,
        second,
        csv_text,
        require_complete=True,
    )

    first_candidates = _first_candidate_lookup(first)
    second_candidates = _first_candidate_lookup(second)
    decision_records: list[RetrievalAdjudicationDecision] = []
    data = first.model_dump(mode="python")
    for pooled_query in data["queries"]:
        query_id = pooled_query["query"]["query_id"]
        for candidate in pooled_query["candidates"]:
            key = (query_id, candidate["document_id"])
            first_candidate = first_candidates[key][1]
            second_candidate = second_candidates[key][1]
            if first_candidate.relevance == second_candidate.relevance:
                candidate["relevance"] = first_candidate.relevance
                candidate["rationale"] = "Independent reviewers agreed."
                continue
            final_relevance, rationale = decisions[key]
            if final_relevance is None or rationale is None:  # pragma: no cover
                raise AssertionError("complete adjudication returned a blank decision")
            candidate["relevance"] = final_relevance
            candidate["rationale"] = rationale
            decision_records.append(
                RetrievalAdjudicationDecision(
                    query_id=query_id,
                    document_id=first_candidate.document_id,
                    source=first_candidate.source,
                    first_relevance=cast(int, first_candidate.relevance),
                    second_relevance=cast(int, second_candidate.relevance),
                    adjudicated_relevance=final_relevance,
                    rationale=rationale,
                )
            )

    finalized_at = adjudicated_at or datetime.now(UTC)
    provenance = IndependentRetrievalAdjudicationProvenance(
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
    final_pool = CandidatePool.model_validate(data)
    final_hash = canonical_sha256(final_pool)
    report = RetrievalAdjudicationReport(
        report_id=f"{final_pool.pool_id}-adjudication-{final_hash[:12]}",
        agreement_report_id=agreement.report_id,
        pool_id=final_pool.pool_id,
        capture_sha256=agreement.capture_sha256,
        generated_at=finalized_at,
        first_review=agreement.first_review,
        second_review=agreement.second_review,
        adjudicator=normalized_adjudicator,
        judgment_count=agreement.overall.judgment_count,
        inherited_agreement_count=agreement.overall.agreement_count,
        adjudication_decision_count=len(decision_records),
        final_pool_sha256=final_hash,
        decisions=tuple(
            sorted(decision_records, key=lambda item: (item.query_id, item.document_id))
        ),
    )
    return final_pool, report

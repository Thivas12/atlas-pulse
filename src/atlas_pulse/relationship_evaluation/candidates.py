"""Gold-blind candidate prediction artifacts and paired relationship comparison."""

from __future__ import annotations

import csv
import io
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import Field, field_validator, model_validator

from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.projections.base import SourceName
from atlas_pulse.relationship_evaluation.adjudication import relationship_capture_sha256
from atlas_pulse.relationship_evaluation.base import (
    RELATIONSHIP_LABELS,
    EventEvidence,
    GeometryBasis,
    IndependentAdjudicationProvenance,
    RelationshipPool,
    RelationshipSliceMetrics,
    ReviewRubricVersion,
    StrictModel,
    relationship_case_id,
)
from atlas_pulse.relationship_evaluation.metrics import calculate_relationship_scores
from atlas_pulse.relationships import ClaimPredicate, RelationshipLabel

CandidateArtifactSchemaVersion = Literal["1.0.0"]
CandidatePromotionStatus = Literal["blocked"]
CandidateOutcomeKind = Literal[
    "unchanged_correct",
    "unchanged_incorrect",
    "improvement",
    "regression",
    "changed_incorrect",
]

CANDIDATE_PREDICTION_COLUMNS = (
    "task_id",
    "task_sha256",
    "case_id",
    "predicate",
    "source_pair",
    "predicted_label",
    "latency_ms",
)

_PROMOTION_BLOCKERS = (
    "No checked-in candidate promotion thresholds exist; this comparison is descriptive.",
    "The deployed rule has no isolated per-case latency measurement, so candidate latency is not a paired performance gate.",
    "Production use requires explicit human approval, regression verification, and a new versioned relationship rule.",
)

_CANDIDATE_CAVEATS = (
    "The candidate task contains reviewer-visible evidence only; it excludes human gold labels, rationales, reviewer identities, and deployed predictions.",
    "Gold labels compare what two public source records explicitly say and do not establish that either source is true.",
    "Only pairs in the bounded measured graph are evaluated, and source-pair caps do not estimate live source prevalence.",
    "Candidate latency is supplied by the external runner and is descriptive; the artifact cannot verify the measurement environment.",
    "The software binds predictions to exact evidence and an adjudicated pool but cannot prove that model selection or tuning was blind to the gold labels.",
    "Scoring a candidate never changes the captured pool or the production relationship annotation version.",
)


class CandidateSystemDefinition(StrictModel):
    """Immutable identity for one externally executed local candidate system."""

    schema_version: CandidateArtifactSchemaVersion = "1.0.0"
    candidate_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$")
    model_id: str = Field(min_length=1, max_length=500)
    model_revision: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    model_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    adapter_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$")
    input_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime: str = Field(min_length=1, max_length=200)
    runtime_version: str = Field(min_length=1, max_length=200)
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @field_validator("model_id", "runtime", "runtime_version")
    @classmethod
    def normalize_text_identity(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("candidate identity fields must not be blank")
        return normalized

    @field_validator("parameters")
    @classmethod
    def validate_parameters(
        cls, value: dict[str, str | int | float | bool]
    ) -> dict[str, str | int | float | bool]:
        for key, item in value.items():
            if not key or key != key.strip() or len(key) > 120:
                raise ValueError("candidate parameter keys must be non-blank normalized strings")
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("candidate parameter values must be finite")
        return value


class CandidateCaseInput(StrictModel):
    """One model-visible case containing evidence but no human or deployed label."""

    case_id: str = Field(pattern=r"^pair-[0-9a-f]{20}$")
    edge_id: str = Field(min_length=1, max_length=200)
    predicate: ClaimPredicate
    source_pair: tuple[SourceName, SourceName]
    left: EventEvidence
    right: EventEvidence
    distance_km: float = Field(ge=0, allow_inf_nan=False)
    time_delta_minutes: float = Field(ge=0, allow_inf_nan=False)
    left_geometry_basis: GeometryBasis
    right_geometry_basis: GeometryBasis

    @model_validator(mode="after")
    def validate_evidence_identity(self) -> CandidateCaseInput:
        if self.left.node_id >= self.right.node_id:
            raise ValueError("candidate evidence must use canonical node order")
        if self.left.source == self.right.source:
            raise ValueError("candidate evidence endpoints must use independent sources")
        expected_sources = tuple(sorted((self.left.source, self.right.source)))
        if self.source_pair != expected_sources:
            raise ValueError("candidate source_pair must equal the sorted endpoint sources")
        expected_id = relationship_case_id(
            edge_id=self.edge_id,
            predicate=self.predicate,
            left=self.left,
            right=self.right,
            distance_km=self.distance_km,
            time_delta_minutes=self.time_delta_minutes,
            left_geometry_basis=self.left_geometry_basis,
            right_geometry_basis=self.right_geometry_basis,
        )
        if self.case_id != expected_id:
            raise ValueError("candidate case_id must match the exact evidence pair")
        return self


class CandidateEvaluationTask(StrictModel):
    """Content-addressed, gold-blind input for any relationship candidate."""

    schema_version: CandidateArtifactSchemaVersion = "1.0.0"
    task_id: str = Field(pattern=r"^candidate-task-[0-9a-f]{20}$")
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    benchmark_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$")
    rubric_version: ReviewRubricVersion
    predicates: tuple[ClaimPredicate, ...] = Field(min_length=1, max_length=3)
    case_count: int = Field(ge=1)
    cases: tuple[CandidateCaseInput, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_task(self) -> CandidateEvaluationTask:
        if self.case_count != len(self.cases):
            raise ValueError("candidate task case_count must match cases")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("candidate task case IDs must be unique")
        if case_ids != sorted(case_ids):
            raise ValueError("candidate task cases must use canonical case ID order")
        if len(self.predicates) != len(set(self.predicates)):
            raise ValueError("candidate task predicates must be unique")
        if {case.predicate for case in self.cases} != set(self.predicates):
            raise ValueError("candidate task predicates must match its cases")
        predicates_by_edge: dict[str, set[ClaimPredicate]] = {}
        for case in self.cases:
            predicates_by_edge.setdefault(case.edge_id, set()).add(case.predicate)
        if any(predicates != set(self.predicates) for predicates in predicates_by_edge.values()):
            raise ValueError("every candidate task edge must contain every predicate")
        expected_hash = candidate_task_sha256(self)
        if self.task_sha256 != expected_hash:
            raise ValueError("candidate task_sha256 must match its exact evidence payload")
        if self.task_id != f"candidate-task-{expected_hash[:20]}":
            raise ValueError("candidate task_id must match task_sha256")
        return self


class CandidatePrediction(StrictModel):
    """One externally produced label with observed inference latency."""

    case_id: str = Field(pattern=r"^pair-[0-9a-f]{20}$")
    predicted_label: RelationshipLabel
    latency_ms: float = Field(ge=0, allow_inf_nan=False)


class CandidatePredictionBatch(StrictModel):
    """Content-addressed predictions from one immutable candidate definition."""

    schema_version: CandidateArtifactSchemaVersion = "1.0.0"
    batch_id: str = Field(pattern=r"^candidate-batch-[0-9a-f]{20}$")
    batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(pattern=r"^candidate-task-[0-9a-f]{20}$")
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    system: CandidateSystemDefinition
    predictions: tuple[CandidatePrediction, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_batch(self) -> CandidatePredictionBatch:
        if self.generated_at.tzinfo is None:
            raise ValueError("candidate prediction generated_at must be timezone-aware")
        case_ids = [prediction.case_id for prediction in self.predictions]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("candidate prediction case IDs must be unique")
        if case_ids != sorted(case_ids):
            raise ValueError("candidate predictions must use canonical case ID order")
        expected_hash = candidate_prediction_batch_sha256(self)
        if self.batch_sha256 != expected_hash:
            raise ValueError("candidate batch_sha256 must match its exact prediction payload")
        if self.batch_id != f"candidate-batch-{expected_hash[:20]}":
            raise ValueError("candidate batch_id must match batch_sha256")
        return self


@dataclass(frozen=True, slots=True)
class CandidatePredictionSheet:
    """Portable CSV template paired with a gold-blind candidate task."""

    content: str
    pending_count: int


class RelationshipMetricDelta(StrictModel):
    """Candidate minus deployed-rule measurements for one exact slice."""

    accuracy: float = Field(ge=-1, le=1, allow_inf_nan=False)
    macro_f1: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    decisive_coverage: float = Field(ge=-1, le=1, allow_inf_nan=False)
    abstention_rate: float = Field(ge=-1, le=1, allow_inf_nan=False)
    selective_accuracy: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)


class RelationshipSliceComparison(StrictModel):
    """Paired baseline and candidate metrics plus direct deltas."""

    baseline: RelationshipSliceMetrics
    candidate: RelationshipSliceMetrics
    delta: RelationshipMetricDelta

    @model_validator(mode="after")
    def validate_delta(self) -> RelationshipSliceComparison:
        if self.baseline.case_count != self.candidate.case_count:
            raise ValueError("paired relationship slices must contain the same cases")
        for name in (
            "accuracy",
            "macro_f1",
            "decisive_coverage",
            "abstention_rate",
            "selective_accuracy",
        ):
            baseline = getattr(self.baseline, name)
            candidate = getattr(self.candidate, name)
            actual = getattr(self.delta, name)
            expected = None if baseline is None or candidate is None else candidate - baseline
            if expected is None:
                if actual is not None:
                    raise ValueError(
                        f"{name} delta must be undefined when either metric is undefined"
                    )
            elif actual is None or abs(actual - expected) > 1e-12:
                raise ValueError(f"{name} delta must equal candidate minus baseline")
        return self


class CandidateLatencyMetrics(StrictModel):
    """Descriptive external-runner latency without a baseline comparison claim."""

    case_count: int = Field(ge=1)
    total_ms: float = Field(ge=0, allow_inf_nan=False)
    mean_ms: float = Field(ge=0, allow_inf_nan=False)
    p50_ms: float = Field(ge=0, allow_inf_nan=False)
    p95_ms: float = Field(ge=0, allow_inf_nan=False)
    max_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_latency(self) -> CandidateLatencyMetrics:
        if not math.isclose(
            self.mean_ms * self.case_count,
            self.total_ms,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ValueError("candidate mean latency must match total and case count")
        if not self.p50_ms <= self.p95_ms <= self.max_ms:
            raise ValueError("candidate latency percentiles must be monotonic")
        return self


class CandidateCaseOutcome(StrictModel):
    """Paired deployed-rule and candidate result for one exact gold case."""

    case_id: str = Field(pattern=r"^pair-[0-9a-f]{20}$")
    edge_id: str
    predicate: ClaimPredicate
    source_pair: tuple[SourceName, SourceName]
    baseline_label: RelationshipLabel
    candidate_label: RelationshipLabel
    gold_label: RelationshipLabel
    baseline_correct: bool
    candidate_correct: bool
    outcome: CandidateOutcomeKind

    @model_validator(mode="after")
    def validate_outcome(self) -> CandidateCaseOutcome:
        if self.baseline_correct != (self.baseline_label == self.gold_label):
            raise ValueError("baseline correctness must match baseline and gold labels")
        if self.candidate_correct != (self.candidate_label == self.gold_label):
            raise ValueError("candidate correctness must match candidate and gold labels")
        expected = _outcome_kind(
            self.baseline_label,
            self.candidate_label,
            self.gold_label,
        )
        if self.outcome != expected:
            raise ValueError("candidate outcome category must match paired labels")
        return self


class PairedOutcomeSummary(StrictModel):
    """Counts of candidate gains, regressions, and unchanged results."""

    case_count: int = Field(ge=1)
    unchanged_correct: int = Field(ge=0)
    unchanged_incorrect: int = Field(ge=0)
    improvements: int = Field(ge=0)
    regressions: int = Field(ge=0)
    changed_incorrect: int = Field(ge=0)
    label_disagreements: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> PairedOutcomeSummary:
        partition = (
            self.unchanged_correct
            + self.unchanged_incorrect
            + self.improvements
            + self.regressions
            + self.changed_incorrect
        )
        if partition != self.case_count:
            raise ValueError("paired outcome counts must cover every case")
        if self.label_disagreements > self.case_count:
            raise ValueError("label disagreements cannot exceed case count")
        return self


class RelationshipCandidateComparisonReport(StrictModel):
    """Auditable paired comparison that cannot authorize production promotion."""

    schema_version: CandidateArtifactSchemaVersion = "1.0.0"
    report_id: str = Field(min_length=1, max_length=240)
    generated_at: datetime
    gold_pool_id: str
    gold_pool_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(pattern=r"^candidate-task-[0-9a-f]{20}$")
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prediction_batch_id: str = Field(pattern=r"^candidate-batch-[0-9a-f]{20}$")
    prediction_batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rubric_version: ReviewRubricVersion
    baseline_relationship_rule_version: str
    candidate_system: CandidateSystemDefinition
    adjudication: IndependentAdjudicationProvenance
    case_count: int = Field(ge=1)
    overall: RelationshipSliceComparison
    predicates: dict[ClaimPredicate, RelationshipSliceComparison]
    source_pairs: dict[str, RelationshipSliceComparison]
    baseline_confusion_matrix: dict[RelationshipLabel, dict[RelationshipLabel, int]]
    candidate_confusion_matrix: dict[RelationshipLabel, dict[RelationshipLabel, int]]
    prediction_transition_matrix: dict[RelationshipLabel, dict[RelationshipLabel, int]]
    paired_outcomes: PairedOutcomeSummary
    candidate_latency: CandidateLatencyMetrics
    outcomes: tuple[CandidateCaseOutcome, ...]
    promotion_status: CandidatePromotionStatus = "blocked"
    promotion_blockers: tuple[str, ...] = _PROMOTION_BLOCKERS
    caveats: tuple[str, ...] = _CANDIDATE_CAVEATS

    @model_validator(mode="after")
    def validate_report(self) -> RelationshipCandidateComparisonReport:
        if self.generated_at.tzinfo is None:
            raise ValueError("candidate comparison generated_at must be timezone-aware")
        if self.overall.baseline.case_count != self.case_count:
            raise ValueError("candidate report case_count must match overall metrics")
        if self.paired_outcomes.case_count != self.case_count:
            raise ValueError("candidate report case_count must match paired outcomes")
        if self.candidate_latency.case_count != self.case_count:
            raise ValueError("candidate report case_count must match latency metrics")
        if len(self.outcomes) != self.case_count:
            raise ValueError("candidate report must retain every case outcome")
        outcome_ids = [outcome.case_id for outcome in self.outcomes]
        if len(outcome_ids) != len(set(outcome_ids)):
            raise ValueError("candidate report outcome IDs must be unique")
        for matrix_name in (
            "baseline_confusion_matrix",
            "candidate_confusion_matrix",
            "prediction_transition_matrix",
        ):
            matrix = getattr(self, matrix_name)
            if set(matrix) != set(RELATIONSHIP_LABELS) or any(
                set(row) != set(RELATIONSHIP_LABELS) for row in matrix.values()
            ):
                raise ValueError(f"{matrix_name} must contain every relationship label")
            if any(count < 0 for row in matrix.values() for count in row.values()):
                raise ValueError(f"{matrix_name} counts must be non-negative")
            if sum(sum(row.values()) for row in matrix.values()) != self.case_count:
                raise ValueError(f"{matrix_name} must cover every case")
        identity = canonical_sha256(
            {
                "gold_pool_sha256": self.gold_pool_sha256,
                "prediction_batch_sha256": self.prediction_batch_sha256,
            }
        )
        expected_report_id = f"{self.gold_pool_id}-candidate-{identity[:12]}"
        if self.report_id != expected_report_id:
            raise ValueError("candidate report_id must match its gold pool and prediction batch")
        if self.promotion_blockers != _PROMOTION_BLOCKERS:
            raise ValueError("candidate report must retain every fixed promotion blocker")
        if self.caveats != _CANDIDATE_CAVEATS:
            raise ValueError("candidate report must retain every fixed caveat")
        return self


def candidate_task_sha256(task: CandidateEvaluationTask) -> str:
    """Hash a candidate task without its self-describing identity fields."""
    return canonical_sha256(
        task.model_dump(
            mode="json",
            exclude={"task_id", "task_sha256"},
            exclude_none=False,
        )
    )


def candidate_prediction_batch_sha256(batch: CandidatePredictionBatch) -> str:
    """Hash a prediction batch without its self-describing identity fields."""
    return canonical_sha256(
        batch.model_dump(
            mode="json",
            exclude={"batch_id", "batch_sha256"},
            exclude_none=False,
        )
    )


def build_candidate_evaluation_task(pool: RelationshipPool) -> CandidateEvaluationTask:
    """Export exact model inputs only after independent adjudication is complete."""
    if pool.adjudication is None:
        raise ValueError("candidate evaluation requires a finalized independently adjudicated pool")
    cases = tuple(
        sorted(
            (CandidateCaseInput.model_validate(case.review_identity()) for case in pool.cases),
            key=lambda case: case.case_id,
        )
    )
    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "source_capture_sha256": relationship_capture_sha256(pool),
        "benchmark_id": pool.benchmark_id,
        "rubric_version": pool.rubric_version,
        "predicates": pool.predicates,
        "case_count": len(cases),
        "cases": [case.model_dump(mode="json", exclude_none=False) for case in cases],
    }
    digest = canonical_sha256(payload)
    return CandidateEvaluationTask(
        task_id=f"candidate-task-{digest[:20]}",
        task_sha256=digest,
        **payload,
    )


def _prediction_metadata(task: CandidateEvaluationTask, case: CandidateCaseInput) -> dict[str, str]:
    return {
        "task_id": task.task_id,
        "task_sha256": task.task_sha256,
        "case_id": case.case_id,
        "predicate": case.predicate,
        "source_pair": "+".join(case.source_pair),
    }


def build_candidate_prediction_sheet(task: CandidateEvaluationTask) -> CandidatePredictionSheet:
    """Create a protected, label-empty CSV for an external model runner."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CANDIDATE_PREDICTION_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for case in task.cases:
        writer.writerow(
            {
                **_prediction_metadata(task, case),
                "predicted_label": "",
                "latency_ms": "",
            }
        )
    return CandidatePredictionSheet(content=output.getvalue(), pending_count=len(task.cases))


def _prediction_batch_payload(
    task: CandidateEvaluationTask,
    system: CandidateSystemDefinition,
    predictions: tuple[CandidatePrediction, ...],
    generated_at: datetime,
) -> dict[str, object]:
    timestamp = generated_at.isoformat()
    if timestamp.endswith("+00:00"):
        timestamp = timestamp[:-6] + "Z"
    return {
        "schema_version": "1.0.0",
        "task_id": task.task_id,
        "task_sha256": task.task_sha256,
        "generated_at": timestamp,
        "system": system.model_dump(mode="json", exclude_none=False),
        "predictions": [
            prediction.model_dump(mode="json", exclude_none=False) for prediction in predictions
        ],
    }


def apply_candidate_predictions(
    task: CandidateEvaluationTask,
    csv_text: str,
    *,
    system: CandidateSystemDefinition,
    generated_at: datetime | None = None,
) -> CandidatePredictionBatch:
    """Import one complete protected prediction sheet and bind its system identity."""
    reader = csv.DictReader(io.StringIO(csv_text, newline=""))
    if tuple(reader.fieldnames or ()) != CANDIDATE_PREDICTION_COLUMNS:
        raise ValueError(
            f"candidate prediction columns must exactly equal {CANDIDATE_PREDICTION_COLUMNS}"
        )
    cases = {case.case_id: case for case in task.cases}
    predictions: dict[str, CandidatePrediction] = {}
    allowed = set(RELATIONSHIP_LABELS)
    for line_number, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(f"candidate prediction row {line_number} has unexpected columns")
        case_id = row["case_id"]
        case = cases.get(case_id)
        if case is None:
            raise ValueError(f"candidate prediction row {line_number} is not a known task case")
        if case_id in predictions:
            raise ValueError(f"candidate prediction row {line_number} duplicates {case_id}")
        protected = _prediction_metadata(task, case)
        changed = [field for field, value in protected.items() if row[field] != value]
        if changed:
            raise ValueError(
                f"candidate prediction row {line_number} changed protected fields: "
                + ", ".join(changed)
            )
        label_text = row["predicted_label"].strip()
        if label_text not in allowed:
            raise ValueError(
                f"candidate prediction row {line_number} requires one of {sorted(allowed)}"
            )
        latency_text = row["latency_ms"].strip()
        try:
            latency_ms = float(latency_text)
        except ValueError as error:
            raise ValueError(
                f"candidate prediction row {line_number} requires numeric latency_ms"
            ) from error
        if not math.isfinite(latency_ms) or latency_ms < 0:
            raise ValueError(
                f"candidate prediction row {line_number} latency_ms must be finite and non-negative"
            )
        predictions[case_id] = CandidatePrediction(
            case_id=case_id,
            predicted_label=cast(RelationshipLabel, label_text),
            latency_ms=latency_ms,
        )

    missing = sorted(set(cases) - set(predictions))
    if missing:
        raise ValueError(f"candidate prediction sheet is missing {len(missing)} case(s)")
    ordered = tuple(predictions[case.case_id] for case in task.cases)
    completed_at = generated_at or datetime.now(UTC)
    if completed_at.tzinfo is None:
        raise ValueError("candidate prediction generated_at must be timezone-aware")
    payload = _prediction_batch_payload(task, system, ordered, completed_at)
    digest = canonical_sha256(payload)
    return CandidatePredictionBatch(
        batch_id=f"candidate-batch-{digest[:20]}",
        batch_sha256=digest,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        generated_at=completed_at,
        system=system,
        predictions=ordered,
    )


def _delta(
    baseline: RelationshipSliceMetrics,
    candidate: RelationshipSliceMetrics,
) -> RelationshipMetricDelta:
    def difference(left: float | None, right: float | None) -> float | None:
        return None if left is None or right is None else right - left

    return RelationshipMetricDelta(
        accuracy=candidate.accuracy - baseline.accuracy,
        macro_f1=difference(baseline.macro_f1, candidate.macro_f1),
        decisive_coverage=candidate.decisive_coverage - baseline.decisive_coverage,
        abstention_rate=candidate.abstention_rate - baseline.abstention_rate,
        selective_accuracy=difference(
            baseline.selective_accuracy,
            candidate.selective_accuracy,
        ),
    )


def _comparison(
    baseline: RelationshipSliceMetrics,
    candidate: RelationshipSliceMetrics,
) -> RelationshipSliceComparison:
    return RelationshipSliceComparison(
        baseline=baseline,
        candidate=candidate,
        delta=_delta(baseline, candidate),
    )


def _percentile(values: tuple[float, ...], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _latency(predictions: tuple[CandidatePrediction, ...]) -> CandidateLatencyMetrics:
    values = tuple(prediction.latency_ms for prediction in predictions)
    total = sum(values)
    return CandidateLatencyMetrics(
        case_count=len(values),
        total_ms=total,
        mean_ms=total / len(values),
        p50_ms=_percentile(values, 0.50),
        p95_ms=_percentile(values, 0.95),
        max_ms=max(values),
    )


def _outcome_kind(
    baseline: RelationshipLabel,
    candidate: RelationshipLabel,
    gold: RelationshipLabel,
) -> CandidateOutcomeKind:
    if baseline == candidate:
        return "unchanged_correct" if baseline == gold else "unchanged_incorrect"
    if baseline != gold and candidate == gold:
        return "improvement"
    if baseline == gold and candidate != gold:
        return "regression"
    return "changed_incorrect"


def _transition_matrix(
    outcomes: tuple[CandidateCaseOutcome, ...],
) -> dict[RelationshipLabel, dict[RelationshipLabel, int]]:
    return {
        baseline: {
            candidate: sum(
                outcome.baseline_label == baseline and outcome.candidate_label == candidate
                for outcome in outcomes
            )
            for candidate in RELATIONSHIP_LABELS
        }
        for baseline in RELATIONSHIP_LABELS
    }


def score_relationship_candidate(
    pool: RelationshipPool,
    task: CandidateEvaluationTask,
    batch: CandidatePredictionBatch,
    *,
    generated_at: datetime | None = None,
) -> RelationshipCandidateComparisonReport:
    """Compare one candidate with captured deployed labels over exact adjudicated gold."""
    if pool.adjudication is None:
        raise ValueError("candidate scoring requires a finalized independently adjudicated pool")
    expected_task = build_candidate_evaluation_task(pool)
    if task != expected_task:
        raise ValueError("candidate task does not match the exact adjudicated pool capture")
    if batch.task_id != task.task_id or batch.task_sha256 != task.task_sha256:
        raise ValueError("candidate prediction batch does not match the evaluation task")

    baseline_predictions = {case.case_id: case.system_prediction.label for case in pool.cases}
    candidate_predictions = {
        prediction.case_id: prediction.predicted_label for prediction in batch.predictions
    }
    baseline = calculate_relationship_scores(pool.cases, baseline_predictions)
    candidate = calculate_relationship_scores(pool.cases, candidate_predictions)

    case_by_id = {case.case_id: case for case in pool.cases}
    outcomes: list[CandidateCaseOutcome] = []
    for case_id in sorted(case_by_id):
        case = case_by_id[case_id]
        if case.gold_label is None:
            raise ValueError("adjudicated pool contains an unlabeled case")
        baseline_label = baseline_predictions[case_id]
        candidate_label = candidate_predictions[case_id]
        outcomes.append(
            CandidateCaseOutcome(
                case_id=case_id,
                edge_id=case.edge_id,
                predicate=case.predicate,
                source_pair=case.source_pair,
                baseline_label=baseline_label,
                candidate_label=candidate_label,
                gold_label=case.gold_label,
                baseline_correct=baseline_label == case.gold_label,
                candidate_correct=candidate_label == case.gold_label,
                outcome=_outcome_kind(baseline_label, candidate_label, case.gold_label),
            )
        )
    outcome_tuple = tuple(outcomes)
    counts = Counter(outcome.outcome for outcome in outcome_tuple)
    paired = PairedOutcomeSummary(
        case_count=len(outcome_tuple),
        unchanged_correct=counts["unchanged_correct"],
        unchanged_incorrect=counts["unchanged_incorrect"],
        improvements=counts["improvement"],
        regressions=counts["regression"],
        changed_incorrect=counts["changed_incorrect"],
        label_disagreements=sum(
            outcome.baseline_label != outcome.candidate_label for outcome in outcome_tuple
        ),
    )

    gold_pool_hash = canonical_sha256(pool)
    report_identity = canonical_sha256(
        {
            "gold_pool_sha256": gold_pool_hash,
            "prediction_batch_sha256": batch.batch_sha256,
        }
    )
    completed_at = generated_at or datetime.now(UTC)
    return RelationshipCandidateComparisonReport(
        report_id=f"{pool.pool_id}-candidate-{report_identity[:12]}",
        generated_at=completed_at,
        gold_pool_id=pool.pool_id,
        gold_pool_sha256=gold_pool_hash,
        source_capture_sha256=task.source_capture_sha256,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        prediction_batch_id=batch.batch_id,
        prediction_batch_sha256=batch.batch_sha256,
        rubric_version=pool.rubric_version,
        baseline_relationship_rule_version=pool.relationship_rule_version,
        candidate_system=batch.system,
        adjudication=pool.adjudication,
        case_count=len(pool.cases),
        overall=_comparison(baseline.overall, candidate.overall),
        predicates={
            name: _comparison(baseline.predicates[name], value)
            for name, value in candidate.predicates.items()
        },
        source_pairs={
            name: _comparison(baseline.source_pairs[name], value)
            for name, value in candidate.source_pairs.items()
        },
        baseline_confusion_matrix=baseline.confusion_matrix,
        candidate_confusion_matrix=candidate.confusion_matrix,
        prediction_transition_matrix=_transition_matrix(outcome_tuple),
        paired_outcomes=paired,
        candidate_latency=_latency(batch.predictions),
        outcomes=outcome_tuple,
    )

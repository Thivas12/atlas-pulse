"""Strict contracts for live, human-reviewed claim-pair evaluation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atlas_pulse.projections import CorrelationQuery, GeoBounds
from atlas_pulse.projections.base import SourceName
from atlas_pulse.relationships import ClaimPredicate, ClaimScope, RelationshipLabel

SchemaVersion = Literal["1.0.0"]
RelationshipArtifactSchemaVersion = Literal["1.0.0", "1.1.0"]
JudgmentStatus = Literal["unjudged", "reviewed"]
ReviewRubricVersion = Literal["claim-pair-rubric-v1"]
SamplingMethod = Literal["source-pair-stable-hash-v1"]
GeometryBasis = Literal["point", "polygon"]
ReviewProcessVersion = Literal["independent-review-adjudication-v1"]

RELATIONSHIP_LABELS: tuple[RelationshipLabel, ...] = (
    "corroborates",
    "contradicts",
    "insufficient_evidence",
)


class StrictModel(BaseModel):
    """Forbid silent drift in machine-generated and reviewer-edited artifacts."""

    model_config = ConfigDict(extra="forbid")


class RelationshipCaptureParameters(StrictModel):
    """Exact bounded parameters sent to the production incident endpoint."""

    radius_km: float = Field(default=50.0, gt=0, le=500, allow_inf_nan=False)
    time_window_minutes: int = Field(default=360, ge=1, le=1_440)
    lookback_hours: int = Field(default=24, ge=1, le=168)
    candidate_edge_limit: int = Field(default=2_000, ge=1, le=5_000)
    incident_limit: int = Field(default=100, ge=1, le=100)
    active_only: bool = True
    bbox: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def validate_production_contract(self) -> RelationshipCaptureParameters:
        """Reuse the production query invariants instead of maintaining looser copies."""
        bounds = GeoBounds(*self.bbox) if self.bbox is not None else None
        CorrelationQuery(
            radius_km=self.radius_km,
            time_window_minutes=self.time_window_minutes,
            lookback_hours=self.lookback_hours,
            edge_limit=self.candidate_edge_limit,
            bounds=bounds,
            active_only=self.active_only,
        )
        return self

    def api_parameters(self) -> dict[str, str | int | float]:
        """Render the exact HTTP query without implicit client defaults."""
        parameters: dict[str, str | int | float] = {
            "limit": self.incident_limit,
            "radius_km": self.radius_km,
            "time_window_minutes": self.time_window_minutes,
            "lookback_hours": self.lookback_hours,
            "candidate_edge_limit": self.candidate_edge_limit,
            "active_only": str(self.active_only).lower(),
        }
        if self.bbox is not None:
            parameters["bbox"] = ",".join(format(value, "g") for value in self.bbox)
        return parameters


class RelationshipBenchmarkDefinition(StrictModel):
    """Checked-in review scope independent of any one captured live snapshot."""

    schema_version: SchemaVersion = "1.0.0"
    benchmark_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$")
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=2_000)
    rubric_version: ReviewRubricVersion = "claim-pair-rubric-v1"
    expected_correlation_rule_version: str = Field(min_length=1, max_length=200)
    expected_relationship_rule_version: str = Field(min_length=1, max_length=200)
    predicates: tuple[ClaimPredicate, ...] = Field(min_length=1, max_length=3)
    max_edges_per_source_pair: int = Field(default=10, ge=1, le=100)
    parameters: RelationshipCaptureParameters = Field(default_factory=RelationshipCaptureParameters)

    @field_validator("predicates")
    @classmethod
    def validate_predicates(cls, value: tuple[ClaimPredicate, ...]) -> tuple[ClaimPredicate, ...]:
        if len(set(value)) != len(value):
            raise ValueError("benchmark predicates must be unique")
        return value


class EventEvidence(StrictModel):
    """Reviewer-visible event document with a content identity."""

    node_id: str = Field(min_length=3, max_length=1_000)
    source: SourceName
    event_id: str = Field(min_length=1, max_length=900)
    event_type: str = Field(min_length=1, max_length=500)
    occurred_at: datetime
    document_text: str = Field(min_length=1, max_length=8_000)
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> EventEvidence:
        if self.node_id != f"{self.source}:{self.event_id}":
            raise ValueError("evidence node_id must equal source:event_id")
        expected = hashlib.sha256(self.document_text.encode()).hexdigest()
        if self.document_hash != expected:
            raise ValueError("document_hash must match the reviewer-visible document_text")
        if self.occurred_at.tzinfo is None:
            raise ValueError("evidence occurred_at must be timezone-aware")
        return self


class CapturedClaim(StrictModel):
    """Deployed extractor output retained outside the blinded review sheet."""

    claim_id: str = Field(min_length=1, max_length=200)
    node_id: str = Field(min_length=3, max_length=1_000)
    predicate: ClaimPredicate
    value: str = Field(min_length=1, max_length=500)
    scope: ClaimScope
    scope_value: str | None = Field(default=None, max_length=1_000)
    evidence_field: str = Field(min_length=1, max_length=500)
    evidence_excerpt: str = Field(min_length=1, max_length=280)
    qualifier: str | None = Field(default=None, max_length=1_000)


class SystemPrediction(StrictModel):
    """One deployed rule decision hidden from the human assessor."""

    label: RelationshipLabel
    relationship_ids: tuple[str, ...] = ()
    claims: tuple[CapturedClaim, ...] = ()
    bases: tuple[str, ...] = ()
    rationales: tuple[str, ...] = ()

    @field_validator("relationship_ids", "bases", "rationales")
    @classmethod
    def validate_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("system prediction collections must be unique")
        return value

    @model_validator(mode="after")
    def validate_decision(self) -> SystemPrediction:
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("system prediction claims must be unique")
        if self.label != "insufficient_evidence" and not self.relationship_ids:
            raise ValueError("decisive system predictions require a relationship ID")
        return self


def relationship_case_id(
    *,
    edge_id: str,
    predicate: ClaimPredicate,
    left: EventEvidence,
    right: EventEvidence,
    distance_km: float,
    time_delta_minutes: float,
    left_geometry_basis: GeometryBasis,
    right_geometry_basis: GeometryBasis,
) -> str:
    """Content-address one exact reviewer-visible pair without system output."""
    payload = {
        "edge_id": edge_id,
        "predicate": predicate,
        "left_document_hash": left.document_hash,
        "right_document_hash": right.document_hash,
        "distance_km": distance_km,
        "time_delta_minutes": time_delta_minutes,
        "left_geometry_basis": left_geometry_basis,
        "right_geometry_basis": right_geometry_basis,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"pair-{hashlib.sha256(encoded).hexdigest()[:20]}"


class RelationshipCase(StrictModel):
    """One predicate-scoped cross-source pair with hidden prediction and human gold label."""

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
    system_prediction: SystemPrediction
    gold_label: RelationshipLabel | None = None
    rationale: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_pair(self) -> RelationshipCase:
        if self.left.node_id >= self.right.node_id:
            raise ValueError("relationship evidence must use canonical node order")
        if self.left.source == self.right.source:
            raise ValueError("relationship evidence endpoints must use independent sources")
        expected_sources = tuple(sorted((self.left.source, self.right.source)))
        if self.source_pair != expected_sources:
            raise ValueError("source_pair must be the sorted endpoint sources")
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
            raise ValueError("case_id must match the exact reviewer-visible evidence pair")
        if any(claim.predicate != self.predicate for claim in self.system_prediction.claims):
            raise ValueError("system prediction claims must match the case predicate")
        endpoint_ids = {self.left.node_id, self.right.node_id}
        if any(claim.node_id not in endpoint_ids for claim in self.system_prediction.claims):
            raise ValueError("system prediction claims must belong to the evidence endpoints")
        return self

    def review_identity(self) -> dict[str, object]:
        """Return every protected reviewer-visible field without system or human labels."""
        return self.model_dump(
            mode="python",
            exclude={"system_prediction", "gold_label", "rationale"},
        )


class IndependentAdjudicationProvenance(StrictModel):
    """Auditable human-review process attached only to a finalized gold pool."""

    process_version: ReviewProcessVersion = "independent-review-adjudication-v1"
    independent_reviewers: tuple[str, str]
    review_pool_sha256s: tuple[str, str]
    agreement_report_id: str = Field(min_length=1, max_length=240)
    observed_agreement: float = Field(ge=0, le=1, allow_inf_nan=False)
    cohen_kappa: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    adjudicator: str = Field(min_length=1, max_length=200)
    adjudicated_at: datetime
    adjudication_decision_count: int = Field(ge=0)

    @field_validator("independent_reviewers")
    @classmethod
    def validate_reviewers(cls, value: tuple[str, str]) -> tuple[str, str]:
        normalized = tuple(" ".join(reviewer.split()) for reviewer in value)
        if any(not reviewer for reviewer in normalized):
            raise ValueError("independent reviewers must not be blank")
        if any(len(reviewer) > 200 for reviewer in normalized):
            raise ValueError("independent reviewer names cannot exceed 200 characters")
        if normalized[0].casefold() == normalized[1].casefold():
            raise ValueError("independent reviewers must be different people")
        if normalized != tuple(sorted(normalized, key=lambda item: (item.casefold(), item))):
            raise ValueError("independent reviewers must use canonical name order")
        return normalized[0], normalized[1]

    @field_validator("review_pool_sha256s")
    @classmethod
    def validate_review_hashes(cls, value: tuple[str, str]) -> tuple[str, str]:
        if any(
            len(item) != 64 or any(char not in "0123456789abcdef" for char in item)
            for item in value
        ):
            raise ValueError("independent review pool hashes must be lowercase SHA-256 values")
        return value

    @field_validator("adjudicator")
    @classmethod
    def normalize_adjudicator(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("adjudicator must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_process(self) -> IndependentAdjudicationProvenance:
        if self.adjudicator.casefold() in {
            reviewer.casefold() for reviewer in self.independent_reviewers
        }:
            raise ValueError("adjudicator must be independent from both reviewers")
        if self.adjudicated_at.tzinfo is None:
            raise ValueError("adjudicated_at must be timezone-aware")
        return self


class RelationshipPool(StrictModel):
    """Portable live capture completed only through strict judgment import."""

    schema_version: RelationshipArtifactSchemaVersion = "1.0.0"
    pool_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,159}$")
    benchmark_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$")
    benchmark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rubric_version: ReviewRubricVersion = "claim-pair-rubric-v1"
    captured_at: datetime
    endpoint: str = Field(min_length=1, max_length=2_000)
    capture_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    parameters: RelationshipCaptureParameters
    predicates: tuple[ClaimPredicate, ...] = Field(min_length=1, max_length=3)
    max_edges_per_source_pair: int = Field(ge=1, le=100)
    sampling_method: SamplingMethod = "source-pair-stable-hash-v1"
    correlation_rule_version: str = Field(min_length=1, max_length=200)
    relationship_rule_version: str = Field(min_length=1, max_length=200)
    incident_count: int = Field(ge=0)
    total_incidents: int = Field(ge=0)
    available_edge_count: int = Field(ge=1)
    sampled_edge_count: int = Field(ge=1)
    available_edges_by_source_pair: dict[str, int]
    sampled_edges_by_source_pair: dict[str, int]
    incidents_truncated: bool
    candidate_edges_truncated: bool
    judgment_status: JudgmentStatus = "unjudged"
    reviewer: str | None = Field(default=None, max_length=200)
    reviewed_at: datetime | None = None
    adjudication: IndependentAdjudicationProvenance | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    cases: tuple[RelationshipCase, ...] = Field(min_length=1)

    @field_validator("predicates")
    @classmethod
    def validate_unique_predicates(
        cls, value: tuple[ClaimPredicate, ...]
    ) -> tuple[ClaimPredicate, ...]:
        if len(set(value)) != len(value):
            raise ValueError("pool predicates must be unique")
        return value

    @field_validator("available_edges_by_source_pair", "sampled_edges_by_source_pair")
    @classmethod
    def validate_source_pair_counts(cls, value: dict[str, int]) -> dict[str, int]:
        sources = {"usgs", "nws", "firms", "gdelt"}
        for pair, count in value.items():
            parts = pair.split("+")
            if len(parts) != 2 or parts != sorted(parts) or len(set(parts)) != 2:
                raise ValueError("source-pair count keys must contain two sorted sources")
            if any(source not in sources for source in parts):
                raise ValueError("source-pair count keys contain an unknown source")
            if count < 0:
                raise ValueError("source-pair counts must be non-negative")
        return value

    @field_validator("reviewer")
    @classmethod
    def normalize_reviewer(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("reviewer must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_pool(self) -> RelationshipPool:
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        if self.reviewed_at is not None and self.reviewed_at.tzinfo is None:
            raise ValueError("reviewed_at must be timezone-aware")
        if self.total_incidents < self.incident_count:
            raise ValueError("total_incidents cannot be smaller than incident_count")
        if sum(self.available_edges_by_source_pair.values()) != self.available_edge_count:
            raise ValueError("available source-pair counts must sum to available_edge_count")
        if sum(self.sampled_edges_by_source_pair.values()) != self.sampled_edge_count:
            raise ValueError("sampled source-pair counts must sum to sampled_edge_count")
        for pair, sampled in self.sampled_edges_by_source_pair.items():
            if sampled > self.available_edges_by_source_pair.get(pair, 0):
                raise ValueError("sampled source-pair counts cannot exceed available counts")
            if sampled > self.max_edges_per_source_pair:
                raise ValueError("sampled source-pair count exceeds the configured cap")

        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("relationship case IDs must be unique")
        edge_predicates = [(case.edge_id, case.predicate) for case in self.cases]
        if len(set(edge_predicates)) != len(edge_predicates):
            raise ValueError("each sampled edge may appear once per predicate")
        edge_ids = {case.edge_id for case in self.cases}
        if len(edge_ids) != self.sampled_edge_count:
            raise ValueError("sampled_edge_count must match distinct case edges")
        sampled_edge_sets: dict[str, set[str]] = {}
        for case in self.cases:
            pair = "+".join(case.source_pair)
            sampled_edge_sets.setdefault(pair, set()).add(case.edge_id)
        actual_sampled_counts = {
            pair: len(edges) for pair, edges in sorted(sampled_edge_sets.items())
        }
        if actual_sampled_counts != self.sampled_edges_by_source_pair:
            raise ValueError("sampled source-pair counts must match captured cases")
        predicates_by_edge = {
            edge_id: {case.predicate for case in self.cases if case.edge_id == edge_id}
            for edge_id in edge_ids
        }
        expected_predicates = set(self.predicates)
        if any(values != expected_predicates for values in predicates_by_edge.values()):
            raise ValueError("every sampled edge must contain every benchmark predicate")

        if self.judgment_status == "reviewed":
            if not self.reviewer or self.reviewed_at is None:
                raise ValueError("reviewed pools require reviewer and reviewed_at")
            if any(case.gold_label is None for case in self.cases):
                raise ValueError("reviewed pools require a gold label for every case")
        elif self.reviewer is not None or self.reviewed_at is not None:
            raise ValueError("unjudged pools must not claim reviewer metadata")
        elif any(case.gold_label is not None or case.rationale is not None for case in self.cases):
            raise ValueError("unjudged pools must not contain partial judgments")

        if self.adjudication is not None:
            if self.schema_version != "1.1.0":
                raise ValueError("adjudicated pools require relationship artifact schema 1.1.0")
            if self.judgment_status != "reviewed":
                raise ValueError("only reviewed pools may contain adjudication provenance")
            if self.reviewer != self.adjudication.adjudicator:
                raise ValueError("pool reviewer must equal the adjudication finalizer")
            if self.reviewed_at != self.adjudication.adjudicated_at:
                raise ValueError("pool reviewed_at must equal adjudicated_at")
        elif self.schema_version != "1.0.0":
            raise ValueError("relationship artifact schema 1.1.0 requires adjudication provenance")
        return self


class LabelMetrics(StrictModel):
    """One-vs-rest measurements for a relationship label."""

    support: int = Field(ge=0)
    predicted_count: int = Field(ge=0)
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    precision: float | None = Field(default=None, ge=0, le=1)
    recall: float | None = Field(default=None, ge=0, le=1)
    f1: float | None = Field(default=None, ge=0, le=1)


class RelationshipSliceMetrics(StrictModel):
    """Classification and abstention measurements for one non-empty slice."""

    case_count: int = Field(ge=1)
    accuracy: float = Field(ge=0, le=1)
    macro_f1: float | None = Field(default=None, ge=0, le=1)
    decisive_coverage: float = Field(ge=0, le=1)
    abstention_rate: float = Field(ge=0, le=1)
    selective_accuracy: float | None = Field(default=None, ge=0, le=1)
    labels: dict[RelationshipLabel, LabelMetrics]


class RelationshipCaseOutcome(StrictModel):
    """Compact per-case result retained for inspectable error analysis."""

    case_id: str
    edge_id: str
    predicate: ClaimPredicate
    source_pair: tuple[SourceName, SourceName]
    predicted_label: RelationshipLabel
    gold_label: RelationshipLabel
    correct: bool


class RelationshipEvaluationReport(StrictModel):
    """Content-addressed evaluation of one deployed relationship rule."""

    schema_version: RelationshipArtifactSchemaVersion = "1.0.0"
    report_id: str
    pool_id: str
    pool_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    reviewer: str
    rubric_version: ReviewRubricVersion
    correlation_rule_version: str
    relationship_rule_version: str
    incident_count: int = Field(ge=0)
    available_edge_count: int = Field(ge=1)
    sampled_edge_count: int = Field(ge=1)
    incidents_truncated: bool
    candidate_edges_truncated: bool
    overall: RelationshipSliceMetrics
    predicates: dict[ClaimPredicate, RelationshipSliceMetrics]
    source_pairs: dict[str, RelationshipSliceMetrics]
    confusion_matrix: dict[RelationshipLabel, dict[RelationshipLabel, int]]
    outcomes: tuple[RelationshipCaseOutcome, ...]
    adjudication: IndependentAdjudicationProvenance | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    caveats: tuple[str, ...]

    @model_validator(mode="after")
    def validate_review_provenance(self) -> RelationshipEvaluationReport:
        if (self.schema_version == "1.1.0") != (self.adjudication is not None):
            raise ValueError("report schema 1.1.0 and adjudication provenance must appear together")
        if self.adjudication is not None and self.reviewer != self.adjudication.adjudicator:
            raise ValueError("report reviewer must equal the adjudication finalizer")
        return self

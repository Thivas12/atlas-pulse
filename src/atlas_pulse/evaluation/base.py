"""Strict contracts for pooled, human-reviewed retrieval evaluation."""

import hashlib
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atlas_pulse.projections import GeoBounds
from atlas_pulse.projections.base import SourceName
from atlas_pulse.retrieval import CitationStatus, GeoRadius, RankingMode, SearchQuery

SchemaVersion = Literal["1.0.0"]
CandidatePoolSchemaVersion = Literal["1.0.0", "1.1.0"]
ReportSchemaVersion = Literal["1.1.0", "1.2.0"]
JudgmentStatus = Literal["unjudged", "reviewed"]
ReviewProcessVersion = Literal["independent-review-adjudication-v1"]
CutoffMetricName = Literal[
    "precision",
    "pooled_recall",
    "reciprocal_rank",
    "ndcg",
    "hit_rate",
    "judged_rate",
    "citation_traceability",
]
GateMetricName = CutoffMetricName | Literal["candidate_coverage", "latency_p95_ms"]
RelevanceGrade = Annotated[int, Field(ge=0, le=3)]


class StrictModel(BaseModel):
    """Forbid silent schema drift in checked-in or reviewer-edited artifacts."""

    model_config = ConfigDict(extra="forbid")


class EvaluationFilters(StrictModel):
    """The production search filters attached to one evaluation query."""

    source: SourceName | None = None
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None
    active_only: bool = True
    bbox: tuple[float, float, float, float] | None = None
    near: tuple[float, float] | None = None
    radius_km: float = Field(default=250.0, gt=0, le=2_000)

    @model_validator(mode="after")
    def validate_search_filters(self) -> "EvaluationFilters":
        """Delegate spatial and temporal invariants to the production contracts."""
        bounds = GeoBounds(*self.bbox) if self.bbox is not None else None
        near = (
            GeoRadius(longitude=self.near[0], latitude=self.near[1], radius_km=self.radius_km)
            if self.near is not None
            else None
        )
        SearchQuery(
            text="validation query",
            source=self.source,
            occurred_after=self.occurred_after,
            occurred_before=self.occurred_before,
            active_only=self.active_only,
            bounds=bounds,
            near=near,
        )
        return self

    def search_query(
        self,
        *,
        text: str,
        limit: int,
        candidate_limit: int,
        ranking_mode: RankingMode,
    ) -> SearchQuery:
        """Create the exact production query represented by this evaluation case."""
        return SearchQuery(
            text=text,
            limit=limit,
            candidate_limit=candidate_limit,
            source=self.source,
            occurred_after=self.occurred_after,
            occurred_before=self.occurred_before,
            active_only=self.active_only,
            bounds=GeoBounds(*self.bbox) if self.bbox is not None else None,
            near=(
                GeoRadius(
                    longitude=self.near[0],
                    latitude=self.near[1],
                    radius_km=self.radius_km,
                )
                if self.near is not None
                else None
            ),
            ranking_mode=ranking_mode,
        )


class EvaluationQuery(StrictModel):
    """One operator-shaped information need, independent of a retrieval method."""

    query_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    text: str = Field(min_length=2, max_length=500)
    slices: tuple[str, ...] = Field(min_length=1)
    filters: EvaluationFilters = Field(default_factory=EvaluationFilters)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("query text must contain at least two non-whitespace characters")
        return normalized

    @field_validator("slices")
    @classmethod
    def normalize_slices(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().casefold() for item in value)
        if any(not item for item in normalized):
            raise ValueError("evaluation slices must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("evaluation slices must be unique")
        return normalized


class EvaluationQuerySet(StrictModel):
    """Versioned query definitions used to construct a pooled judgment task."""

    schema_version: SchemaVersion = "1.0.0"
    query_set_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{2,79}$")
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=2_000)
    pool_depth: int = Field(default=10, ge=1, le=50)
    modes: tuple[RankingMode, ...] = Field(
        default=("lexical", "dense", "rrf", "hybrid"),
        min_length=1,
    )
    queries: tuple[EvaluationQuery, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_identifiers(self) -> "EvaluationQuerySet":
        query_ids = [query.query_id for query in self.queries]
        if len(set(query_ids)) != len(query_ids):
            raise ValueError("query IDs must be unique")
        if len(set(self.modes)) != len(self.modes):
            raise ValueError("retrieval modes must be unique")
        return self


class PooledCandidate(StrictModel):
    """A blinded candidate to grade on a four-level relevance scale."""

    document_id: str = Field(pattern=r"^[a-z0-9_-]+:.+$")
    source: SourceName
    event_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=1_000)
    occurred_at: datetime
    document_text: str = Field(min_length=1, max_length=8_000)
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    citation_status: CitationStatus
    citation_url: str | None = None
    relevance: RelevanceGrade | None = None
    rationale: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_identity(self) -> "PooledCandidate":
        if self.document_id != f"{self.source}:{self.event_id}":
            raise ValueError("document_id must equal source:event_id")
        if self.document_hash != hashlib.sha256(self.document_text.encode()).hexdigest():
            raise ValueError("document_hash must match document_text")
        if self.citation_status == "traceable" and self.citation_url is None:
            raise ValueError("traceable candidates require citation_url")
        if self.citation_status != "traceable" and self.citation_url is not None:
            raise ValueError("untraceable candidates must not expose citation_url")
        return self

    def evidence_identity(self) -> dict[str, object]:
        """Return every reviewer-visible field without its judgment."""
        return self.model_dump(mode="python", exclude={"relevance", "rationale"})


class CapturedRun(StrictModel):
    """The immutable ordered output of one retrieval mode for one query."""

    mode: RankingMode
    ranking_rule: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    document_ids: tuple[str, ...] = Field(max_length=50)

    @field_validator("document_ids")
    @classmethod
    def validate_unique_documents(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("a captured run must not contain duplicate document IDs")
        return value


class PooledQuery(StrictModel):
    """One blinded judgment pool plus the system runs that formed it."""

    query: EvaluationQuery
    candidates: tuple[PooledCandidate, ...]
    runs: tuple[CapturedRun, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pool(self) -> "PooledQuery":
        candidate_ids = [candidate.document_id for candidate in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("pooled candidates must be unique")
        modes = [run.mode for run in self.runs]
        if len(set(modes)) != len(modes):
            raise ValueError("a pooled query must contain at most one run per mode")
        unknown = {
            document_id
            for run in self.runs
            for document_id in run.document_ids
            if document_id not in set(candidate_ids)
        }
        if unknown:
            raise ValueError(f"runs reference documents outside the pool: {sorted(unknown)}")
        return self


class IndependentRetrievalAdjudicationProvenance(StrictModel):
    """Auditable review process attached only to a finalized retrieval gold pool."""

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
            len(item) != 64 or any(character not in "0123456789abcdef" for character in item)
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
    def validate_process(self) -> "IndependentRetrievalAdjudicationProvenance":
        if self.adjudicator.casefold() in {
            reviewer.casefold() for reviewer in self.independent_reviewers
        }:
            raise ValueError("adjudicator must be independent from both reviewers")
        if self.adjudicated_at.tzinfo is None:
            raise ValueError("adjudicated_at must be timezone-aware")
        return self


class CandidatePool(StrictModel):
    """Portable capture artifact completed through strict judgment import."""

    schema_version: CandidatePoolSchemaVersion = "1.0.0"
    pool_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$")
    query_set_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{2,79}$")
    query_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_at: datetime
    endpoint: str = Field(min_length=1, max_length=2_000)
    judgment_status: JudgmentStatus = "unjudged"
    reviewer: str | None = Field(default=None, max_length=200)
    reviewed_at: datetime | None = None
    adjudication: IndependentRetrievalAdjudicationProvenance | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    queries: tuple[PooledQuery, ...] = Field(min_length=1)

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
    def validate_review_state(self) -> "CandidatePool":
        query_ids = [item.query.query_id for item in self.queries]
        if len(set(query_ids)) != len(query_ids):
            raise ValueError("pooled query IDs must be unique")
        expected_modes = {run.mode for run in self.queries[0].runs}
        if any({run.mode for run in query.runs} != expected_modes for query in self.queries):
            raise ValueError("every pooled query must contain the same retrieval modes")
        embedding_models = {run.embedding_model for query in self.queries for run in query.runs}
        if len(embedding_models) != 1:
            raise ValueError("a candidate pool must contain exactly one embedding model")
        rules_by_mode: dict[RankingMode, set[str]] = {}
        for query in self.queries:
            for run in query.runs:
                rules_by_mode.setdefault(run.mode, set()).add(run.ranking_rule)
        if any(len(rules) != 1 for rules in rules_by_mode.values()):
            raise ValueError("a ranking mode must use one rule throughout a candidate pool")
        candidates = [candidate for query in self.queries for candidate in query.candidates]
        if self.judgment_status == "reviewed":
            if not self.reviewer or self.reviewed_at is None:
                raise ValueError("reviewed pools require reviewer and reviewed_at")
            if any(candidate.relevance is None for candidate in candidates):
                raise ValueError("reviewed pools require a relevance grade for every candidate")
        elif self.reviewer is not None or self.reviewed_at is not None:
            raise ValueError("unjudged pools must not claim reviewer metadata")
        elif any(
            candidate.relevance is not None or candidate.rationale is not None
            for candidate in candidates
        ):
            raise ValueError("unjudged pools must not contain partial judgments")
        if self.adjudication is not None:
            if self.schema_version != "1.1.0":
                raise ValueError("adjudicated pools require candidate-pool schema 1.1.0")
            if self.judgment_status != "reviewed":
                raise ValueError("only reviewed pools may contain adjudication provenance")
            if self.reviewer != self.adjudication.adjudicator:
                raise ValueError("pool reviewer must equal the adjudication finalizer")
            if self.reviewed_at != self.adjudication.adjudicated_at:
                raise ValueError("pool reviewed_at must equal adjudicated_at")
        elif self.schema_version != "1.0.0":
            raise ValueError("candidate-pool schema 1.1.0 requires adjudication provenance")
        return self


class CutoffMetrics(StrictModel):
    """Standard pooled-retrieval metrics at one cutoff."""

    precision: float = Field(ge=0, le=1)
    pooled_recall: float = Field(ge=0, le=1)
    reciprocal_rank: float = Field(ge=0, le=1)
    ndcg: float = Field(ge=0, le=1)
    hit_rate: float = Field(ge=0, le=1)
    judged_rate: float = Field(ge=0, le=1)
    citation_traceability: float = Field(ge=0, le=1)


class QueryRunMetrics(StrictModel):
    """Per-query measurements retained for error analysis."""

    query_id: str
    slices: tuple[str, ...]
    mode: RankingMode
    ranking_rule: str
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    result_count: int = Field(ge=0, le=50)
    cutoffs: dict[int, CutoffMetrics]


class AggregateMetrics(StrictModel):
    """Macro averages and observed latency for one mode or slice."""

    query_count: int = Field(ge=1)
    candidate_coverage: float = Field(ge=0, le=1)
    empty_query_ids: tuple[str, ...] = ()
    latency_p50_ms: float = Field(ge=0, allow_inf_nan=False)
    latency_p95_ms: float = Field(ge=0, allow_inf_nan=False)
    cutoffs: dict[int, CutoffMetrics]

    @model_validator(mode="after")
    def validate_candidate_coverage(self) -> "AggregateMetrics":
        if tuple(sorted(set(self.empty_query_ids))) != self.empty_query_ids:
            raise ValueError("empty query IDs must be unique and sorted")
        if len(self.empty_query_ids) > self.query_count:
            raise ValueError("empty query count cannot exceed query_count")
        expected = (self.query_count - len(self.empty_query_ids)) / self.query_count
        if abs(self.candidate_coverage - expected) > 1e-12:
            raise ValueError("candidate_coverage must match empty_query_ids")
        return self


class GateOutcome(StrictModel):
    """Inspectable result for one quality gate."""

    rule_id: str
    mode: RankingMode
    slice_name: str | None
    metric: GateMetricName
    cutoff: int | None
    passed: bool
    observed: float
    comparison: str


class EvaluationReport(StrictModel):
    """Machine-readable result tied to a content-addressed reviewed pool."""

    schema_version: ReportSchemaVersion = "1.1.0"
    report_id: str
    pool_id: str
    pool_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    reviewer: str
    embedding_models: tuple[str, ...]
    query_metrics: tuple[QueryRunMetrics, ...]
    modes: dict[RankingMode, AggregateMetrics]
    slices: dict[str, dict[RankingMode, AggregateMetrics]]
    gate_policy_id: str | None = None
    gate_outcomes: tuple[GateOutcome, ...] = ()
    adjudication: IndependentRetrievalAdjudicationProvenance | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    caveats: tuple[str, ...]

    @model_validator(mode="after")
    def validate_review_provenance(self) -> "EvaluationReport":
        if (self.schema_version == "1.2.0") != (self.adjudication is not None):
            raise ValueError("report schema 1.2.0 and adjudication provenance must appear together")
        if self.adjudication is not None and self.reviewer != self.adjudication.adjudicator:
            raise ValueError("report reviewer must equal the adjudication finalizer")
        return self


class GateRule(StrictModel):
    """One explicit minimum quality or maximum latency condition."""

    rule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    mode: RankingMode
    slice_name: str | None = Field(default=None, min_length=1, max_length=100)
    metric: GateMetricName
    cutoff: int | None = Field(default=None, ge=1, le=50)
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)

    @field_validator("slice_name")
    @classmethod
    def normalize_slice_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split()).casefold()
        if not normalized:
            raise ValueError("gate slice_name must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_threshold(self) -> "GateRule":
        if (self.minimum is None) == (self.maximum is None):
            raise ValueError("a gate rule requires exactly one of minimum or maximum")
        aggregate_metrics = {"candidate_coverage", "latency_p95_ms"}
        if self.metric in aggregate_metrics and self.cutoff is not None:
            raise ValueError("aggregate gates must not define a cutoff")
        if self.metric not in aggregate_metrics and self.cutoff is None:
            raise ValueError("retrieval metric gates require a cutoff")
        threshold = self.minimum if self.minimum is not None else self.maximum
        assert threshold is not None
        if self.metric == "latency_p95_ms" and threshold < 0:
            raise ValueError("latency thresholds must be non-negative")
        if self.metric != "latency_p95_ms" and not 0 <= threshold <= 1:
            raise ValueError("retrieval metric thresholds must be within [0, 1]")
        return self


class GatePolicy(StrictModel):
    """Versioned deployment policy evaluated against a report."""

    schema_version: SchemaVersion = "1.0.0"
    policy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,119}$")
    rules: tuple[GateRule, ...] = Field(min_length=1)

    @field_validator("rules")
    @classmethod
    def validate_unique_rules(cls, value: tuple[GateRule, ...]) -> tuple[GateRule, ...]:
        rule_ids = [rule.rule_id for rule in value]
        if len(set(rule_ids)) != len(rule_ids):
            raise ValueError("gate rule IDs must be unique")
        return value

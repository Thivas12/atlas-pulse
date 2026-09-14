"""FastAPI read surface for immutable history and projected current signals."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Self

from agent_rag_core import Event
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from valkey.exceptions import ValkeyError

from atlas_pulse import __version__
from atlas_pulse.agent_governance import AgentRunLedgerReader
from atlas_pulse.agent_runs import (
    AGENT_AUTHORIZATION_POLICY_VERSION,
    AGENT_RUN_CAVEAT,
    AGENT_RUN_IDENTITY_ALGORITHM,
    AGENT_RUN_RULE_VERSION,
    AGENT_RUN_SCHEMA_VERSION,
    AgentApprovalObservation,
    AgentApprovalStatus,
    AgentReleaseObservation,
    AgentReleaseStatus,
    AgentRunManifest,
    AgentRunStatus,
    AuthorizationBlockReason,
    AuthorizationCheckId,
    AuthorizationCheckStatus,
    agent_release_observation,
    build_agent_run_manifest,
    build_agent_run_proposal,
)
from atlas_pulse.correlation import (
    CORRELATION_CAVEAT,
    CORRELATION_RULE_VERSION,
    IncidentCandidate,
    build_incident_candidates,
)
from atlas_pulse.evidence_packs import (
    EVIDENCE_PACK_CAVEAT,
    EVIDENCE_PACK_IDENTITY_ALGORITHM,
    EVIDENCE_PACK_RULE_VERSION,
    EVIDENCE_PACK_SCHEMA_VERSION,
    EVIDENCE_PACK_TRUST_BOUNDARY,
    EvidenceExclusionReason,
    EvidencePack,
    EvidencePackBudget,
    EvidencePackStatus,
    build_evidence_pack,
)
from atlas_pulse.projections import CorrelationQuery, GeoBounds, SignalQuery, SignalStore
from atlas_pulse.projections.base import SourceName
from atlas_pulse.relationships import (
    RELATIONSHIP_CAVEAT,
    RELATIONSHIP_RULE_VERSION,
    EvidenceClaim,
    EvidenceRelationship,
    RelationshipAnalysis,
    analyze_incident,
)
from atlas_pulse.retrieval import (
    CitationValidation,
    GeoRadius,
    RankingExplanation,
    RankingMode,
    SearchQuery,
    SearchResult,
    SearchService,
)
from atlas_pulse.source_poll_store import SourcePollStore
from atlas_pulse.source_polling import (
    SourceFreshnessResponse,
    SourcePollHistoryResponse,
    SourcePollPolicy,
    evaluate_source_freshness,
)
from atlas_pulse.streams.base import EventBus

if TYPE_CHECKING:
    from atlas_pulse.agent_trajectory.release import AgentReleaseAssessment


class HealthResponse(BaseModel):
    """Liveness or readiness response."""

    status: str
    version: str
    commit_sha: str


class EventEnvelope(BaseModel):
    """Normalized event paired with its replay position."""

    stream_id: str
    event: Event


class EventsResponse(BaseModel):
    """Bounded newest-first event page."""

    count: int
    items: tuple[EventEnvelope, ...]


class ReplayResponse(BaseModel):
    """Deterministic oldest-first page with an exclusive continuation cursor."""

    count: int
    items: tuple[EventEnvelope, ...]
    next_cursor: str | None
    has_more: bool
    order: Literal["oldest_first"] = "oldest_first"


class SignalsResponse(BaseModel):
    """Keyset-paginated newest-first current-state page."""

    count: int
    items: tuple[EventEnvelope, ...]
    next_cursor: str | None
    has_more: bool
    order: Literal["newest_revision_first"] = "newest_revision_first"


class IncidentCenterResponse(BaseModel):
    """Spherical mean of the participating source focus points."""

    latitude: float
    longitude: float


class EvidenceNodeResponse(BaseModel):
    """One current signal retained verbatim in an evidence graph."""

    node_id: str
    stream_id: str
    event: Event


class EvidenceEdgeResponse(BaseModel):
    """One measured, non-causal cross-source relationship."""

    edge_id: str
    from_node_id: str
    to_node_id: str
    relation: Literal["spatiotemporal_cooccurrence"]
    spatial_relation: Literal["intersects", "within_radius"]
    distance_km: float
    time_delta_minutes: float
    from_geometry_basis: Literal["point", "polygon"]
    to_geometry_basis: Literal["point", "polygon"]
    rule_version: str


class EvidenceClaimResponse(BaseModel):
    """One normalized claim with exact field-level source provenance."""

    claim_id: str
    node_id: str
    predicate: Literal["hazard_domain", "evacuation_state", "road_access_state"]
    value: str
    scope: Literal["measured_edge_area", "named_place"]
    scope_value: str | None
    evidence_field: str
    evidence_excerpt: str
    qualifier: str | None
    rule_version: str


class EvidenceRelationshipResponse(BaseModel):
    """One versioned comparison between claims attached to a measured edge."""

    relationship_id: str
    edge_id: str
    from_node_id: str
    to_node_id: str
    label: Literal["corroborates", "contradicts", "insufficient_evidence"]
    predicate: Literal["hazard_domain", "evacuation_state", "road_access_state"] | None
    normalized_value: str | None
    from_claim_id: str | None
    to_claim_id: str | None
    basis: Literal[
        "exact_normalized_agreement",
        "mutually_exclusive_structured_values",
        "no_decisive_comparison",
    ]
    rationale: str
    rule_version: str


class RelationshipAnalysisResponse(BaseModel):
    """Conservative claim annotations that remain separate from graph measurements."""

    claims: tuple[EvidenceClaimResponse, ...]
    relationships: tuple[EvidenceRelationshipResponse, ...]
    analyzed_edge_count: int
    corroboration_count: int
    contradiction_count: int
    insufficient_evidence_count: int
    rule_version: str
    caveat: str


class IncidentCandidateResponse(BaseModel):
    """A reproducible graph component that remains explicitly unverified."""

    incident_id: str
    title: str
    started_at: datetime
    latest_signal_at: datetime
    center: IncidentCenterResponse | None
    sources: tuple[str, ...]
    node_count: int
    edge_count: int
    max_distance_km: float
    time_span_minutes: float
    nodes: tuple[EvidenceNodeResponse, ...]
    edges: tuple[EvidenceEdgeResponse, ...]
    relationship_analysis: RelationshipAnalysisResponse
    rule_version: str
    caveat: str


class CorrelationParametersResponse(BaseModel):
    """Exact bounded rule parameters used for this graph snapshot."""

    radius_km: float
    time_window_minutes: int
    lookback_hours: int
    candidate_edge_limit: int
    incident_limit: int
    active_only: bool
    bbox: tuple[float, float, float, float] | None


class IncidentsResponse(BaseModel):
    """Bounded deterministic cross-source incident candidates."""

    count: int
    total_incidents: int
    items: tuple[IncidentCandidateResponse, ...]
    incidents_truncated: bool
    candidate_edges_truncated: bool
    rule_version: str = CORRELATION_RULE_VERSION
    caveat: str = CORRELATION_CAVEAT
    relationship_rule_version: str = RELATIONSHIP_RULE_VERSION
    relationship_caveat: str = RELATIONSHIP_CAVEAT
    parameters: CorrelationParametersResponse


class CitationResponse(BaseModel):
    """Credential-safe source link with deliberately narrow validation semantics."""

    status: Literal["traceable", "missing", "rejected"]
    url: str | None
    source_field: str | None
    reasons: tuple[str, ...]


class TraceableCitationResponse(BaseModel):
    """Source link whose required traceability fields are present."""

    status: Literal["traceable"]
    url: str
    source_field: str
    reasons: tuple[str, ...]


class RankingResponse(BaseModel):
    """Inspectable hybrid retrieval and evidence tie-break contributions."""

    lexical_rank: int | None
    lexical_score: float | None
    dense_rank: int | None
    dense_similarity: float | None
    rrf_score: float
    exact_phrase_match: bool
    token_coverage: float
    rerank_score: float


class SearchHitResponse(BaseModel):
    """One current source event returned by the retrieval system."""

    stream_id: str
    event: Event
    document_text: str
    distance_km: float | None
    ranking: RankingResponse
    citation: CitationResponse


class SearchParametersResponse(BaseModel):
    """Exact query filters used to reproduce a search."""

    query: str
    limit: int
    candidate_limit: int
    source: SourceName | None
    occurred_after: datetime | None
    occurred_before: datetime | None
    active_only: bool
    bbox: tuple[float, float, float, float] | None
    near: tuple[float, float] | None
    radius_km: float | None
    ranking_mode: RankingMode


class SearchResponse(BaseModel):
    """Evidence-first hybrid retrieval response; never a generated answer."""

    count: int
    candidates_considered: int
    items: tuple[SearchHitResponse, ...]
    embedding_model: str
    ranking_mode: RankingMode
    ranking_rule: str
    caveat: str
    parameters: SearchParametersResponse


class EvidencePackBudgetResponse(BaseModel):
    """Hard, model-neutral source-text limits used for one pack."""

    max_items: int
    max_characters_per_item: int
    max_total_characters: int
    character_unit: Literal["unicode_code_points"] = "unicode_code_points"


class EvidencePackRetrievalResponse(BaseModel):
    """Exact deployed retrieval provenance retained by one pack."""

    candidates_considered: int
    returned_hits: int
    embedding_model: str
    ranking_mode: RankingMode
    ranking_rule: str
    caveat: str
    parameters: SearchParametersResponse


class EvidencePackItemResponse(BaseModel):
    """One structurally traceable source excerpt safe to hand to an agent as data."""

    evidence_id: str
    retrieval_rank: int
    stream_id: str
    event_id: str
    event_type: str
    source: str
    occurred_at: datetime
    ingested_at: datetime
    event_schema_version: str
    text: str
    document_sha256: str
    text_sha256: str
    document_characters: int
    text_characters: int
    truncated: bool
    distance_km: float | None
    ranking: RankingResponse
    citation: TraceableCitationResponse


class EvidencePackExclusionResponse(BaseModel):
    """One retrieved result withheld from the bounded agent handoff."""

    retrieval_rank: int
    stream_id: str
    event_id: str
    event_type: str
    source: str
    occurred_at: datetime
    ingested_at: datetime
    event_schema_version: str
    document_sha256: str
    distance_km: float | None
    ranking: RankingResponse
    citation: CitationResponse
    reason: EvidenceExclusionReason


class EvidencePackResponse(BaseModel):
    """Content-addressed evidence handoff with no generated answer."""

    pack_id: str
    schema_version: str = EVIDENCE_PACK_SCHEMA_VERSION
    rule_version: str = EVIDENCE_PACK_RULE_VERSION
    identity_algorithm: str = EVIDENCE_PACK_IDENTITY_ALGORITHM
    status: EvidencePackStatus
    item_count: int = Field(ge=0)
    exclusion_count: int = Field(ge=0)
    source_text_characters: int = Field(ge=0)
    budget: EvidencePackBudgetResponse
    retrieval: EvidencePackRetrievalResponse
    items: tuple[EvidencePackItemResponse, ...]
    exclusions: tuple[EvidencePackExclusionResponse, ...]
    answer_generated: Literal[False] = False
    trust_boundary: str = EVIDENCE_PACK_TRUST_BOUNDARY
    caveat: str = EVIDENCE_PACK_CAVEAT


class AgentRunCapabilitiesResponse(BaseModel):
    """Capabilities requested by the locked first specialist profile."""

    read_evidence: Literal[True]
    generate_text: Literal[True]
    network_access: Literal[False]
    tool_access: Literal[False]
    external_side_effects: Literal[False]


class AgentRunRequestResponse(BaseModel):
    """Proposed read-only run intent; no execution occurs during preflight."""

    purpose: Literal["evidence_triage"]
    mode: Literal["read_only"]
    requested_output: Literal["grounded_evidence_brief"]
    capabilities: AgentRunCapabilitiesResponse


class AgentRunEvidenceResponse(BaseModel):
    """Exact evidence-pack dependency bound into the manifest identity."""

    pack_id: str = Field(pattern=r"^pack-[0-9a-f]{64}$")
    pack_rule_version: str
    pack_status: EvidencePackStatus
    item_count: int = Field(ge=0)
    exclusion_count: int = Field(ge=0)
    source_text_characters: int = Field(ge=0)
    evidence_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_evidence_binding(self) -> Self:
        if self.item_count != len(self.evidence_ids):
            raise ValueError("evidence item_count does not match evidence IDs")
        if (self.item_count > 0) != (self.pack_status == "traceable_evidence_available"):
            raise ValueError("pack_status does not match evidence availability")
        if any(
            len(evidence_id) != 73
            or not evidence_id.startswith("evidence-")
            or any(character not in "0123456789abcdef" for character in evidence_id[9:])
            for evidence_id in self.evidence_ids
        ):
            raise ValueError("evidence IDs must use the evidence-<sha256> identity")
        return self


class AgentAuthorizationPolicyResponse(BaseModel):
    """Default-deny policy snapshot evaluated by preflight."""

    policy_version: str = AGENT_AUTHORIZATION_POLICY_VERSION
    default_decision: Literal["deny"]
    execution_enabled: Literal[False]
    human_release_required: Literal[True]
    evaluated_model_required: Literal[True]
    relationship_benchmark_required: Literal[True]
    grounded_answer_evaluation_required: Literal[True]
    agent_trajectory_evaluation_required: Literal[True]
    trajectory_drift_monitoring_required: Literal[True]
    release_threshold_policy_required: Literal[True]
    network_access_allowed: Literal[False]
    tool_access_allowed: Literal[False]
    external_side_effects_allowed: Literal[False]


class AgentApprovalObservationResponse(BaseModel):
    """Time-qualified approval state resolved from the signed ledger."""

    status: AgentApprovalStatus
    approval_id: str | None = Field(default=None, pattern=r"^approval-[0-9a-f]{64}$")
    approved_proposal_id: str | None = Field(default=None, pattern=r"^proposal-[0-9a-f]{64}$")
    source_manifest_id: str | None = Field(default=None, pattern=r"^manifest-[0-9a-f]{64}$")
    approver_id: str | None = None
    signing_key_id: str | None = Field(default=None, pattern=r"^ed25519-[0-9a-f]{64}$")
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    revocation_id: str | None = Field(default=None, pattern=r"^revocation-[0-9a-f]{64}$")
    evaluated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_approval_state(self) -> Self:
        if self.status == "not_supplied":
            if any(
                value is not None
                for value in (
                    self.approval_id,
                    self.approved_proposal_id,
                    self.source_manifest_id,
                    self.approver_id,
                    self.signing_key_id,
                    self.issued_at,
                    self.expires_at,
                    self.revocation_id,
                    self.evaluated_at,
                )
            ):
                raise ValueError("not_supplied approval state cannot contain ledger fields")
            return self
        if self.approval_id is None or self.evaluated_at is None:
            raise ValueError("resolved approval states require approval_id and evaluated_at")
        detailed = self.status in {
            "not_yet_valid",
            "active",
            "expired",
            "revoked",
            "scope_mismatch",
        }
        details = (
            self.approved_proposal_id,
            self.source_manifest_id,
            self.approver_id,
            self.signing_key_id,
            self.issued_at,
            self.expires_at,
        )
        if detailed and any(value is None for value in details):
            raise ValueError("resolved approval artifact details are incomplete")
        if self.status == "revoked" and self.revocation_id is None:
            raise ValueError("revoked approval state requires revocation_id")
        if self.status != "revoked" and self.revocation_id is not None:
            raise ValueError("only revoked approval state may include revocation_id")
        return self


class AgentReleaseObservationResponse(BaseModel):
    """Content-addressed quality evidence selected for the proposal."""

    status: AgentReleaseStatus
    assessment_id: str | None = Field(default=None, pattern=r"^release-assessment-[0-9a-f]{20}$")
    assessment_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    policy_id: str | None = Field(default=None, pattern=r"^release-policy-[0-9a-f]{20}$")
    policy_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    agent_candidate_id: str | None = None
    relationship_report_id: str | None = None
    trajectory_report_ids: tuple[str, ...]
    model_adapter_evaluated: bool
    relationship_benchmark_passed: bool
    grounded_answer_evaluation_passed: bool
    agent_trajectory_evaluation_passed: bool
    trajectory_drift_monitoring_passed: bool
    release_threshold_policy_passed: bool
    blocking_reasons: tuple[str, ...]

    @model_validator(mode="after")
    def validate_release_state(self) -> Self:
        identity = (
            self.assessment_id,
            self.assessment_sha256,
            self.policy_id,
            self.policy_sha256,
            self.agent_candidate_id,
            self.relationship_report_id,
        )
        flags = (
            self.model_adapter_evaluated,
            self.relationship_benchmark_passed,
            self.grounded_answer_evaluation_passed,
            self.agent_trajectory_evaluation_passed,
            self.trajectory_drift_monitoring_passed,
            self.release_threshold_policy_passed,
        )
        if self.status == "not_supplied":
            if (
                any(value is not None for value in identity)
                or self.trajectory_report_ids
                or any(flags)
                or self.blocking_reasons
            ):
                raise ValueError("not_supplied release state cannot contain assessment fields")
        elif any(value is None for value in identity) or not self.trajectory_report_ids:
            raise ValueError("resolved release state requires complete assessment identity")
        elif self.status == "eligible_for_human_review" and (
            not all(flags) or self.blocking_reasons
        ):
            raise ValueError("eligible release state requires every quality gate to pass")
        elif self.status == "blocked" and (
            self.release_threshold_policy_passed or not self.blocking_reasons
        ):
            raise ValueError("blocked release state requires threshold blocking reasons")
        return self


class AuthorizationCheckResponse(BaseModel):
    """One machine-readable comparison contributing to authorization."""

    check_id: AuthorizationCheckId
    status: AuthorizationCheckStatus
    observed: str
    required: str
    blocking_reason: AuthorizationBlockReason | None


class AgentAuthorizationResponse(BaseModel):
    """Complete fail-closed authorization result."""

    decision: AgentRunStatus
    passed_check_count: int = Field(ge=0)
    blocked_check_count: int = Field(ge=1)
    blocking_reasons: tuple[AuthorizationBlockReason, ...] = Field(min_length=1)
    checks: tuple[AuthorizationCheckResponse, ...]

    @model_validator(mode="after")
    def validate_authorization_checks(self) -> Self:
        expected_check_ids = {
            "evidence_pack_integrity",
            "traceable_evidence",
            "capability_scope",
            "model_adapter",
            "live_relationship_benchmark",
            "grounded_answer_evaluation",
            "agent_trajectory_evaluation",
            "trajectory_drift_monitoring",
            "release_threshold_policy",
            "human_release",
            "execution_release",
        }
        observed_check_ids = {check.check_id for check in self.checks}
        if observed_check_ids != expected_check_ids or len(self.checks) != len(expected_check_ids):
            raise ValueError("authorization checks must contain each v3 check exactly once")
        passed = tuple(check for check in self.checks if check.status == "passed")
        blocked = tuple(check for check in self.checks if check.status == "blocked")
        if self.passed_check_count != len(passed):
            raise ValueError("passed_check_count does not match checks")
        if self.blocked_check_count != len(blocked):
            raise ValueError("blocked_check_count does not match checks")
        if any(check.blocking_reason is not None for check in passed):
            raise ValueError("passed checks cannot have a blocking reason")
        if any(check.blocking_reason is None for check in blocked):
            raise ValueError("blocked checks require a blocking reason")
        expected_reasons = tuple(
            check.blocking_reason for check in blocked if check.blocking_reason is not None
        )
        if self.blocking_reasons != expected_reasons:
            raise ValueError("blocking_reasons do not match blocked checks")
        return self


class AgentExecutionStateResponse(BaseModel):
    """Explicit proof that preflight did not execute anything."""

    status: Literal["not_started"]
    agent_model_invoked: Literal[False]
    agent_network_accessed: Literal[False]
    agent_tools_invoked: Literal[False]
    answer_generated: Literal[False]
    agent_side_effects_performed: Literal[False]


class AgentRunManifestResponse(BaseModel):
    """Content-addressed proposed-run manifest under the locked v3 policy."""

    manifest_id: str = Field(pattern=r"^manifest-[0-9a-f]{64}$")
    schema_version: str = AGENT_RUN_SCHEMA_VERSION
    rule_version: str = AGENT_RUN_RULE_VERSION
    identity_algorithm: str = AGENT_RUN_IDENTITY_ALGORITHM
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{64}$")
    status: AgentRunStatus
    request: AgentRunRequestResponse
    evidence: AgentRunEvidenceResponse
    policy: AgentAuthorizationPolicyResponse
    release: AgentReleaseObservationResponse
    approval: AgentApprovalObservationResponse
    authorization: AgentAuthorizationResponse
    execution: AgentExecutionStateResponse
    caveat: str = AGENT_RUN_CAVEAT

    @model_validator(mode="after")
    def validate_human_release_binding(self) -> Self:
        human_release = next(
            check for check in self.authorization.checks if check.check_id == "human_release"
        )
        if (self.approval.status == "active") != (human_release.status == "passed"):
            raise ValueError("human release check does not match approval status")
        if (
            self.approval.status == "active"
            and self.approval.approved_proposal_id != self.proposal_id
        ):
            raise ValueError("active approval is not bound to the manifest proposal")
        release_check = next(
            check
            for check in self.authorization.checks
            if check.check_id == "release_threshold_policy"
        )
        if (self.release.status == "eligible_for_human_review") != (
            release_check.status == "passed"
        ):
            raise ValueError("release threshold check does not match release assessment")
        return self


class AgentRunPreflightResponse(BaseModel):
    """One verifiable evidence pack chained to its no-execution manifest."""

    evidence_pack: EvidencePackResponse
    manifest: AgentRunManifestResponse

    @model_validator(mode="after")
    def validate_pack_binding(self) -> Self:
        pack = self.evidence_pack
        evidence = self.manifest.evidence
        if evidence.pack_id != pack.pack_id:
            raise ValueError("manifest is not bound to the returned pack")
        if evidence.pack_rule_version != pack.rule_version:
            raise ValueError("pack rule binding does not match")
        if evidence.pack_status != pack.status:
            raise ValueError("pack status binding does not match")
        if evidence.item_count != pack.item_count:
            raise ValueError("pack item binding does not match")
        if evidence.exclusion_count != pack.exclusion_count:
            raise ValueError("pack exclusion binding does not match")
        if evidence.source_text_characters != pack.source_text_characters:
            raise ValueError("pack character binding does not match")
        if evidence.evidence_ids != tuple(item.evidence_id for item in pack.items):
            raise ValueError("manifest evidence IDs do not match the returned pack")
        return self


def _claim_response(claim: EvidenceClaim) -> EvidenceClaimResponse:
    return EvidenceClaimResponse(
        claim_id=claim.claim_id,
        node_id=claim.node_id,
        predicate=claim.predicate,
        value=claim.value,
        scope=claim.scope,
        scope_value=claim.scope_value,
        evidence_field=claim.evidence_field,
        evidence_excerpt=claim.evidence_excerpt,
        qualifier=claim.qualifier,
        rule_version=claim.rule_version,
    )


def _relationship_response(
    relationship: EvidenceRelationship,
) -> EvidenceRelationshipResponse:
    return EvidenceRelationshipResponse(
        relationship_id=relationship.relationship_id,
        edge_id=relationship.edge_id,
        from_node_id=relationship.from_node_id,
        to_node_id=relationship.to_node_id,
        label=relationship.label,
        predicate=relationship.predicate,
        normalized_value=relationship.normalized_value,
        from_claim_id=relationship.from_claim_id,
        to_claim_id=relationship.to_claim_id,
        basis=relationship.basis,
        rationale=relationship.rationale,
        rule_version=relationship.rule_version,
    )


def _analysis_response(analysis: RelationshipAnalysis) -> RelationshipAnalysisResponse:
    return RelationshipAnalysisResponse(
        claims=tuple(_claim_response(claim) for claim in analysis.claims),
        relationships=tuple(
            _relationship_response(relationship) for relationship in analysis.relationships
        ),
        analyzed_edge_count=analysis.analyzed_edge_count,
        corroboration_count=analysis.corroboration_count,
        contradiction_count=analysis.contradiction_count,
        insufficient_evidence_count=analysis.insufficient_evidence_count,
        rule_version=analysis.rule_version,
        caveat=analysis.caveat,
    )


def _incident_response(incident: IncidentCandidate) -> IncidentCandidateResponse:
    center = (
        IncidentCenterResponse(
            latitude=incident.center_latitude,
            longitude=incident.center_longitude,
        )
        if incident.center_latitude is not None and incident.center_longitude is not None
        else None
    )
    return IncidentCandidateResponse(
        incident_id=incident.incident_id,
        title=incident.title,
        started_at=incident.started_at,
        latest_signal_at=incident.latest_signal_at,
        center=center,
        sources=incident.sources,
        node_count=len(incident.nodes),
        edge_count=len(incident.edges),
        max_distance_km=incident.max_distance_km,
        time_span_minutes=incident.time_span_minutes,
        nodes=tuple(
            EvidenceNodeResponse(
                node_id=node.node_id,
                stream_id=node.message.stream_id,
                event=node.message.event,
            )
            for node in incident.nodes
        ),
        edges=tuple(
            EvidenceEdgeResponse(
                edge_id=edge.edge_id,
                from_node_id=edge.from_node_id,
                to_node_id=edge.to_node_id,
                relation=edge.relation,
                spatial_relation=edge.spatial_relation,
                distance_km=edge.distance_km,
                time_delta_minutes=edge.time_delta_minutes,
                from_geometry_basis=edge.from_geometry_basis,
                to_geometry_basis=edge.to_geometry_basis,
                rule_version=edge.rule_version,
            )
            for edge in incident.edges
        ),
        relationship_analysis=_analysis_response(analyze_incident(incident)),
        rule_version=incident.rule_version,
        caveat=incident.caveat,
    )


def _bounds_from_query(bbox: str | None) -> GeoBounds | None:
    if bbox is None:
        return None
    try:
        values = tuple(float(value.strip()) for value in bbox.split(","))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="bbox must contain four numbers: west,south,east,north",
        ) from error
    if len(values) != 4:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="bbox must contain four numbers: west,south,east,north",
        )
    try:
        return GeoBounds(west=values[0], south=values[1], east=values[2], north=values[3])
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


def _validate_time_window(
    occurred_after: datetime | None,
    occurred_before: datetime | None,
) -> None:
    for value in (occurred_after, occurred_before):
        if value is not None and value.tzinfo is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="time filters must include a UTC offset",
            )
    if (
        occurred_after is not None
        and occurred_before is not None
        and occurred_after > occurred_before
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="occurred_after must not be later than occurred_before",
        )


def _near_from_query(near: str | None, radius_km: float) -> GeoRadius | None:
    if near is None:
        return None
    try:
        values = tuple(float(value.strip()) for value in near.split(","))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="near must contain longitude,latitude",
        ) from error
    if len(values) != 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="near must contain longitude,latitude",
        )
    try:
        return GeoRadius(longitude=values[0], latitude=values[1], radius_km=radius_km)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


def _search_query_from_request(
    *,
    text: str,
    limit: int,
    candidate_limit: int,
    source: SourceName | None,
    occurred_after: datetime | None,
    occurred_before: datetime | None,
    bbox: str | None,
    near: str | None,
    radius_km: float,
    active_only: bool,
    ranking_mode: RankingMode,
) -> SearchQuery:
    _validate_time_window(occurred_after, occurred_before)
    try:
        return SearchQuery(
            text=text,
            limit=limit,
            candidate_limit=candidate_limit,
            source=source,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            bounds=_bounds_from_query(bbox),
            near=_near_from_query(near, radius_km),
            active_only=active_only,
            ranking_mode=ranking_mode,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


def _ranking_response(ranking: RankingExplanation) -> RankingResponse:
    return RankingResponse(
        lexical_rank=ranking.lexical_rank,
        lexical_score=ranking.lexical_score,
        dense_rank=ranking.dense_rank,
        dense_similarity=ranking.dense_similarity,
        rrf_score=ranking.rrf_score,
        exact_phrase_match=ranking.exact_phrase_match,
        token_coverage=ranking.token_coverage,
        rerank_score=ranking.rerank_score,
    )


def _citation_response(citation: CitationValidation) -> CitationResponse:
    return CitationResponse(
        status=citation.status,
        url=citation.url,
        source_field=citation.source_field,
        reasons=citation.reasons,
    )


def _traceable_citation_response(citation: CitationValidation) -> TraceableCitationResponse:
    if citation.status != "traceable" or citation.url is None or citation.source_field is None:
        raise ValueError("included evidence must have a complete traceable citation")
    return TraceableCitationResponse(
        status=citation.status,
        url=citation.url,
        source_field=citation.source_field,
        reasons=citation.reasons,
    )


def _search_parameters_response(query: SearchQuery) -> SearchParametersResponse:
    bounds = query.bounds
    near = query.near
    return SearchParametersResponse(
        query=query.text,
        limit=query.limit,
        candidate_limit=query.candidate_limit,
        source=query.source,
        occurred_after=query.occurred_after,
        occurred_before=query.occurred_before,
        active_only=query.active_only,
        bbox=(bounds.west, bounds.south, bounds.east, bounds.north) if bounds else None,
        near=(near.longitude, near.latitude) if near else None,
        radius_km=near.radius_km if near else None,
        ranking_mode=query.ranking_mode,
    )


def _search_response(result: SearchResult, query: SearchQuery) -> SearchResponse:
    return SearchResponse(
        count=len(result.hits),
        candidates_considered=result.candidates_considered,
        items=tuple(
            SearchHitResponse(
                stream_id=hit.message.stream_id,
                event=hit.message.event,
                document_text=hit.document_text,
                distance_km=hit.distance_km,
                ranking=_ranking_response(hit.ranking),
                citation=_citation_response(hit.citation),
            )
            for hit in result.hits
        ),
        embedding_model=result.embedding_model,
        ranking_mode=result.ranking_mode,
        ranking_rule=result.ranking_rule,
        caveat=result.caveat,
        parameters=_search_parameters_response(query),
    )


def _evidence_pack_response(pack: EvidencePack) -> EvidencePackResponse:
    return EvidencePackResponse(
        pack_id=pack.pack_id,
        schema_version=pack.schema_version,
        rule_version=pack.rule_version,
        identity_algorithm=pack.identity_algorithm,
        status=pack.status,
        item_count=len(pack.items),
        exclusion_count=len(pack.exclusions),
        source_text_characters=pack.source_text_characters,
        budget=EvidencePackBudgetResponse(
            max_items=pack.budget.max_items,
            max_characters_per_item=pack.budget.max_characters_per_item,
            max_total_characters=pack.budget.max_total_characters,
        ),
        retrieval=EvidencePackRetrievalResponse(
            candidates_considered=pack.retrieval.candidates_considered,
            returned_hits=pack.retrieval.returned_hits,
            embedding_model=pack.retrieval.embedding_model,
            ranking_mode=pack.retrieval.ranking_mode,
            ranking_rule=pack.retrieval.ranking_rule,
            caveat=pack.retrieval.caveat,
            parameters=_search_parameters_response(pack.retrieval.query),
        ),
        items=tuple(
            EvidencePackItemResponse(
                evidence_id=item.evidence_id,
                retrieval_rank=item.retrieval_rank,
                stream_id=item.stream_id,
                event_id=item.event_id,
                event_type=item.event_type,
                source=item.source,
                occurred_at=item.occurred_at,
                ingested_at=item.ingested_at,
                event_schema_version=item.event_schema_version,
                text=item.text,
                document_sha256=item.document_sha256,
                text_sha256=item.text_sha256,
                document_characters=item.document_characters,
                text_characters=item.text_characters,
                truncated=item.truncated,
                distance_km=item.distance_km,
                ranking=_ranking_response(item.ranking),
                citation=_traceable_citation_response(item.citation),
            )
            for item in pack.items
        ),
        exclusions=tuple(
            EvidencePackExclusionResponse(
                retrieval_rank=exclusion.retrieval_rank,
                stream_id=exclusion.stream_id,
                event_id=exclusion.event_id,
                event_type=exclusion.event_type,
                source=exclusion.source,
                occurred_at=exclusion.occurred_at,
                ingested_at=exclusion.ingested_at,
                event_schema_version=exclusion.event_schema_version,
                document_sha256=exclusion.document_sha256,
                distance_km=exclusion.distance_km,
                ranking=_ranking_response(exclusion.ranking),
                citation=_citation_response(exclusion.citation),
                reason=exclusion.reason,
            )
            for exclusion in pack.exclusions
        ),
        trust_boundary=pack.trust_boundary,
        caveat=pack.caveat,
    )


def _agent_run_manifest_response(manifest: AgentRunManifest) -> AgentRunManifestResponse:
    capabilities = manifest.request.capabilities
    policy = manifest.policy
    authorization = manifest.authorization
    execution = manifest.execution
    return AgentRunManifestResponse(
        manifest_id=manifest.manifest_id,
        schema_version=manifest.schema_version,
        rule_version=manifest.rule_version,
        identity_algorithm=manifest.identity_algorithm,
        proposal_id=manifest.proposal_id,
        status=manifest.status,
        request=AgentRunRequestResponse(
            purpose=manifest.request.purpose,
            mode=manifest.request.mode,
            requested_output=manifest.request.requested_output,
            capabilities=AgentRunCapabilitiesResponse(
                read_evidence=capabilities.read_evidence,
                generate_text=capabilities.generate_text,
                network_access=capabilities.network_access,
                tool_access=capabilities.tool_access,
                external_side_effects=capabilities.external_side_effects,
            ),
        ),
        evidence=AgentRunEvidenceResponse(
            pack_id=manifest.evidence.pack_id,
            pack_rule_version=manifest.evidence.pack_rule_version,
            pack_status=manifest.evidence.pack_status,
            item_count=manifest.evidence.item_count,
            exclusion_count=manifest.evidence.exclusion_count,
            source_text_characters=manifest.evidence.source_text_characters,
            evidence_ids=manifest.evidence.evidence_ids,
        ),
        policy=AgentAuthorizationPolicyResponse(
            policy_version=policy.policy_version,
            default_decision=policy.default_decision,
            execution_enabled=policy.execution_enabled,
            human_release_required=policy.human_release_required,
            evaluated_model_required=policy.evaluated_model_required,
            relationship_benchmark_required=policy.relationship_benchmark_required,
            grounded_answer_evaluation_required=policy.grounded_answer_evaluation_required,
            agent_trajectory_evaluation_required=policy.agent_trajectory_evaluation_required,
            trajectory_drift_monitoring_required=policy.trajectory_drift_monitoring_required,
            release_threshold_policy_required=policy.release_threshold_policy_required,
            network_access_allowed=policy.network_access_allowed,
            tool_access_allowed=policy.tool_access_allowed,
            external_side_effects_allowed=policy.external_side_effects_allowed,
        ),
        release=AgentReleaseObservationResponse(
            status=manifest.release.status,
            assessment_id=manifest.release.assessment_id,
            assessment_sha256=manifest.release.assessment_sha256,
            policy_id=manifest.release.policy_id,
            policy_sha256=manifest.release.policy_sha256,
            agent_candidate_id=manifest.release.agent_candidate_id,
            relationship_report_id=manifest.release.relationship_report_id,
            trajectory_report_ids=manifest.release.trajectory_report_ids,
            model_adapter_evaluated=manifest.release.model_adapter_evaluated,
            relationship_benchmark_passed=manifest.release.relationship_benchmark_passed,
            grounded_answer_evaluation_passed=(manifest.release.grounded_answer_evaluation_passed),
            agent_trajectory_evaluation_passed=(
                manifest.release.agent_trajectory_evaluation_passed
            ),
            trajectory_drift_monitoring_passed=(
                manifest.release.trajectory_drift_monitoring_passed
            ),
            release_threshold_policy_passed=(manifest.release.release_threshold_policy_passed),
            blocking_reasons=manifest.release.blocking_reasons,
        ),
        approval=AgentApprovalObservationResponse(
            status=manifest.approval.status,
            approval_id=manifest.approval.approval_id,
            approved_proposal_id=manifest.approval.approved_proposal_id,
            source_manifest_id=manifest.approval.source_manifest_id,
            approver_id=manifest.approval.approver_id,
            signing_key_id=manifest.approval.signing_key_id,
            issued_at=manifest.approval.issued_at,
            expires_at=manifest.approval.expires_at,
            revocation_id=manifest.approval.revocation_id,
            evaluated_at=manifest.approval.evaluated_at,
        ),
        authorization=AgentAuthorizationResponse(
            decision=authorization.decision,
            passed_check_count=authorization.passed_check_count,
            blocked_check_count=authorization.blocked_check_count,
            blocking_reasons=authorization.blocking_reasons,
            checks=tuple(
                AuthorizationCheckResponse(
                    check_id=check.check_id,
                    status=check.status,
                    observed=check.observed,
                    required=check.required,
                    blocking_reason=check.blocking_reason,
                )
                for check in authorization.checks
            ),
        ),
        execution=AgentExecutionStateResponse(
            status=execution.status,
            agent_model_invoked=execution.agent_model_invoked,
            agent_network_accessed=execution.agent_network_accessed,
            agent_tools_invoked=execution.agent_tools_invoked,
            answer_generated=execution.answer_generated,
            agent_side_effects_performed=execution.agent_side_effects_performed,
        ),
        caveat=manifest.caveat,
    )


def create_app(
    event_bus: EventBus,
    signal_store: SignalStore | None = None,
    search_service: SearchService | None = None,
    agent_run_ledger: AgentRunLedgerReader | None = None,
    agent_release_assessment: AgentReleaseAssessment | None = None,
    build_commit_sha: str = "unknown",
    source_poll_store: SourcePollStore | None = None,
    source_poll_policies: tuple[SourcePollPolicy, ...] = (),
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    """Create an application with an injected stream implementation."""
    if build_commit_sha != "unknown" and (
        len(build_commit_sha) != 40
        or any(character not in "0123456789abcdef" for character in build_commit_sha)
    ):
        raise ValueError(
            "build commit SHA must be 'unknown' or 40 lowercase hexadecimal characters"
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            try:
                await event_bus.close()
            finally:
                try:
                    if signal_store is not None:
                        await signal_store.close()
                finally:
                    try:
                        if search_service is not None:
                            await search_service.close()
                    finally:
                        try:
                            if agent_run_ledger is not None:
                                await agent_run_ledger.close()
                        finally:
                            if source_poll_store is not None:
                                await source_poll_store.close()

    app = FastAPI(
        title="AtlasPulse API",
        summary="Real-time, evidence-grounded global disruption intelligence",
        version=__version__,
        lifespan=lifespan,
    )

    @app.get("/healthz", response_model=HealthResponse, tags=["operations"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__, commit_sha=build_commit_sha)

    @app.get("/readyz", response_model=HealthResponse, tags=["operations"])
    async def readiness() -> HealthResponse:
        if not await event_bus.is_ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="event stream unavailable",
            )
        if signal_store is not None and not await signal_store.is_ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="signal projection unavailable",
            )
        if search_service is not None and not await search_service.is_ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="retrieval index unavailable",
            )
        return HealthResponse(status="ready", version=__version__, commit_sha=build_commit_sha)

    @app.get(
        "/v1/source-freshness",
        response_model=SourceFreshnessResponse,
        tags=["operations"],
    )
    async def source_freshness() -> SourceFreshnessResponse:
        if source_poll_store is None or not source_poll_policies:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="source freshness unavailable",
            )
        sources = tuple(policy.source for policy in source_poll_policies)
        try:
            states = await source_poll_store.load_states(sources)
            return evaluate_source_freshness(
                source_poll_policies,
                states,
                generated_at=clock(),
            )
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            TypeError,
            ValueError,
            ValkeyError,
        ) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="source freshness unavailable",
            ) from error

    @app.get(
        "/v1/source-polls",
        response_model=SourcePollHistoryResponse,
        tags=["operations"],
    )
    async def source_poll_history(
        before: str | None = Query(default=None, pattern=r"^[0-9]+-[0-9]+$"),
        limit: int = Query(default=24, ge=1, le=100),
    ) -> SourcePollHistoryResponse:
        if source_poll_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="source poll history unavailable",
            )
        try:
            page = await source_poll_store.load_recent_transitions(
                before=before,
                limit=limit + 1,
            )
            has_more = len(page) > limit
            visible = page[:limit]
            return SourcePollHistoryResponse(
                generated_at=clock(),
                count=len(visible),
                items=visible,
                next_cursor=visible[-1].stream_id if visible else None,
                has_more=has_more,
            )
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            TypeError,
            ValueError,
            ValkeyError,
        ) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="source poll history unavailable",
            ) from error

    @app.get("/v1/events", response_model=EventsResponse, tags=["events"])
    async def latest_events(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> EventsResponse:
        messages = await event_bus.latest(limit)
        items = tuple(
            EventEnvelope(stream_id=message.stream_id, event=message.event) for message in messages
        )
        return EventsResponse(count=len(items), items=items)

    @app.get("/v1/events/replay", response_model=ReplayResponse, tags=["events"])
    async def replay_events(
        after: str | None = Query(default=None, pattern=r"^\d+-\d+$"),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> ReplayResponse:
        page = await event_bus.replay(after=after, limit=limit + 1)
        has_more = len(page) > limit
        visible = page[:limit]
        items = tuple(
            EventEnvelope(stream_id=message.stream_id, event=message.event) for message in visible
        )
        return ReplayResponse(
            count=len(items),
            items=items,
            next_cursor=items[-1].stream_id if items else None,
            has_more=has_more,
        )

    @app.get("/v1/signals", response_model=SignalsResponse, tags=["signals"])
    async def current_signals(
        limit: int = Query(default=100, ge=1, le=500),
        after: str | None = Query(default=None, pattern=r"^\d+-\d+$"),
        source: SourceName | None = None,
        min_severity: int | None = Query(default=None, ge=0, le=4),
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        bbox: str | None = Query(
            default=None,
            description="Non-wrapping WGS84 west,south,east,north viewport",
        ),
        include_area_only: bool = Query(default=False),
        active_only: bool = Query(default=True),
    ) -> SignalsResponse:
        if signal_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="signal projection unavailable",
            )
        _validate_time_window(occurred_after, occurred_before)
        page = await signal_store.query_current(
            SignalQuery(
                limit=limit,
                after=after,
                source=source,
                min_severity=min_severity,
                occurred_after=occurred_after,
                occurred_before=occurred_before,
                bounds=_bounds_from_query(bbox),
                include_area_only=include_area_only,
                active_only=active_only,
            )
        )
        items = tuple(
            EventEnvelope(stream_id=message.stream_id, event=message.event)
            for message in page.items
        )
        return SignalsResponse(
            count=len(items),
            items=items,
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )

    @app.get("/v1/incidents", response_model=IncidentsResponse, tags=["incidents"])
    async def incident_candidates(
        limit: int = Query(default=50, ge=1, le=100),
        radius_km: float = Query(default=50.0, gt=0, le=500),
        time_window_minutes: int = Query(default=360, ge=1, le=1_440),
        lookback_hours: int = Query(default=24, ge=1, le=168),
        candidate_edge_limit: int = Query(default=2_000, ge=1, le=5_000),
        bbox: str | None = Query(
            default=None,
            description="Optional non-wrapping WGS84 west,south,east,north viewport",
        ),
        active_only: bool = Query(default=True),
    ) -> IncidentsResponse:
        if signal_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="signal projection unavailable",
            )
        bounds = _bounds_from_query(bbox)
        query = CorrelationQuery(
            radius_km=radius_km,
            time_window_minutes=time_window_minutes,
            lookback_hours=lookback_hours,
            edge_limit=candidate_edge_limit,
            bounds=bounds,
            active_only=active_only,
        )
        result = build_incident_candidates(
            await signal_store.query_correlations(query),
            limit=limit,
        )
        return IncidentsResponse(
            count=len(result.incidents),
            total_incidents=result.total_incidents,
            items=tuple(_incident_response(incident) for incident in result.incidents),
            incidents_truncated=result.incidents_truncated,
            candidate_edges_truncated=result.candidate_edges_truncated,
            parameters=CorrelationParametersResponse(
                radius_km=query.radius_km,
                time_window_minutes=query.time_window_minutes,
                lookback_hours=query.lookback_hours,
                candidate_edge_limit=query.edge_limit,
                incident_limit=limit,
                active_only=query.active_only,
                bbox=(bounds.west, bounds.south, bounds.east, bounds.north) if bounds else None,
            ),
        )

    @app.get("/v1/search", response_model=SearchResponse, tags=["retrieval"])
    async def search_events(
        q: str = Query(min_length=2, max_length=500),
        limit: int = Query(default=10, ge=1, le=50),
        candidate_limit: int = Query(default=50, ge=1, le=200),
        source: SourceName | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        bbox: str | None = Query(
            default=None,
            description="Optional non-wrapping WGS84 west,south,east,north viewport",
        ),
        near: str | None = Query(
            default=None,
            description="Optional WGS84 longitude,latitude radius origin",
        ),
        radius_km: float = Query(default=250.0, gt=0, le=2_000),
        active_only: bool = Query(default=True),
        ranking_mode: RankingMode = "hybrid",
    ) -> SearchResponse:
        if search_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="retrieval index unavailable",
            )
        query = _search_query_from_request(
            text=q,
            limit=limit,
            candidate_limit=candidate_limit,
            source=source,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            bbox=bbox,
            near=near,
            radius_km=radius_km,
            active_only=active_only,
            ranking_mode=ranking_mode,
        )
        return _search_response(await search_service.search(query), query)

    @app.get(
        "/v1/evidence-packs",
        response_model=EvidencePackResponse,
        tags=["retrieval", "agents"],
    )
    async def evidence_pack(
        q: str = Query(min_length=2, max_length=500),
        retrieval_limit: int = Query(default=20, ge=1, le=50),
        candidate_limit: int = Query(default=100, ge=1, le=200),
        max_items: int = Query(default=8, ge=1, le=50),
        max_characters_per_item: int = Query(default=2_000, ge=1, le=8_000),
        max_total_characters: int = Query(default=12_000, ge=1, le=64_000),
        source: SourceName | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        bbox: str | None = Query(
            default=None,
            description="Optional non-wrapping WGS84 west,south,east,north viewport",
        ),
        near: str | None = Query(
            default=None,
            description="Optional WGS84 longitude,latitude radius origin",
        ),
        radius_km: float = Query(default=250.0, gt=0, le=2_000),
        active_only: bool = Query(default=True),
        ranking_mode: RankingMode = "hybrid",
    ) -> EvidencePackResponse:
        if search_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="retrieval index unavailable",
            )
        query = _search_query_from_request(
            text=q,
            limit=retrieval_limit,
            candidate_limit=candidate_limit,
            source=source,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            bbox=bbox,
            near=near,
            radius_km=radius_km,
            active_only=active_only,
            ranking_mode=ranking_mode,
        )
        budget = EvidencePackBudget(
            max_items=max_items,
            max_characters_per_item=max_characters_per_item,
            max_total_characters=max_total_characters,
        )
        result = await search_service.search(query)
        return _evidence_pack_response(build_evidence_pack(result, query, budget=budget))

    @app.get(
        "/v1/agent-runs/preflight",
        response_model=AgentRunPreflightResponse,
        tags=["agents"],
    )
    async def agent_run_preflight(
        q: str = Query(min_length=2, max_length=500),
        approval_id: str | None = Query(
            default=None,
            pattern=r"^approval-[0-9a-f]{64}$",
            description="Optional signed-ledger approval to evaluate for this exact proposal",
        ),
        retrieval_limit: int = Query(default=20, ge=1, le=50),
        candidate_limit: int = Query(default=100, ge=1, le=200),
        max_items: int = Query(default=8, ge=1, le=50),
        max_characters_per_item: int = Query(default=2_000, ge=1, le=8_000),
        max_total_characters: int = Query(default=12_000, ge=1, le=64_000),
        source: SourceName | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        bbox: str | None = Query(
            default=None,
            description="Optional non-wrapping WGS84 west,south,east,north viewport",
        ),
        near: str | None = Query(
            default=None,
            description="Optional WGS84 longitude,latitude radius origin",
        ),
        radius_km: float = Query(default=250.0, gt=0, le=2_000),
        active_only: bool = Query(default=True),
        ranking_mode: RankingMode = "hybrid",
    ) -> AgentRunPreflightResponse:
        if search_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="retrieval index unavailable",
            )
        query = _search_query_from_request(
            text=q,
            limit=retrieval_limit,
            candidate_limit=candidate_limit,
            source=source,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            bbox=bbox,
            near=near,
            radius_km=radius_km,
            active_only=active_only,
            ranking_mode=ranking_mode,
        )
        budget = EvidencePackBudget(
            max_items=max_items,
            max_characters_per_item=max_characters_per_item,
            max_total_characters=max_total_characters,
        )
        result = await search_service.search(query)
        pack = build_evidence_pack(result, query, budget=budget)
        release = (
            agent_release_observation(agent_release_assessment)
            if agent_release_assessment is not None
            else AgentReleaseObservation()
        )
        proposal = build_agent_run_proposal(pack, release=release)
        approval = AgentApprovalObservation()
        if approval_id is not None:
            evaluated_at = datetime.now(UTC)
            approval = (
                await agent_run_ledger.resolve_approval(
                    approval_id=approval_id,
                    proposal_id=proposal.proposal_id,
                    evaluated_at=evaluated_at,
                )
                if agent_run_ledger is not None
                else AgentApprovalObservation(
                    status="ledger_unavailable",
                    approval_id=approval_id,
                    evaluated_at=evaluated_at,
                )
            )
        manifest = build_agent_run_manifest(
            pack,
            approval=approval,
            proposal=proposal,
            release=release,
        )
        return AgentRunPreflightResponse(
            evidence_pack=_evidence_pack_response(pack),
            manifest=_agent_run_manifest_response(manifest),
        )

    return app

"""Fail-closed, content-addressed preflight manifests for future agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from atlas_pulse.evidence_packs import (
    EVIDENCE_PACK_IDENTITY_ALGORITHM,
    EvidencePack,
    EvidencePackStatus,
)
from atlas_pulse.identity import canonical_json_sha256

if TYPE_CHECKING:
    from atlas_pulse.agent_trajectory.release import (
        AgentReleaseAssessment,
        ReleaseGateId,
    )

AGENT_RUN_SCHEMA_VERSION = "1.2.0"
AGENT_RUN_RULE_VERSION = "agent-run-manifest-v3"
AGENT_RUN_PROPOSAL_RULE_VERSION = "agent-run-proposal-v2"
AGENT_AUTHORIZATION_POLICY_VERSION = "agent-authorization-v3"
AGENT_RUN_IDENTITY_ALGORITHM = EVIDENCE_PACK_IDENTITY_ALGORITHM
AGENT_RUN_CAVEAT = (
    "Preflight records a deterministic policy decision bound to one evidence-pack identity. "
    "A validated release assessment may satisfy only its measured quality gates, and a trusted, "
    "active approval may satisfy only the human-release check. Preflight may perform ordinary "
    "local retrieval to assemble the pack, but it does not start the proposed agent, invoke a "
    "generative agent model, grant agent network or tool access, generate an answer, or perform "
    "agent side effects."
)

AgentRunPurpose = Literal["evidence_triage"]
AgentRunMode = Literal["read_only"]
AgentRunOutput = Literal["grounded_evidence_brief"]
AgentRunStatus = Literal["blocked"]
AgentApprovalStatus = Literal[
    "not_supplied",
    "not_found",
    "not_yet_valid",
    "active",
    "expired",
    "revoked",
    "scope_mismatch",
    "untrusted_signer",
    "ledger_invalid",
    "ledger_unavailable",
]
AgentReleaseStatus = Literal[
    "not_supplied",
    "blocked",
    "eligible_for_human_review",
]
AuthorizationCheckStatus = Literal["passed", "blocked"]
AuthorizationCheckId = Literal[
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
]
AuthorizationBlockReason = Literal[
    "no_traceable_evidence",
    "model_adapter_not_selected",
    "live_relationship_benchmark_incomplete",
    "grounded_answer_evaluation_missing",
    "agent_trajectory_evaluation_missing",
    "trajectory_drift_evidence_missing",
    "release_thresholds_not_met",
    "human_release_not_granted",
    "human_release_not_yet_valid",
    "human_release_expired",
    "human_release_revoked",
    "human_release_scope_mismatch",
    "human_release_untrusted",
    "approval_ledger_invalid",
    "approval_ledger_unavailable",
    "execution_disabled",
]


@dataclass(frozen=True, slots=True)
class AgentRunCapabilities:
    """Explicit capabilities requested by the first governed agent profile."""

    read_evidence: Literal[True] = True
    generate_text: Literal[True] = True
    network_access: Literal[False] = False
    tool_access: Literal[False] = False
    external_side_effects: Literal[False] = False


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    """Narrow future run intent; this milestone never executes it."""

    purpose: AgentRunPurpose = "evidence_triage"
    mode: AgentRunMode = "read_only"
    requested_output: AgentRunOutput = "grounded_evidence_brief"
    capabilities: AgentRunCapabilities = field(default_factory=AgentRunCapabilities)


@dataclass(frozen=True, slots=True)
class AgentRunEvidence:
    """Content-addressed evidence dependency bound into one manifest."""

    pack_id: str
    pack_rule_version: str
    pack_status: EvidencePackStatus
    item_count: int
    exclusion_count: int
    source_text_characters: int
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentAuthorizationPolicy:
    """Exact default-deny policy snapshot used for this preflight."""

    policy_version: str = AGENT_AUTHORIZATION_POLICY_VERSION
    default_decision: Literal["deny"] = "deny"
    execution_enabled: Literal[False] = False
    human_release_required: Literal[True] = True
    evaluated_model_required: Literal[True] = True
    relationship_benchmark_required: Literal[True] = True
    grounded_answer_evaluation_required: Literal[True] = True
    agent_trajectory_evaluation_required: Literal[True] = True
    trajectory_drift_monitoring_required: Literal[True] = True
    release_threshold_policy_required: Literal[True] = True
    network_access_allowed: Literal[False] = False
    tool_access_allowed: Literal[False] = False
    external_side_effects_allowed: Literal[False] = False


@dataclass(frozen=True, slots=True)
class AgentApprovalObservation:
    """Approval state resolved from the signed ledger for one exact proposal."""

    status: AgentApprovalStatus = "not_supplied"
    approval_id: str | None = None
    approved_proposal_id: str | None = None
    source_manifest_id: str | None = None
    approver_id: str | None = None
    signing_key_id: str | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    revocation_id: str | None = None
    evaluated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AgentReleaseObservation:
    """Validated quality evidence selected for this exact proposal scope."""

    status: AgentReleaseStatus = "not_supplied"
    assessment_id: str | None = None
    assessment_sha256: str | None = None
    policy_id: str | None = None
    policy_sha256: str | None = None
    agent_candidate_id: str | None = None
    relationship_report_id: str | None = None
    trajectory_report_ids: tuple[str, ...] = ()
    model_adapter_evaluated: bool = False
    relationship_benchmark_passed: bool = False
    grounded_answer_evaluation_passed: bool = False
    agent_trajectory_evaluation_passed: bool = False
    trajectory_drift_monitoring_passed: bool = False
    release_threshold_policy_passed: bool = False
    blocking_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentRunProposal:
    """Stable approval scope independent of the time-varying authorization result."""

    proposal_id: str
    proposal_rule_version: str
    request: AgentRunRequest
    evidence: AgentRunEvidence
    policy: AgentAuthorizationPolicy
    release: AgentReleaseObservation


@dataclass(frozen=True, slots=True)
class AuthorizationCheck:
    """One inspectable policy comparison with a machine-readable block reason."""

    check_id: AuthorizationCheckId
    status: AuthorizationCheckStatus
    observed: str
    required: str
    blocking_reason: AuthorizationBlockReason | None


@dataclass(frozen=True, slots=True)
class AgentAuthorization:
    """Fail-closed authorization result and every contributing check."""

    decision: AgentRunStatus
    passed_check_count: int
    blocked_check_count: int
    blocking_reasons: tuple[AuthorizationBlockReason, ...]
    checks: tuple[AuthorizationCheck, ...]


@dataclass(frozen=True, slots=True)
class AgentExecutionState:
    """Proof that preflight never started the proposed agent execution."""

    status: Literal["not_started"] = "not_started"
    agent_model_invoked: Literal[False] = False
    agent_network_accessed: Literal[False] = False
    agent_tools_invoked: Literal[False] = False
    answer_generated: Literal[False] = False
    agent_side_effects_performed: Literal[False] = False


@dataclass(frozen=True, slots=True)
class AgentRunManifest:
    """Immutable proposed-run record; authorization is deliberately blocked in v1."""

    manifest_id: str
    schema_version: str
    rule_version: str
    identity_algorithm: str
    proposal_id: str
    status: AgentRunStatus
    request: AgentRunRequest
    evidence: AgentRunEvidence
    policy: AgentAuthorizationPolicy
    release: AgentReleaseObservation
    approval: AgentApprovalObservation
    authorization: AgentAuthorization
    execution: AgentExecutionState
    caveat: str


def _check(
    check_id: AuthorizationCheckId,
    *,
    passed: bool,
    observed: str,
    required: str,
    blocking_reason: AuthorizationBlockReason | None = None,
) -> AuthorizationCheck:
    if passed:
        return AuthorizationCheck(
            check_id=check_id,
            status="passed",
            observed=observed,
            required=required,
            blocking_reason=None,
        )
    if blocking_reason is None:
        raise ValueError("blocked authorization checks require a blocking reason")
    return AuthorizationCheck(
        check_id=check_id,
        status="blocked",
        observed=observed,
        required=required,
        blocking_reason=blocking_reason,
    )


def _human_release_blocking_reason(status: AgentApprovalStatus) -> AuthorizationBlockReason:
    if status == "not_yet_valid":
        return "human_release_not_yet_valid"
    if status == "expired":
        return "human_release_expired"
    if status == "revoked":
        return "human_release_revoked"
    if status == "scope_mismatch":
        return "human_release_scope_mismatch"
    if status == "untrusted_signer":
        return "human_release_untrusted"
    if status == "ledger_invalid":
        return "approval_ledger_invalid"
    if status == "ledger_unavailable":
        return "approval_ledger_unavailable"
    return "human_release_not_granted"


def agent_release_observation(assessment: AgentReleaseAssessment) -> AgentReleaseObservation:
    """Reduce a validated assessment to the exact quality state bound into preflight."""
    passed = {gate.gate_id: gate.passed for gate in assessment.gates}

    def every(*gate_ids: ReleaseGateId) -> bool:
        return all(passed.get(gate_id, False) for gate_id in gate_ids)

    return AgentReleaseObservation(
        status=assessment.status,
        assessment_id=assessment.assessment_id,
        assessment_sha256=assessment.assessment_sha256,
        policy_id=assessment.policy.policy_id,
        policy_sha256=assessment.policy.policy_sha256,
        agent_candidate_id=assessment.agent_system.candidate_id,
        relationship_report_id=assessment.relationship_evidence.report_id,
        trajectory_report_ids=tuple(
            evidence.report_id for evidence in assessment.trajectory_evidence
        ),
        model_adapter_evaluated=every("agent_candidate_identity"),
        relationship_benchmark_passed=every(
            "relationship_sample_size",
            "relationship_macro_f1",
            "relationship_accuracy_delta",
            "relationship_regression_rate",
        ),
        grounded_answer_evaluation_passed=every(
            "grounded_review_adjudication",
            "grounded_answer_coverage",
            "grounded_strict_pass",
            "grounded_unsupported_claims",
            "grounded_complete_citations",
        ),
        agent_trajectory_evaluation_passed=every(
            "trajectory_capture_count",
            "trajectory_total_cases",
            "trajectory_capture_sample_size",
            "trajectory_policy_compliance",
            "trajectory_evidence_inspection",
            "trajectory_claim_trace",
            "trajectory_strict_pass",
            "trajectory_end_to_end_pass",
            "trajectory_step_budget",
            "trajectory_latency_budget",
        ),
        trajectory_drift_monitoring_passed=every(
            "trajectory_drift_chain",
            "trajectory_drift_stability",
        ),
        release_threshold_policy_passed=assessment.status == "eligible_for_human_review",
        blocking_reasons=assessment.blocking_reasons,
    )


def _validate_release_observation(release: AgentReleaseObservation) -> None:
    identity_fields = (
        release.assessment_id,
        release.assessment_sha256,
        release.policy_id,
        release.policy_sha256,
        release.agent_candidate_id,
        release.relationship_report_id,
    )
    flags = (
        release.model_adapter_evaluated,
        release.relationship_benchmark_passed,
        release.grounded_answer_evaluation_passed,
        release.agent_trajectory_evaluation_passed,
        release.trajectory_drift_monitoring_passed,
        release.release_threshold_policy_passed,
    )
    if release.status == "not_supplied":
        if (
            any(value is not None for value in identity_fields)
            or release.trajectory_report_ids
            or any(flags)
            or release.blocking_reasons
        ):
            raise ValueError("not_supplied release observation cannot contain assessment fields")
        return
    if any(value is None for value in identity_fields) or not release.trajectory_report_ids:
        raise ValueError("resolved release observations require complete assessment identity")
    if release.status == "eligible_for_human_review":
        if not all(flags) or release.blocking_reasons:
            raise ValueError("eligible release observations require every quality gate to pass")
    elif release.release_threshold_policy_passed or not release.blocking_reasons:
        raise ValueError("blocked release observations require threshold blocking reasons")


def _validate_active_approval(
    approval: AgentApprovalObservation,
    *,
    proposal_id: str,
) -> None:
    if approval.status != "active":
        return
    required = (
        approval.approval_id,
        approval.approved_proposal_id,
        approval.source_manifest_id,
        approval.approver_id,
        approval.signing_key_id,
        approval.issued_at,
        approval.expires_at,
        approval.evaluated_at,
    )
    if any(value is None for value in required):
        raise ValueError("active approval observation is incomplete")
    if approval.approved_proposal_id != proposal_id:
        raise ValueError("active approval does not match the run proposal")
    if approval.revocation_id is not None:
        raise ValueError("active approval cannot contain a revocation")
    assert approval.issued_at is not None
    assert approval.expires_at is not None
    assert approval.evaluated_at is not None
    if not approval.issued_at <= approval.evaluated_at < approval.expires_at:
        raise ValueError("active approval observation is outside its lifetime")


def _authorization_checks(
    pack: EvidencePack,
    release: AgentReleaseObservation,
    approval: AgentApprovalObservation,
) -> tuple[AuthorizationCheck, ...]:
    traceable_evidence = pack.status == "traceable_evidence_available" and bool(pack.items)
    return (
        _check(
            "evidence_pack_integrity",
            passed=True,
            observed="content_addressed_bounded_pack",
            required="content_addressed_bounded_pack",
        ),
        _check(
            "traceable_evidence",
            passed=traceable_evidence,
            observed=pack.status,
            required="traceable_evidence_available",
            blocking_reason="no_traceable_evidence",
        ),
        _check(
            "capability_scope",
            passed=True,
            observed="read_only_no_network_no_tools_no_side_effects",
            required="read_only_no_network_no_tools_no_side_effects",
        ),
        _check(
            "model_adapter",
            passed=release.model_adapter_evaluated,
            observed=release.agent_candidate_id or release.status,
            required="evaluated_model_adapter",
            blocking_reason="model_adapter_not_selected",
        ),
        _check(
            "live_relationship_benchmark",
            passed=release.relationship_benchmark_passed,
            observed=release.relationship_report_id or release.status,
            required="adjudicated_pass",
            blocking_reason="live_relationship_benchmark_incomplete",
        ),
        _check(
            "grounded_answer_evaluation",
            passed=release.grounded_answer_evaluation_passed,
            observed=release.assessment_id or release.status,
            required="evaluated_pass",
            blocking_reason="grounded_answer_evaluation_missing",
        ),
        _check(
            "agent_trajectory_evaluation",
            passed=release.agent_trajectory_evaluation_passed,
            observed=release.assessment_id or release.status,
            required="observable_trajectory_pass",
            blocking_reason="agent_trajectory_evaluation_missing",
        ),
        _check(
            "trajectory_drift_monitoring",
            passed=release.trajectory_drift_monitoring_passed,
            observed=release.assessment_id or release.status,
            required="complete_stable_drift_chain",
            blocking_reason="trajectory_drift_evidence_missing",
        ),
        _check(
            "release_threshold_policy",
            passed=release.release_threshold_policy_passed,
            observed=release.status,
            required="eligible_for_human_review",
            blocking_reason="release_thresholds_not_met",
        ),
        _check(
            "human_release",
            passed=approval.status == "active",
            observed=approval.status,
            required="active_trusted_unrevoked_approval",
            blocking_reason=_human_release_blocking_reason(approval.status),
        ),
        _check(
            "execution_release",
            passed=False,
            observed="disabled",
            required="enabled",
            blocking_reason="execution_disabled",
        ),
    )


def _proposal_payload(
    *,
    request: AgentRunRequest,
    evidence: AgentRunEvidence,
    policy: AgentAuthorizationPolicy,
    release: AgentReleaseObservation,
) -> dict[str, object]:
    return {
        "schema_version": AGENT_RUN_SCHEMA_VERSION,
        "manifest_rule_version": AGENT_RUN_RULE_VERSION,
        "proposal_rule_version": AGENT_RUN_PROPOSAL_RULE_VERSION,
        "identity_algorithm": AGENT_RUN_IDENTITY_ALGORITHM,
        "request": asdict(request),
        "evidence": asdict(evidence),
        "policy": asdict(policy),
        "release": asdict(release),
    }


def build_agent_run_proposal(
    pack: EvidencePack,
    *,
    release: AgentReleaseObservation | None = None,
) -> AgentRunProposal:
    """Build the stable, content-addressed scope that a human may approve."""
    release = release or AgentReleaseObservation()
    _validate_release_observation(release)
    request = AgentRunRequest()
    evidence = AgentRunEvidence(
        pack_id=pack.pack_id,
        pack_rule_version=pack.rule_version,
        pack_status=pack.status,
        item_count=len(pack.items),
        exclusion_count=len(pack.exclusions),
        source_text_characters=pack.source_text_characters,
        evidence_ids=tuple(item.evidence_id for item in pack.items),
    )
    policy = AgentAuthorizationPolicy()
    payload = _proposal_payload(
        request=request,
        evidence=evidence,
        policy=policy,
        release=release,
    )
    return AgentRunProposal(
        proposal_id=f"proposal-{canonical_json_sha256(payload)}",
        proposal_rule_version=AGENT_RUN_PROPOSAL_RULE_VERSION,
        request=request,
        evidence=evidence,
        policy=policy,
        release=release,
    )


def _manifest_payload(
    *,
    proposal_id: str,
    request: AgentRunRequest,
    evidence: AgentRunEvidence,
    policy: AgentAuthorizationPolicy,
    release: AgentReleaseObservation,
    approval: AgentApprovalObservation,
    authorization: AgentAuthorization,
    execution: AgentExecutionState,
) -> dict[str, object]:
    return {
        "schema_version": AGENT_RUN_SCHEMA_VERSION,
        "rule_version": AGENT_RUN_RULE_VERSION,
        "identity_algorithm": AGENT_RUN_IDENTITY_ALGORITHM,
        "proposal_id": proposal_id,
        "status": authorization.decision,
        "request": asdict(request),
        "evidence": asdict(evidence),
        "policy": asdict(policy),
        "release": asdict(release),
        "approval": asdict(approval),
        "authorization": asdict(authorization),
        "execution": asdict(execution),
        "caveat": AGENT_RUN_CAVEAT,
    }


def build_agent_run_manifest(
    pack: EvidencePack,
    *,
    approval: AgentApprovalObservation | None = None,
    proposal: AgentRunProposal | None = None,
    release: AgentReleaseObservation | None = None,
) -> AgentRunManifest:
    """Build a no-execution manifest under the locked v3 policy."""
    release = release or AgentReleaseObservation()
    _validate_release_observation(release)
    expected_proposal = build_agent_run_proposal(pack, release=release)
    if proposal is not None and proposal != expected_proposal:
        raise ValueError("agent run proposal does not match the evidence pack")
    proposal = proposal or expected_proposal
    approval = approval or AgentApprovalObservation()
    _validate_active_approval(approval, proposal_id=proposal.proposal_id)
    checks = _authorization_checks(pack, release, approval)
    blocking_reasons = tuple(
        check.blocking_reason
        for check in checks
        if check.status == "blocked" and check.blocking_reason is not None
    )
    authorization = AgentAuthorization(
        decision="blocked",
        passed_check_count=sum(check.status == "passed" for check in checks),
        blocked_check_count=sum(check.status == "blocked" for check in checks),
        blocking_reasons=blocking_reasons,
        checks=checks,
    )
    execution = AgentExecutionState()
    payload = _manifest_payload(
        proposal_id=proposal.proposal_id,
        request=proposal.request,
        evidence=proposal.evidence,
        policy=proposal.policy,
        release=proposal.release,
        approval=approval,
        authorization=authorization,
        execution=execution,
    )
    return AgentRunManifest(
        manifest_id=f"manifest-{canonical_json_sha256(payload)}",
        schema_version=AGENT_RUN_SCHEMA_VERSION,
        rule_version=AGENT_RUN_RULE_VERSION,
        identity_algorithm=AGENT_RUN_IDENTITY_ALGORITHM,
        proposal_id=proposal.proposal_id,
        status=authorization.decision,
        request=proposal.request,
        evidence=proposal.evidence,
        policy=proposal.policy,
        release=proposal.release,
        approval=approval,
        authorization=authorization,
        execution=execution,
        caveat=AGENT_RUN_CAVEAT,
    )

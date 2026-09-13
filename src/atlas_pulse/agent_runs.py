"""Fail-closed, content-addressed preflight manifests for future agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from atlas_pulse.evidence_packs import (
    EVIDENCE_PACK_IDENTITY_ALGORITHM,
    EvidencePack,
    EvidencePackStatus,
)
from atlas_pulse.identity import canonical_json_sha256

AGENT_RUN_SCHEMA_VERSION = "1.0.0"
AGENT_RUN_RULE_VERSION = "agent-run-manifest-v1"
AGENT_AUTHORIZATION_POLICY_VERSION = "agent-authorization-v1"
AGENT_RUN_IDENTITY_ALGORITHM = EVIDENCE_PACK_IDENTITY_ALGORITHM
AGENT_RUN_CAVEAT = (
    "Preflight records a deterministic policy decision bound to one evidence-pack identity. "
    "It may perform ordinary local retrieval to assemble that pack, but it does not start the "
    "proposed agent, invoke a generative agent model, grant agent network or tool access, generate "
    "an answer, or perform agent side effects."
)

AgentRunPurpose = Literal["evidence_triage"]
AgentRunMode = Literal["read_only"]
AgentRunOutput = Literal["grounded_evidence_brief"]
AgentRunStatus = Literal["blocked"]
AuthorizationCheckStatus = Literal["passed", "blocked"]
AuthorizationCheckId = Literal[
    "evidence_pack_integrity",
    "traceable_evidence",
    "capability_scope",
    "model_adapter",
    "live_relationship_benchmark",
    "grounded_answer_evaluation",
    "human_release",
    "execution_release",
]
AuthorizationBlockReason = Literal[
    "no_traceable_evidence",
    "model_adapter_not_selected",
    "live_relationship_benchmark_incomplete",
    "grounded_answer_evaluation_missing",
    "human_release_not_granted",
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
    network_access_allowed: Literal[False] = False
    tool_access_allowed: Literal[False] = False
    external_side_effects_allowed: Literal[False] = False


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
    status: AgentRunStatus
    request: AgentRunRequest
    evidence: AgentRunEvidence
    policy: AgentAuthorizationPolicy
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


def _authorization_checks(pack: EvidencePack) -> tuple[AuthorizationCheck, ...]:
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
            passed=False,
            observed="not_selected",
            required="evaluated_model_adapter",
            blocking_reason="model_adapter_not_selected",
        ),
        _check(
            "live_relationship_benchmark",
            passed=False,
            observed="awaiting_independent_adjudication",
            required="adjudicated_pass",
            blocking_reason="live_relationship_benchmark_incomplete",
        ),
        _check(
            "grounded_answer_evaluation",
            passed=False,
            observed="not_available",
            required="evaluated_pass",
            blocking_reason="grounded_answer_evaluation_missing",
        ),
        _check(
            "human_release",
            passed=False,
            observed="not_granted",
            required="explicit_human_approval",
            blocking_reason="human_release_not_granted",
        ),
        _check(
            "execution_release",
            passed=False,
            observed="disabled",
            required="enabled",
            blocking_reason="execution_disabled",
        ),
    )


def _manifest_payload(
    *,
    request: AgentRunRequest,
    evidence: AgentRunEvidence,
    policy: AgentAuthorizationPolicy,
    authorization: AgentAuthorization,
    execution: AgentExecutionState,
) -> dict[str, object]:
    return {
        "schema_version": AGENT_RUN_SCHEMA_VERSION,
        "rule_version": AGENT_RUN_RULE_VERSION,
        "identity_algorithm": AGENT_RUN_IDENTITY_ALGORITHM,
        "status": authorization.decision,
        "request": asdict(request),
        "evidence": asdict(evidence),
        "policy": asdict(policy),
        "authorization": asdict(authorization),
        "execution": asdict(execution),
        "caveat": AGENT_RUN_CAVEAT,
    }


def build_agent_run_manifest(pack: EvidencePack) -> AgentRunManifest:
    """Build a deterministic no-execution manifest under the locked v1 policy."""
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
    checks = _authorization_checks(pack)
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
        request=request,
        evidence=evidence,
        policy=policy,
        authorization=authorization,
        execution=execution,
    )
    return AgentRunManifest(
        manifest_id=f"manifest-{canonical_json_sha256(payload)}",
        schema_version=AGENT_RUN_SCHEMA_VERSION,
        rule_version=AGENT_RUN_RULE_VERSION,
        identity_algorithm=AGENT_RUN_IDENTITY_ALGORITHM,
        status=authorization.decision,
        request=request,
        evidence=evidence,
        policy=policy,
        authorization=authorization,
        execution=execution,
        caveat=AGENT_RUN_CAVEAT,
    )

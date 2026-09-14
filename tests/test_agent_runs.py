"""Content-addressed, fail-closed future agent-run manifest tests."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

import pytest
from agent_rag_core import Event

from atlas_pulse.agent_runs import (
    AGENT_AUTHORIZATION_POLICY_VERSION,
    AGENT_RUN_CAVEAT,
    AGENT_RUN_IDENTITY_ALGORITHM,
    AGENT_RUN_RULE_VERSION,
    AGENT_RUN_SCHEMA_VERSION,
    AgentApprovalObservation,
    AgentApprovalStatus,
    AgentReleaseObservation,
    _check,
    build_agent_run_manifest,
    build_agent_run_proposal,
)
from atlas_pulse.evidence_packs import build_evidence_pack
from atlas_pulse.identity import canonical_json_sha256
from atlas_pulse.retrieval import (
    CitationValidation,
    RankingExplanation,
    SearchHit,
    SearchQuery,
    SearchResult,
)
from atlas_pulse.retrieval.ranking import RANKING_RULE, RETRIEVAL_CAVEAT
from atlas_pulse.streams import StreamMessage


def _search_result(text: str = "Title: Severe thunderstorm warning") -> SearchResult:
    event = Event(
        event_id="alert-1",
        event_type="weather.alert",
        source="nws",
        occurred_at=datetime(2026, 9, 13, 12, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 13, 12, 1, tzinfo=UTC),
        payload={"title": "Severe thunderstorm warning"},
    )
    hit = SearchHit(
        message=StreamMessage(stream_id="100-1", event=event),
        document_text=text,
        distance_km=None,
        ranking=RankingExplanation(
            lexical_rank=1,
            lexical_score=0.8,
            dense_rank=1,
            dense_similarity=0.9,
            rrf_score=0.95,
            exact_phrase_match=True,
            token_coverage=1.0,
            rerank_score=0.95,
        ),
        citation=CitationValidation(
            status="traceable",
            url="https://api.weather.gov/alerts/alert-1",
            source_field="source_url",
            reasons=("public_http_url",),
        ),
    )
    return SearchResult(
        hits=(hit,),
        candidates_considered=3,
        embedding_model="test/local-embedding",
        ranking_mode="hybrid",
        ranking_rule=RANKING_RULE,
        caveat=RETRIEVAL_CAVEAT,
    )


def test_manifest_binds_the_exact_pack_and_blocks_every_unmet_release_gate() -> None:
    pack = build_evidence_pack(
        _search_result(),
        SearchQuery(text="dangerous storm", limit=5, candidate_limit=20),
    )

    manifest = build_agent_run_manifest(pack)

    assert manifest.schema_version == AGENT_RUN_SCHEMA_VERSION
    assert manifest.rule_version == AGENT_RUN_RULE_VERSION
    assert manifest.identity_algorithm == AGENT_RUN_IDENTITY_ALGORITHM
    assert manifest.proposal_id == build_agent_run_proposal(pack).proposal_id
    assert manifest.status == "blocked"
    assert manifest.evidence.pack_id == pack.pack_id
    assert manifest.evidence.evidence_ids == (pack.items[0].evidence_id,)
    assert manifest.evidence.item_count == 1
    assert manifest.policy.policy_version == AGENT_AUTHORIZATION_POLICY_VERSION
    assert manifest.policy.default_decision == "deny"
    assert manifest.approval == AgentApprovalObservation()
    assert manifest.authorization.passed_check_count == 3
    assert manifest.release.status == "not_supplied"
    assert manifest.authorization.blocked_check_count == 8
    assert manifest.authorization.blocking_reasons == (
        "model_adapter_not_selected",
        "live_relationship_benchmark_incomplete",
        "grounded_answer_evaluation_missing",
        "agent_trajectory_evaluation_missing",
        "trajectory_drift_evidence_missing",
        "release_thresholds_not_met",
        "human_release_not_granted",
        "execution_disabled",
    )
    assert manifest.request.capabilities.network_access is False
    assert manifest.request.capabilities.tool_access is False
    assert manifest.request.capabilities.external_side_effects is False
    assert manifest.execution.agent_model_invoked is False
    assert manifest.execution.agent_network_accessed is False
    assert manifest.execution.agent_tools_invoked is False
    assert manifest.execution.answer_generated is False
    assert manifest.execution.agent_side_effects_performed is False
    assert manifest.caveat == AGENT_RUN_CAVEAT


def test_manifest_identity_is_deterministic_independently_verifiable_and_pack_sensitive() -> None:
    query = SearchQuery(text="dangerous storm", limit=5, candidate_limit=20)
    manifest = build_agent_run_manifest(build_evidence_pack(_search_result(), query))
    repeated = build_agent_run_manifest(build_evidence_pack(_search_result(), query))
    changed = build_agent_run_manifest(
        build_evidence_pack(_search_result("Title: Revised warning"), query)
    )
    identity = asdict(manifest)
    identity.pop("manifest_id")

    assert manifest == repeated
    assert manifest.manifest_id == repeated.manifest_id
    assert manifest.manifest_id == f"manifest-{canonical_json_sha256(identity)}"
    assert manifest.manifest_id.startswith("manifest-") and len(manifest.manifest_id) == 73
    assert manifest.manifest_id != changed.manifest_id


def test_no_evidence_adds_an_explicit_block_without_changing_no_execution_state() -> None:
    result = SearchResult(
        hits=(),
        candidates_considered=0,
        embedding_model="test/local-embedding",
        ranking_mode="hybrid",
        ranking_rule=RANKING_RULE,
        caveat=RETRIEVAL_CAVEAT,
    )
    pack = build_evidence_pack(result, SearchQuery(text="missing signal"))

    manifest = build_agent_run_manifest(pack)

    assert manifest.evidence.pack_status == "no_traceable_evidence"
    assert manifest.authorization.passed_check_count == 2
    assert manifest.authorization.blocked_check_count == 9
    assert manifest.authorization.blocking_reasons[0] == "no_traceable_evidence"
    assert manifest.authorization.checks[1].status == "blocked"
    assert manifest.execution.status == "not_started"


def test_active_approval_clears_only_the_human_gate_and_never_execution() -> None:
    pack = build_evidence_pack(
        _search_result(),
        SearchQuery(text="dangerous storm", limit=5, candidate_limit=20),
    )
    proposal = build_agent_run_proposal(pack)
    issued_at = datetime(2026, 9, 14, 8, tzinfo=UTC)
    approval = AgentApprovalObservation(
        status="active",
        approval_id=f"approval-{'a' * 64}",
        approved_proposal_id=proposal.proposal_id,
        source_manifest_id=f"manifest-{'b' * 64}",
        approver_id="github:12345",
        signing_key_id=f"ed25519-{'c' * 64}",
        issued_at=issued_at,
        expires_at=issued_at.replace(hour=9),
        evaluated_at=issued_at.replace(minute=30),
    )

    manifest = build_agent_run_manifest(pack, proposal=proposal, approval=approval)
    human_release = next(
        check for check in manifest.authorization.checks if check.check_id == "human_release"
    )

    assert manifest.status == "blocked"
    assert human_release.status == "passed"
    assert manifest.authorization.passed_check_count == 4
    assert manifest.authorization.blocked_check_count == 7
    assert "human_release_not_granted" not in manifest.authorization.blocking_reasons
    assert manifest.authorization.blocking_reasons[-1] == "execution_disabled"
    assert manifest.execution.status == "not_started"


def test_eligible_release_evidence_clears_only_quality_gates_and_changes_scope() -> None:
    pack = build_evidence_pack(
        _search_result(),
        SearchQuery(text="dangerous storm", limit=5, candidate_limit=20),
    )
    release = AgentReleaseObservation(
        status="eligible_for_human_review",
        assessment_id=f"release-assessment-{'a' * 20}",
        assessment_sha256="b" * 64,
        policy_id=f"release-policy-{'c' * 20}",
        policy_sha256="d" * 64,
        agent_candidate_id="qwen-grounded-agent-v1",
        relationship_report_id="relationship-live-v1-candidate",
        trajectory_report_ids=(f"trajectory-report-{'e' * 20}",),
        model_adapter_evaluated=True,
        relationship_benchmark_passed=True,
        grounded_answer_evaluation_passed=True,
        agent_trajectory_evaluation_passed=True,
        trajectory_drift_monitoring_passed=True,
        release_threshold_policy_passed=True,
    )

    proposal = build_agent_run_proposal(pack, release=release)
    manifest = build_agent_run_manifest(pack, proposal=proposal, release=release)
    checks = {check.check_id: check for check in manifest.authorization.checks}

    assert proposal.proposal_id != build_agent_run_proposal(pack).proposal_id
    assert manifest.release == release
    assert manifest.authorization.passed_check_count == 9
    assert manifest.authorization.blocked_check_count == 2
    assert checks["release_threshold_policy"].status == "passed"
    assert checks["human_release"].status == "blocked"
    assert checks["execution_release"].status == "blocked"
    assert manifest.status == "blocked"
    assert manifest.execution.status == "not_started"


def test_release_observation_fails_closed_on_incomplete_eligibility() -> None:
    pack = build_evidence_pack(_search_result(), SearchQuery(text="dangerous storm"))
    with pytest.raises(ValueError, match="complete assessment identity"):
        build_agent_run_proposal(
            pack,
            release=AgentReleaseObservation(status="eligible_for_human_review"),
        )


@pytest.mark.parametrize(
    ("approval_status", "blocking_reason"),
    [
        ("not_yet_valid", "human_release_not_yet_valid"),
        ("expired", "human_release_expired"),
        ("revoked", "human_release_revoked"),
        ("scope_mismatch", "human_release_scope_mismatch"),
        ("untrusted_signer", "human_release_untrusted"),
        ("ledger_invalid", "approval_ledger_invalid"),
        ("ledger_unavailable", "approval_ledger_unavailable"),
    ],
)
def test_approval_failure_state_is_preserved_as_a_specific_block(
    approval_status: AgentApprovalStatus,
    blocking_reason: str,
) -> None:
    pack = build_evidence_pack(_search_result(), SearchQuery(text="dangerous storm"))
    manifest = build_agent_run_manifest(
        pack,
        approval=AgentApprovalObservation(status=approval_status),
    )
    human_release = next(
        check for check in manifest.authorization.checks if check.check_id == "human_release"
    )

    assert human_release.observed == approval_status
    assert human_release.blocking_reason == blocking_reason


def test_manifest_rejects_a_proposal_from_a_different_pack() -> None:
    original = build_evidence_pack(_search_result(), SearchQuery(text="dangerous storm"))
    changed = build_evidence_pack(
        _search_result("Title: Revised warning"), SearchQuery(text="dangerous storm")
    )
    with pytest.raises(ValueError, match="proposal does not match"):
        build_agent_run_manifest(original, proposal=build_agent_run_proposal(changed))


def test_manifest_rejects_an_incomplete_or_stale_active_approval() -> None:
    pack = build_evidence_pack(_search_result(), SearchQuery(text="dangerous storm"))
    proposal = build_agent_run_proposal(pack)
    with pytest.raises(ValueError, match="incomplete"):
        build_agent_run_manifest(pack, approval=AgentApprovalObservation(status="active"))

    issued_at = datetime(2026, 9, 14, 8, tzinfo=UTC)
    stale = AgentApprovalObservation(
        status="active",
        approval_id=f"approval-{'a' * 64}",
        approved_proposal_id=proposal.proposal_id,
        source_manifest_id=f"manifest-{'b' * 64}",
        approver_id="github:12345",
        signing_key_id=f"ed25519-{'c' * 64}",
        issued_at=issued_at,
        expires_at=issued_at.replace(hour=9),
        evaluated_at=issued_at.replace(hour=10),
    )
    with pytest.raises(ValueError, match="outside its lifetime"):
        build_agent_run_manifest(pack, approval=stale)


def test_blocked_check_requires_a_machine_readable_reason() -> None:
    with pytest.raises(ValueError, match="require a blocking reason"):
        _check(
            "model_adapter",
            passed=False,
            observed="not_selected",
            required="evaluated_model_adapter",
        )


def test_canonical_identity_rejects_unsupported_values() -> None:
    with pytest.raises(TypeError, match="cannot canonicalize object"):
        canonical_json_sha256(object())

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
    _check,
    build_agent_run_manifest,
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
    assert manifest.status == "blocked"
    assert manifest.evidence.pack_id == pack.pack_id
    assert manifest.evidence.evidence_ids == (pack.items[0].evidence_id,)
    assert manifest.evidence.item_count == 1
    assert manifest.policy.policy_version == AGENT_AUTHORIZATION_POLICY_VERSION
    assert manifest.policy.default_decision == "deny"
    assert manifest.authorization.passed_check_count == 3
    assert manifest.authorization.blocked_check_count == 5
    assert manifest.authorization.blocking_reasons == (
        "model_adapter_not_selected",
        "live_relationship_benchmark_incomplete",
        "grounded_answer_evaluation_missing",
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
    assert manifest.authorization.blocked_check_count == 6
    assert manifest.authorization.blocking_reasons[0] == "no_traceable_evidence"
    assert manifest.authorization.checks[1].status == "blocked"
    assert manifest.execution.status == "not_started"


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

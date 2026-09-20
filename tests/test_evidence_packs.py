"""Content-addressed, fail-closed agent evidence-pack tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from agent_rag_core import Event

from atlas_pulse.evidence_packs import (
    EVIDENCE_PACK_CAVEAT,
    EVIDENCE_PACK_IDENTITY_ALGORITHM,
    EVIDENCE_PACK_RULE_VERSION,
    EVIDENCE_PACK_SCHEMA_VERSION,
    EVIDENCE_PACK_TRUST_BOUNDARY,
    EvidencePackBudget,
    build_evidence_pack,
)
from atlas_pulse.retrieval import (
    CitationStatus,
    CitationValidation,
    RankingExplanation,
    SearchHit,
    SearchQuery,
    SearchResult,
    document_hash,
)
from atlas_pulse.retrieval.ranking import RANKING_RULE, RETRIEVAL_CAVEAT
from atlas_pulse.streams import StreamMessage


def _hit(
    event_id: str,
    text: str,
    *,
    citation_status: CitationStatus = "traceable",
    source: str = "nws",
) -> SearchHit:
    event = Event(
        event_id=event_id,
        event_type="weather.alert",
        source=source,
        occurred_at=datetime(2026, 9, 13, 12, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 13, 12, 1, tzinfo=UTC),
        payload={"title": event_id},
    )
    citation = CitationValidation(
        status=citation_status,
        url=f"https://example.org/{event_id}" if citation_status == "traceable" else None,
        source_field="source_url" if citation_status == "traceable" else None,
        reasons=("public_http_url",) if citation_status == "traceable" else (citation_status,),
    )
    return SearchHit(
        message=StreamMessage(stream_id=f"100-{event_id[-1]}", event=event),
        document_text=text,
        distance_km=None,
        ranking=RankingExplanation(
            lexical_rank=1,
            lexical_score=0.8,
            dense_rank=2,
            dense_similarity=0.7,
            rrf_score=0.9,
            exact_phrase_match=False,
            token_coverage=0.5,
            rerank_score=0.9,
        ),
        citation=citation,
    )


def _result(*hits: SearchHit) -> SearchResult:
    return SearchResult(
        hits=hits,
        candidates_considered=len(hits) + 3,
        embedding_model="test/local-embedding",
        ranking_mode="hybrid",
        ranking_rule=RANKING_RULE,
        caveat=RETRIEVAL_CAVEAT,
    )


def test_pack_preserves_rank_fails_closed_and_applies_both_text_budgets() -> None:
    result = _result(
        _hit("event-1", "ABCDEFGHIJ"),
        _hit("event-2", "must not leak", citation_status="missing"),
        _hit("event-3", "klmnop"),
        _hit("event-4", "also withheld", citation_status="rejected"),
        _hit("event-5", "item cap"),
    )
    query = SearchQuery(text="dangerous storm", limit=5, candidate_limit=20)
    budget = EvidencePackBudget(
        max_items=2,
        max_characters_per_item=5,
        max_total_characters=8,
    )

    pack = build_evidence_pack(result, query, budget=budget)

    assert pack.schema_version == EVIDENCE_PACK_SCHEMA_VERSION
    assert pack.rule_version == EVIDENCE_PACK_RULE_VERSION
    assert pack.identity_algorithm == EVIDENCE_PACK_IDENTITY_ALGORITHM
    assert pack.status == "traceable_evidence_available"
    assert [item.retrieval_rank for item in pack.items] == [1, 3]
    assert [item.text for item in pack.items] == ["ABCDE", "klm"]
    assert all(item.truncated for item in pack.items)
    assert pack.source_text_characters == 8
    assert [item.source for item in pack.items] == ["nws", "nws"]
    assert [exclusion.reason for exclusion in pack.exclusions] == [
        "citation_missing",
        "citation_rejected",
        "item_limit",
    ]
    assert pack.exclusions[0].ranking.lexical_rank == 1
    assert pack.exclusions[0].event_type == "weather.alert"
    assert pack.items[0].ingested_at == datetime(2026, 9, 13, 12, 1, tzinfo=UTC)
    assert "must not leak" not in repr(pack)
    assert pack.trust_boundary == EVIDENCE_PACK_TRUST_BOUNDARY
    assert pack.caveat == EVIDENCE_PACK_CAVEAT


def test_total_budget_excludes_later_traceable_hits_and_counts_unicode_code_points() -> None:
    first = _hit("event-1", "é🌍x")
    second = _hit("event-2", "later")

    pack = build_evidence_pack(
        _result(first, second),
        SearchQuery(text="unicode evidence", limit=2, candidate_limit=10),
        budget=EvidencePackBudget(
            max_items=5,
            max_characters_per_item=8_000,
            max_total_characters=2,
        ),
    )

    assert pack.items[0].text == "é🌍"
    assert pack.items[0].text_characters == 2
    assert pack.items[0].document_characters == 3
    assert pack.items[0].document_sha256 == document_hash("é🌍x")
    assert pack.items[0].text_sha256 == document_hash("é🌍")
    assert pack.exclusions[0].reason == "source_text_character_limit"
    assert pack.exclusions[0].document_sha256 == document_hash("later")


def test_pack_and_evidence_ids_are_deterministic_and_sensitive_to_exact_snapshot() -> None:
    first = _hit("event-1", "one")
    second = _hit("event-2", "two")
    query = SearchQuery(text="exact snapshot", limit=2, candidate_limit=10)

    pack = build_evidence_pack(_result(first, second), query)
    repeated = build_evidence_pack(_result(first, second), query)
    reordered = build_evidence_pack(_result(second, first), query)
    changed_budget = build_evidence_pack(
        _result(first, second),
        query,
        budget=EvidencePackBudget(max_items=1),
    )
    constrained = build_evidence_pack(
        _result(first, second),
        replace(query, min_magnitude=5, max_depth_km=70, tsunami=True),
    )

    assert pack == repeated
    assert pack.pack_id == repeated.pack_id
    assert pack.pack_id.startswith("pack-") and len(pack.pack_id) == 69
    assert pack.pack_id != reordered.pack_id
    assert pack.pack_id != changed_budget.pack_id
    assert pack.pack_id != constrained.pack_id
    ids = {item.event_id: item.evidence_id for item in pack.items}
    reordered_ids = {item.event_id: item.evidence_id for item in reordered.items}
    assert ids == reordered_ids
    assert all(value.startswith("evidence-") and len(value) == 73 for value in ids.values())


def test_empty_and_untraceable_results_return_explicit_no_evidence_packs() -> None:
    query = SearchQuery(text="no evidence")
    empty = build_evidence_pack(_result(), query)
    invalid_traceable = replace(
        _hit("event-3", "incomplete citation"),
        citation=CitationValidation(
            status="traceable",
            url=None,
            source_field=None,
            reasons=("invalid_test_contract",),
        ),
    )
    withheld = build_evidence_pack(
        _result(
            _hit("event-1", "", citation_status="traceable"),
            _hit("event-2", "unsafe", citation_status="rejected"),
            invalid_traceable,
        ),
        query,
    )

    assert empty.status == "no_traceable_evidence"
    assert empty.items == ()
    assert empty.exclusions == ()
    assert withheld.status == "no_traceable_evidence"
    assert withheld.items == ()
    assert [item.reason for item in withheld.exclusions] == [
        "empty_source_text",
        "citation_rejected",
        "citation_contract_invalid",
    ]
    assert "unsafe" not in repr(withheld)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_items": 0}, "max_items"),
        ({"max_items": 51}, "max_items"),
        ({"max_characters_per_item": 0}, "max_characters_per_item"),
        ({"max_characters_per_item": 8_001}, "max_characters_per_item"),
        ({"max_total_characters": 0}, "max_total_characters"),
        ({"max_total_characters": 64_001}, "max_total_characters"),
    ],
)
def test_budget_rejects_unbounded_values(kwargs: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        EvidencePackBudget(**kwargs)

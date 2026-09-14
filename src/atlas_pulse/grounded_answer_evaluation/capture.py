"""Capture gold-free grounded-answer tasks from deployed evidence packs."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from atlas_pulse.api import (
    CitationResponse,
    EvidencePackResponse,
    RankingResponse,
    SearchParametersResponse,
    TraceableCitationResponse,
)
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.evidence_packs import (
    EvidencePack,
    EvidencePackBudget,
    EvidencePackExclusion,
    EvidencePackItem,
    EvidencePackRetrieval,
    validate_evidence_pack_identity,
)
from atlas_pulse.grounded_answer_evaluation.base import (
    GroundedAnswerBenchmark,
    GroundedAnswerEvidence,
    GroundedAnswerTask,
    GroundedAnswerTaskCase,
    grounded_answer_case_sha256,
    grounded_answer_task_sha256,
)
from atlas_pulse.projections import GeoBounds
from atlas_pulse.retrieval import (
    CitationValidation,
    GeoRadius,
    RankingExplanation,
    SearchQuery,
)


def _safe_endpoint(value: str) -> str:
    parsed = urlsplit(value.strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an absolute HTTP(S) endpoint")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base URL must not contain embedded credentials")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _request_parameters(
    benchmark: GroundedAnswerBenchmark,
    query_index: int,
) -> dict[str, str | int | float]:
    query = benchmark.queries[query_index]
    filters = query.filters
    parameters: dict[str, str | int | float] = {
        "q": query.question,
        "retrieval_limit": benchmark.retrieval_limit,
        "candidate_limit": benchmark.candidate_limit,
        "max_items": benchmark.max_items,
        "max_characters_per_item": benchmark.max_characters_per_item,
        "max_total_characters": benchmark.max_total_characters,
        "active_only": str(filters.active_only).lower(),
        "ranking_mode": "hybrid",
    }
    if filters.source is not None:
        parameters["source"] = filters.source
    if filters.occurred_after is not None:
        parameters["occurred_after"] = filters.occurred_after.isoformat()
    if filters.occurred_before is not None:
        parameters["occurred_before"] = filters.occurred_before.isoformat()
    if filters.bbox is not None:
        parameters["bbox"] = ",".join(format(value, "g") for value in filters.bbox)
    if filters.near is not None:
        parameters["near"] = ",".join(format(value, "g") for value in filters.near)
        parameters["radius_km"] = filters.radius_km
    return parameters


def _expected_parameters(
    benchmark: GroundedAnswerBenchmark,
    query_index: int,
) -> SearchParametersResponse:
    query = benchmark.queries[query_index]
    production = query.filters.search_query(
        text=query.question,
        limit=benchmark.retrieval_limit,
        candidate_limit=benchmark.candidate_limit,
        ranking_mode="hybrid",
    )
    bounds = production.bounds
    near = production.near
    return SearchParametersResponse(
        query=production.text,
        limit=production.limit,
        candidate_limit=production.candidate_limit,
        source=production.source,
        occurred_after=production.occurred_after,
        occurred_before=production.occurred_before,
        active_only=production.active_only,
        bbox=(bounds.west, bounds.south, bounds.east, bounds.north) if bounds else None,
        near=(near.longitude, near.latitude) if near else None,
        radius_km=near.radius_km if near else None,
        ranking_mode=production.ranking_mode,
    )


def _ranking(value: RankingResponse) -> RankingExplanation:
    return RankingExplanation(**value.model_dump(mode="python"))


def _citation(value: CitationResponse | TraceableCitationResponse) -> CitationValidation:
    return CitationValidation(**value.model_dump(mode="python"))


def _portable_pack(
    result: EvidencePackResponse, expected: SearchParametersResponse
) -> EvidencePack:
    """Rebuild the core value object and independently validate all content identities."""
    if result.retrieval.parameters != expected:
        raise RuntimeError("evidence-pack API did not echo the exact requested query and filters")
    if result.item_count != len(result.items) or result.exclusion_count != len(result.exclusions):
        raise RuntimeError("evidence-pack API returned inconsistent item counts")
    if result.retrieval.returned_hits != result.item_count + result.exclusion_count:
        raise RuntimeError("evidence-pack API did not account for every returned retrieval hit")
    budget = EvidencePackBudget(
        max_items=result.budget.max_items,
        max_characters_per_item=result.budget.max_characters_per_item,
        max_total_characters=result.budget.max_total_characters,
    )
    retrieval = EvidencePackRetrieval(
        query=_search_query(expected),
        candidates_considered=result.retrieval.candidates_considered,
        returned_hits=result.retrieval.returned_hits,
        embedding_model=result.retrieval.embedding_model,
        ranking_mode=result.retrieval.ranking_mode,
        ranking_rule=result.retrieval.ranking_rule,
        caveat=result.retrieval.caveat,
    )
    items = tuple(
        EvidencePackItem(
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
            ranking=_ranking(item.ranking),
            citation=_citation(item.citation),
        )
        for item in result.items
    )
    exclusions = tuple(
        EvidencePackExclusion(
            retrieval_rank=item.retrieval_rank,
            stream_id=item.stream_id,
            event_id=item.event_id,
            event_type=item.event_type,
            source=item.source,
            occurred_at=item.occurred_at,
            ingested_at=item.ingested_at,
            event_schema_version=item.event_schema_version,
            document_sha256=item.document_sha256,
            distance_km=item.distance_km,
            ranking=_ranking(item.ranking),
            citation=_citation(item.citation),
            reason=item.reason,
        )
        for item in result.exclusions
    )
    pack = EvidencePack(
        pack_id=result.pack_id,
        schema_version=result.schema_version,
        rule_version=result.rule_version,
        identity_algorithm=result.identity_algorithm,
        status=result.status,
        budget=budget,
        retrieval=retrieval,
        items=items,
        exclusions=exclusions,
        source_text_characters=result.source_text_characters,
        trust_boundary=result.trust_boundary,
        caveat=result.caveat,
    )
    validate_evidence_pack_identity(pack)
    return pack


def _search_query(parameters: SearchParametersResponse) -> SearchQuery:
    """Recreate the production query represented by the API echo."""
    near: GeoRadius | None = None
    if parameters.near is not None:
        if parameters.radius_km is None:
            raise RuntimeError("evidence-pack API returned a near filter without radius_km")
        near = GeoRadius(
            longitude=parameters.near[0],
            latitude=parameters.near[1],
            radius_km=parameters.radius_km,
        )
    return SearchQuery(
        text=parameters.query,
        limit=parameters.limit,
        candidate_limit=parameters.candidate_limit,
        source=parameters.source,
        occurred_after=parameters.occurred_after,
        occurred_before=parameters.occurred_before,
        bounds=GeoBounds(*parameters.bbox) if parameters.bbox is not None else None,
        near=near,
        active_only=parameters.active_only,
        ranking_mode=parameters.ranking_mode,
    )


def _task_case(
    query_id: str, question: str, slices: tuple[str, ...], pack: EvidencePack
) -> GroundedAnswerTaskCase:
    evidence = tuple(
        GroundedAnswerEvidence(
            evidence_id=item.evidence_id,
            retrieval_rank=item.retrieval_rank,
            stream_id=item.stream_id,
            event_id=item.event_id,
            event_type=item.event_type,
            source=item.source,
            occurred_at=item.occurred_at,
            text=item.text,
            document_sha256=item.document_sha256,
            text_sha256=item.text_sha256,
            truncated=item.truncated,
            citation_url=item.citation.url or "",
        )
        for item in pack.items
    )
    draft = GroundedAnswerTaskCase.model_construct(
        case_id="answer-case-" + "0" * 20,
        query_id=query_id,
        question=question,
        slices=slices,
        pack_id=pack.pack_id,
        pack_status=pack.status,
        pack_rule_version="retrieval-evidence-pack-v1",
        evidence=evidence,
        exclusion_count=len(pack.exclusions),
        source_text_characters=pack.source_text_characters,
    )
    digest = grounded_answer_case_sha256(draft)
    return GroundedAnswerTaskCase(
        case_id=f"answer-case-{digest[:20]}",
        query_id=query_id,
        question=question,
        slices=slices,
        pack_id=pack.pack_id,
        pack_status=pack.status,
        evidence=evidence,
        exclusion_count=len(pack.exclusions),
        source_text_characters=pack.source_text_characters,
    )


async def capture_grounded_answer_task(
    benchmark: GroundedAnswerBenchmark,
    *,
    base_url: str,
    client: httpx.AsyncClient | None = None,
    captured_at: datetime | None = None,
) -> GroundedAnswerTask:
    """Capture one exact evidence pack per question without generating an answer."""
    endpoint = _safe_endpoint(base_url)
    timestamp = captured_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("grounded-answer capture timestamp must be timezone-aware")
    benchmark_hash = canonical_sha256(benchmark)
    owns_client = client is None
    active_client = client or httpx.AsyncClient(base_url=endpoint, timeout=30.0)
    cases: list[GroundedAnswerTaskCase] = []
    try:
        for index, query in enumerate(benchmark.queries):
            response = await active_client.get(
                "/v1/evidence-packs",
                params=_request_parameters(benchmark, index),
            )
            response.raise_for_status()
            result = EvidencePackResponse.model_validate(response.json())
            expected = _expected_parameters(benchmark, index)
            if (
                result.budget.max_items != benchmark.max_items
                or result.budget.max_characters_per_item != benchmark.max_characters_per_item
                or result.budget.max_total_characters != benchmark.max_total_characters
            ):
                raise RuntimeError("evidence-pack API changed the requested source-text budget")
            pack = _portable_pack(result, expected)
            cases.append(_task_case(query.query_id, query.question, query.slices, pack))
    finally:
        if owns_client:
            await active_client.aclose()

    ordered = tuple(sorted(cases, key=lambda item: item.case_id))
    draft = GroundedAnswerTask.model_construct(
        schema_version="1.0.0",
        task_id="grounded-task-" + "0" * 20,
        task_sha256="0" * 64,
        benchmark_id=benchmark.benchmark_id,
        benchmark_sha256=benchmark_hash,
        captured_at=timestamp,
        endpoint=endpoint,
        case_count=len(ordered),
        cases=ordered,
    )
    digest = grounded_answer_task_sha256(draft)
    return GroundedAnswerTask(
        task_id=f"grounded-task-{digest[:20]}",
        task_sha256=digest,
        benchmark_id=benchmark.benchmark_id,
        benchmark_sha256=benchmark_hash,
        captured_at=timestamp,
        endpoint=endpoint,
        case_count=len(ordered),
        cases=ordered,
    )

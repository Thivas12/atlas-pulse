"""Deterministic rank fusion, lightweight reranking, and evidence attachment."""

import re
from dataclasses import dataclass

from atlas_pulse.retrieval.base import (
    CandidateBatch,
    ChannelCandidate,
    RankingExplanation,
    RankingMode,
    SearchHit,
)
from atlas_pulse.retrieval.document import validate_event_citation

RANKING_RULE = "rrf60-transparent-rerank-v1"
RANKING_RULES: dict[RankingMode, str] = {
    "lexical": "postgres-english-fts-v1",
    "dense": "bge-cosine-hnsw-v1",
    "rrf": "rrf60-v1",
    "hybrid": RANKING_RULE,
}
RRF_K = 60
RETRIEVAL_CAVEAT = (
    "Results are ranked source events, not a generated answer. A traceable citation validates "
    "URL structure and event identity only; it does not independently verify the source claim."
)
_TOKEN = re.compile(r"[\w'-]+", flags=re.UNICODE)


@dataclass(slots=True)
class _MergedCandidate:
    candidate: ChannelCandidate
    lexical_rank: int | None = None
    lexical_score: float | None = None
    dense_rank: int | None = None
    dense_similarity: float | None = None


def _tokens(value: str) -> frozenset[str]:
    return frozenset(token.casefold() for token in _TOKEN.findall(value) if len(token) > 1)


def _merge(batch: CandidateBatch) -> dict[tuple[str, str], _MergedCandidate]:
    merged: dict[tuple[str, str], _MergedCandidate] = {}
    for channel, candidates in (("lexical", batch.lexical), ("dense", batch.dense)):
        for candidate in candidates:
            key = (candidate.message.event.source, candidate.message.event.event_id)
            current = merged.setdefault(key, _MergedCandidate(candidate=candidate))
            if channel == "lexical":
                current.lexical_rank = candidate.rank
                current.lexical_score = candidate.score
            else:
                current.dense_rank = candidate.rank
                current.dense_similarity = candidate.score
            if current.candidate.distance_km is None and candidate.distance_km is not None:
                current.candidate = candidate
    return merged


def rank_candidates(
    batch: CandidateBatch,
    *,
    query_text: str,
    limit: int,
    mode: RankingMode = "hybrid",
) -> tuple[SearchHit, ...]:
    """Rank one candidate snapshot for production or evaluation ablations."""
    query_normalized = " ".join(query_text.casefold().split())
    query_tokens = _tokens(query_text)
    hits: list[SearchHit] = []
    for merged in _merge(batch).values():
        rrf_raw = sum(
            1 / (RRF_K + rank)
            for rank in (merged.lexical_rank, merged.dense_rank)
            if rank is not None
        )
        rrf_score = rrf_raw / (2 / (RRF_K + 1))
        document_normalized = " ".join(merged.candidate.document_text.casefold().split())
        exact_phrase = query_normalized in document_normalized
        document_tokens = _tokens(merged.candidate.document_text)
        token_coverage = (
            len(query_tokens & document_tokens) / len(query_tokens) if query_tokens else 0.0
        )
        rerank_score = 0.85 * rrf_score + 0.10 * token_coverage + 0.05 * int(exact_phrase)
        hits.append(
            SearchHit(
                message=merged.candidate.message,
                document_text=merged.candidate.document_text,
                distance_km=merged.candidate.distance_km,
                ranking=RankingExplanation(
                    lexical_rank=merged.lexical_rank,
                    lexical_score=merged.lexical_score,
                    dense_rank=merged.dense_rank,
                    dense_similarity=merged.dense_similarity,
                    rrf_score=round(rrf_score, 8),
                    exact_phrase_match=exact_phrase,
                    token_coverage=round(token_coverage, 8),
                    rerank_score=round(rerank_score, 8),
                ),
                citation=validate_event_citation(merged.candidate.message.event),
            )
        )
    if mode == "lexical":
        hits = [hit for hit in hits if hit.ranking.lexical_rank is not None]
        hits.sort(
            key=lambda hit: (
                hit.ranking.lexical_rank or 0,
                hit.message.event.source,
                hit.message.event.event_id,
            )
        )
    elif mode == "dense":
        hits = [hit for hit in hits if hit.ranking.dense_rank is not None]
        hits.sort(
            key=lambda hit: (
                hit.ranking.dense_rank or 0,
                hit.message.event.source,
                hit.message.event.event_id,
            )
        )
    elif mode == "rrf":
        hits.sort(
            key=lambda hit: (
                -hit.ranking.rrf_score,
                hit.message.event.source,
                hit.message.event.event_id,
            )
        )
    else:
        hits.sort(
            key=lambda hit: (
                -hit.ranking.rerank_score,
                -hit.ranking.rrf_score,
                hit.message.event.source,
                hit.message.event.event_id,
            )
        )
    return tuple(hits[:limit])


def fuse_and_rerank(
    batch: CandidateBatch,
    *,
    query_text: str,
    limit: int,
) -> tuple[SearchHit, ...]:
    """Preserve the original hybrid-ranking API for existing consumers."""
    return rank_candidates(batch, query_text=query_text, limit=limit, mode="hybrid")

"""Evidence-preserving hybrid retrieval public API."""

from atlas_pulse.retrieval.base import (
    CandidateBatch,
    ChannelCandidate,
    CitationValidation,
    Embedding,
    EmbeddingProvider,
    GeoRadius,
    IndexedDocument,
    RankingExplanation,
    RetrievalStore,
    SearchHit,
    SearchQuery,
    SearchResult,
    SearchService,
)
from atlas_pulse.retrieval.document import (
    document_hash,
    render_event_document,
    validate_event_citation,
)
from atlas_pulse.retrieval.embedding import FastEmbedProvider
from atlas_pulse.retrieval.postgres import PostgresRetrievalStore
from atlas_pulse.retrieval.ranking import (
    RANKING_RULE,
    RETRIEVAL_CAVEAT,
    fuse_and_rerank,
)
from atlas_pulse.retrieval.service import (
    HybridSearchService,
    IndexingCycle,
    RetrievalIndexerService,
)

__all__ = [
    "RANKING_RULE",
    "RETRIEVAL_CAVEAT",
    "CandidateBatch",
    "ChannelCandidate",
    "CitationValidation",
    "Embedding",
    "EmbeddingProvider",
    "FastEmbedProvider",
    "GeoRadius",
    "HybridSearchService",
    "IndexedDocument",
    "IndexingCycle",
    "PostgresRetrievalStore",
    "RankingExplanation",
    "RetrievalIndexerService",
    "RetrievalStore",
    "SearchHit",
    "SearchQuery",
    "SearchResult",
    "SearchService",
    "document_hash",
    "fuse_and_rerank",
    "render_event_document",
    "validate_event_citation",
]

"""Restart-safe retrieval indexing and transparent hybrid search orchestration."""

from dataclasses import asdict, dataclass

import structlog
from opentelemetry import metrics, trace

from atlas_pulse.retrieval.base import (
    EmbeddingProvider,
    IndexedDocument,
    RetrievalStore,
    SearchQuery,
    SearchResult,
)
from atlas_pulse.retrieval.document import document_hash, render_event_document
from atlas_pulse.retrieval.ranking import (
    RANKING_RULES,
    RETRIEVAL_CAVEAT,
    rank_candidates,
)
from atlas_pulse.streams import EventBus

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)
indexed_counter = meter.create_counter(
    "atlas.retrieval.indexed_documents",
    description="Event documents committed to the hybrid retrieval index",
)
search_counter = meter.create_counter(
    "atlas.retrieval.searches",
    description="Hybrid retrieval requests completed",
)


@dataclass(frozen=True, slots=True)
class IndexingCycle:
    """Auditable outcome of one bounded retrieval-index cycle."""

    projection_name: str
    previous_checkpoint: str | None
    checkpoint: str | None
    indexed_documents: int


class RetrievalIndexerService:
    """Render and embed strictly after an atomically committed checkpoint."""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        store: RetrievalStore,
        embedder: EmbeddingProvider,
        projection_name: str,
        batch_size: int,
    ) -> None:
        self._event_bus = event_bus
        self._store = store
        self._embedder = embedder
        self._projection_name = projection_name
        self._batch_size = batch_size

    async def index_once(self) -> IndexingCycle:
        previous = await self._store.checkpoint(self._projection_name)
        messages = await self._event_bus.replay(after=previous, limit=self._batch_size)
        if not messages:
            result = IndexingCycle(
                projection_name=self._projection_name,
                previous_checkpoint=previous,
                checkpoint=previous,
                indexed_documents=0,
            )
            await logger.adebug("retrieval_index_cycle_idle", **asdict(result))
            return result

        with tracer.start_as_current_span("retrieval.index_once") as span:
            texts = tuple(render_event_document(message.event) for message in messages)
            embeddings = await self._embedder.embed_documents(texts)
            if len(embeddings) != len(messages):
                raise RuntimeError("embedding count must match the stream batch")
            documents = tuple(
                IndexedDocument(
                    message=message,
                    text=document_text,
                    document_hash=document_hash(document_text),
                    embedding_model=self._embedder.model_name,
                    embedding=embedding,
                )
                for message, document_text, embedding in zip(
                    messages, texts, embeddings, strict=True
                )
            )
            await self._store.index(
                projection_name=self._projection_name,
                documents=documents,
            )
            checkpoint = messages[-1].stream_id
            indexed_counter.add(len(documents), {"projection": self._projection_name})
            span.set_attribute("atlas.retrieval.documents", len(documents))
            span.set_attribute("atlas.retrieval.checkpoint", checkpoint)
            result = IndexingCycle(
                projection_name=self._projection_name,
                previous_checkpoint=previous,
                checkpoint=checkpoint,
                indexed_documents=len(documents),
            )
            await logger.ainfo("retrieval_index_cycle_completed", **asdict(result))
            return result


class HybridSearchService:
    """Local dense retrieval, FTS, RRF, evidence tie-breaking, and citation validation."""

    def __init__(self, *, store: RetrievalStore, embedder: EmbeddingProvider) -> None:
        self._store = store
        self._embedder = embedder

    async def search(self, query: SearchQuery) -> SearchResult:
        with tracer.start_as_current_span("retrieval.search") as span:
            uses_lexical = query.ranking_mode != "dense"
            uses_dense = query.ranking_mode != "lexical"
            span.set_attribute("atlas.retrieval.lexical_channel", uses_lexical)
            span.set_attribute("atlas.retrieval.dense_channel", uses_dense)
            embedding = await self._embedder.embed_query(query.text) if uses_dense else None
            candidates = await self._store.candidates(
                query,
                embedding,
                embedding_model=self._embedder.model_name,
            )
            hits = rank_candidates(
                candidates,
                query_text=query.text,
                limit=query.limit,
                mode=query.ranking_mode,
            )
            candidate_count = len(
                {
                    (candidate.message.event.source, candidate.message.event.event_id)
                    for candidate in (*candidates.lexical, *candidates.dense)
                }
            )
            span.set_attribute("atlas.retrieval.candidates", candidate_count)
            span.set_attribute("atlas.retrieval.hits", len(hits))
            search_counter.add(1, {"model": self._embedder.model_name})
            return SearchResult(
                hits=hits,
                candidates_considered=candidate_count,
                embedding_model=self._embedder.model_name,
                ranking_mode=query.ranking_mode,
                ranking_rule=RANKING_RULES[query.ranking_mode],
                caveat=RETRIEVAL_CAVEAT,
            )

    async def is_ready(self) -> bool:
        return await self._store.is_ready()

    async def close(self) -> None:
        await self._store.close()

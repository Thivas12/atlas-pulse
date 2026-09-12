"""Typed contracts for evidence-preserving hybrid retrieval."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Literal, Protocol

from atlas_pulse.projections import GeoBounds
from atlas_pulse.projections.base import SourceName
from atlas_pulse.streams import StreamMessage

Embedding = tuple[float, ...]
CitationStatus = Literal["traceable", "missing", "rejected"]


@dataclass(frozen=True, slots=True)
class GeoRadius:
    """A WGS84 point and bounded search radius."""

    longitude: float
    latitude: float
    radius_km: float

    def __post_init__(self) -> None:
        if not all(isfinite(value) for value in (self.longitude, self.latitude, self.radius_km)):
            raise ValueError("near coordinates and radius must be finite")
        if not -180 <= self.longitude <= 180:
            raise ValueError("near longitude must be within [-180, 180]")
        if not -90 <= self.latitude <= 90:
            raise ValueError("near latitude must be within [-90, 90]")
        if not 0 < self.radius_km <= 2_000:
            raise ValueError("radius_km must be within (0, 2000]")


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Validated semantic, temporal, and geospatial retrieval request."""

    text: str
    limit: int = 10
    candidate_limit: int = 50
    source: SourceName | None = None
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None
    bounds: GeoBounds | None = None
    near: GeoRadius | None = None
    active_only: bool = True

    def __post_init__(self) -> None:
        normalized = " ".join(self.text.split())
        if not 2 <= len(normalized) <= 500:
            raise ValueError("query text must contain between 2 and 500 characters")
        if not 1 <= self.limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        if not self.limit <= self.candidate_limit <= 200:
            raise ValueError("candidate_limit must be between limit and 200")
        for value in (self.occurred_after, self.occurred_before):
            if value is not None and value.tzinfo is None:
                raise ValueError("time filters must include a UTC offset")
        if (
            self.occurred_after is not None
            and self.occurred_before is not None
            and self.occurred_after > self.occurred_before
        ):
            raise ValueError("occurred_after must not be later than occurred_before")
        object.__setattr__(self, "text", normalized)


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    """One event revision rendered and embedded for atomic indexing."""

    message: StreamMessage
    text: str
    document_hash: str
    embedding_model: str
    embedding: Embedding


@dataclass(frozen=True, slots=True)
class ChannelCandidate:
    """One result from either the lexical or dense retrieval channel."""

    message: StreamMessage
    document_text: str
    rank: int
    score: float
    distance_km: float | None = None


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    """Independently ranked result lists read from one database snapshot."""

    lexical: tuple[ChannelCandidate, ...]
    dense: tuple[ChannelCandidate, ...]


@dataclass(frozen=True, slots=True)
class CitationValidation:
    """Structural validation of a source-evidence link; not a factual endorsement."""

    status: CitationStatus
    url: str | None
    source_field: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RankingExplanation:
    """Every deterministic contribution to a final search rank."""

    lexical_rank: int | None
    lexical_score: float | None
    dense_rank: int | None
    dense_similarity: float | None
    rrf_score: float
    exact_phrase_match: bool
    token_coverage: float
    rerank_score: float


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A current event with transparent ranking and traceable evidence."""

    message: StreamMessage
    document_text: str
    distance_km: float | None
    ranking: RankingExplanation
    citation: CitationValidation


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Bounded hybrid-search result with explicit model and rule identity."""

    hits: tuple[SearchHit, ...]
    candidates_considered: int
    embedding_model: str
    ranking_rule: str
    caveat: str


class EmbeddingProvider(Protocol):
    """Local dense-embedding capability shared by indexing and query paths."""

    @property
    def model_name(self) -> str:
        """Return the stable model identifier."""
        ...

    @property
    def dimensions(self) -> int:
        """Return the vector width enforced by the database schema."""
        ...

    async def embed_documents(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
        """Embed document passages without a remote inference service."""
        ...

    async def embed_query(self, text: str) -> Embedding:
        """Embed one search query using model-specific query behavior."""
        ...


class RetrievalStore(Protocol):
    """Atomic retrieval-index projection and hybrid-candidate capability."""

    async def checkpoint(self, projection_name: str) -> str | None:
        """Return the last atomically indexed stream position."""
        ...

    async def index(
        self,
        *,
        projection_name: str,
        documents: tuple[IndexedDocument, ...],
    ) -> None:
        """Upsert current documents and checkpoint in one transaction."""
        ...

    async def candidates(
        self,
        query: SearchQuery,
        embedding: Embedding,
        *,
        embedding_model: str,
    ) -> CandidateBatch:
        """Return lexical and vector ranks from one consistent snapshot."""
        ...

    async def is_ready(self) -> bool:
        """Return whether the retrieval schema accepts queries."""
        ...

    async def close(self) -> None:
        """Release owned resources."""
        ...


class SearchService(Protocol):
    """Read-only capability injected into the HTTP API."""

    async def search(self, query: SearchQuery) -> SearchResult:
        """Run local embedding, retrieval, fusion, and citation validation."""
        ...

    async def is_ready(self) -> bool:
        """Return whether the durable retrieval index accepts queries."""
        ...

    async def close(self) -> None:
        """Release owned resources."""
        ...

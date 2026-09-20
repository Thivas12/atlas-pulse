"""Hybrid retrieval contracts, indexing orchestration, and ranking tests."""

from __future__ import annotations

import math
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from types import ModuleType

import pytest
from agent_rag_core import Event, GeoPoint

from atlas_pulse.projections import GeoBounds
from atlas_pulse.retrieval import (
    CandidateBatch,
    ChannelCandidate,
    CitationValidation,
    FastEmbedProvider,
    GeoRadius,
    HybridSearchService,
    IndexedDocument,
    RankingExplanation,
    RetrievalIndexerService,
    SearchHit,
    SearchQuery,
    SearchResult,
    document_hash,
    fuse_and_rerank,
    rank_candidates,
    render_event_document,
    validate_event_citation,
)
from atlas_pulse.retrieval.base import Embedding
from atlas_pulse.retrieval.document import _render_value
from atlas_pulse.retrieval.ranking import RANKING_RULE, RANKING_RULES, RETRIEVAL_CAVEAT
from atlas_pulse.streams import InMemoryEventBus, StreamMessage


def make_event(
    event_id: str,
    *,
    source: str = "usgs",
    title: str = "M 4.2 earthquake near Test City",
    source_url: object = "https://example.org/evidence/1",
    extra: dict[str, object] | None = None,
) -> Event:
    payload: dict[str, object] = {
        "title": title,
        "place": "Test City",
        "magnitude": 4.2,
        "tsunami": False,
        "source_url": source_url,
    }
    payload.update(extra or {})
    return Event(
        event_id=event_id,
        event_type="weather.alert" if source == "nws" else "seismic.earthquake",
        source=source,
        occurred_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 12, 12, 1, tzinfo=UTC),
        location=GeoPoint(latitude=12.25, longitude=77.5, altitude_km=None),
        payload=payload,
    )


def candidate(
    event: Event,
    *,
    rank: int,
    score: float,
    distance_km: float | None = None,
) -> ChannelCandidate:
    return ChannelCandidate(
        message=StreamMessage(stream_id=f"100-{event.event_id[-1]}", event=event),
        document_text=render_event_document(event),
        rank=rank,
        score=score,
        distance_km=distance_km,
    )


class StubEmbedder:
    model_name = "test/local-embedding"
    dimensions = 3

    def __init__(self, *, mismatched: bool = False) -> None:
        self.mismatched = mismatched
        self.document_calls: list[tuple[str, ...]] = []
        self.query_calls: list[str] = []

    async def embed_documents(self, texts: tuple[str, ...]) -> tuple[Embedding, ...]:
        self.document_calls.append(texts)
        vectors = tuple((1.0, 0.0, 0.0) for _text in texts)
        return vectors[:-1] if self.mismatched else vectors

    async def embed_query(self, text: str) -> Embedding:
        self.query_calls.append(text)
        return (1.0, 0.0, 0.0)


class RecordingRetrievalStore:
    def __init__(self, *, batch: CandidateBatch | None = None, ready: bool = True) -> None:
        self.checkpoint_value: str | None = None
        self.indexed: list[tuple[IndexedDocument, ...]] = []
        self.batch = batch or CandidateBatch(lexical=(), dense=())
        self.ready = ready
        self.query: SearchQuery | None = None
        self.embedding: Embedding | None = None
        self.embedding_model: str | None = None
        self.closed = False

    async def checkpoint(self, projection_name: str) -> str | None:
        assert projection_name == "test-retrieval"
        return self.checkpoint_value

    async def index(
        self,
        *,
        projection_name: str,
        documents: tuple[IndexedDocument, ...],
    ) -> None:
        assert projection_name == "test-retrieval"
        self.indexed.append(documents)
        self.checkpoint_value = documents[-1].message.stream_id

    async def candidates(
        self,
        query: SearchQuery,
        embedding: Embedding | None,
        *,
        embedding_model: str,
    ) -> CandidateBatch:
        self.query = query
        self.embedding = embedding
        self.embedding_model = embedding_model
        return self.batch

    async def is_ready(self) -> bool:
        return self.ready

    async def close(self) -> None:
        self.closed = True


def test_document_rendering_is_deterministic_bounded_and_source_neutral() -> None:
    event = make_event(
        "quake-1",
        extra={
            "description": "  Roads\n may   be affected. ",
            "instruction": ["Avoid bridges", "Follow local officials"],
            "geometry": {"type": "Polygon", "coordinates": []},
            "sender_name": "Emergency Office",
        },
    )

    rendered = render_event_document(event)

    assert rendered.startswith("Source: usgs\nEvent type: seismic.earthquake")
    assert "Coordinates: 12.250000, 77.500000" in rendered
    assert "Description: Roads may be affected." in rendered
    assert "Instruction: Avoid bridges, Follow local officials" in rendered
    assert "Tsunami flag: no" in rendered
    assert "coordinates" not in rendered
    assert document_hash(rendered) == document_hash(render_event_document(event))
    assert (
        len(render_event_document(make_event("long-1", extra={"description": "danger " * 3_000})))
        == 8_000
    )


def test_document_renderer_accepts_mapping_and_ignores_unknown_object_values() -> None:
    event = make_event(
        "mapped-1",
        extra={
            "description": {"b": 2, "a": 1},
            "severity": "",
        },
    )
    rendered = render_event_document(event)
    assert 'Description: {"a":1,"b":2}' in rendered
    assert "Severity:" not in rendered
    assert _render_value(object()) is None


def test_citation_validation_uses_the_first_safe_evidence_field() -> None:
    event = make_event(
        "citation-1",
        source_url="https://user:secret@example.org/private",
        extra={"detail_url": "https://earthquake.usgs.gov/event/citation-1"},
    )

    citation = validate_event_citation(event)

    assert citation == CitationValidation(
        status="traceable",
        url="https://earthquake.usgs.gov/event/citation-1",
        source_field="detail_url",
        reasons=("public_http_url", "source_event_identity_attached"),
    )


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("file:///tmp/evidence", "unsupported_scheme"),
        ("https://localhost/event", "non_public_hostname"),
        ("https://127.0.0.1/event", "non_public_address"),
        ("https://example.org/event?token=secret", "credential_like_query_parameter"),
        ("https://[not-an-ip/event", "malformed_url"),
        ("https:///event", "missing_hostname"),
        ("https://example.org:99999/event", "malformed_url"),
        ("https://intranet/event", "non_public_hostname"),
    ],
)
def test_citation_validation_rejects_unsafe_links(url: str, reason: str) -> None:
    citation = validate_event_citation(make_event("unsafe-1", source_url=url))
    assert citation.status == "rejected"
    assert citation.url is None
    assert f"source_url:{reason}" in citation.reasons


def test_citation_validation_reports_missing_evidence() -> None:
    citation = validate_event_citation(make_event("missing-1", source_url=None))
    assert citation.status == "missing"
    assert citation.reasons == ("no_source_evidence_url",)


def test_search_contract_normalizes_and_preserves_all_filters() -> None:
    after = datetime(2026, 9, 1, tzinfo=UTC)
    before = datetime(2026, 9, 12, tzinfo=UTC)
    query = SearchQuery(
        text="  severe   weather  ",
        limit=5,
        candidate_limit=25,
        source="nws",
        occurred_after=after,
        occurred_before=before,
        bounds=GeoBounds(west=-100, south=20, east=-80, north=40),
        near=GeoRadius(longitude=-90, latitude=30, radius_km=100),
        active_only=False,
        min_magnitude=5,
        max_depth_km=70,
        tsunami=True,
        alert_type="  Tornado   Warning ",
        min_confidence_rank=3,
        observation_period="night",
    )
    assert query.text == "severe weather"
    assert query.source == "nws"
    assert query.near is not None and query.near.radius_km == 100
    assert query.alert_type == "Tornado Warning"
    assert query.min_magnitude == 5
    assert query.max_depth_km == 70
    assert query.tsunami is True
    assert query.min_confidence_rank == 3
    assert query.observation_period == "night"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"text": "x"}, "query text"),
        ({"text": "x" * 501}, "query text"),
        ({"text": "ok", "limit": 0}, "limit"),
        ({"text": "ok", "limit": 10, "candidate_limit": 9}, "candidate_limit"),
        ({"text": "ok", "candidate_limit": 201}, "candidate_limit"),
        ({"text": "ok", "ranking_mode": "unknown"}, "ranking_mode"),
        ({"text": "ok", "min_magnitude": math.nan}, "min_magnitude"),
        ({"text": "ok", "max_depth_km": -1}, "max_depth_km"),
        ({"text": "ok", "tsunami": 1}, "tsunami"),
        ({"text": "ok", "alert_type": "   "}, "alert_type"),
        ({"text": "ok", "min_confidence_rank": 0}, "min_confidence_rank"),
        ({"text": "ok", "min_confidence_rank": True}, "min_confidence_rank"),
        ({"text": "ok", "observation_period": "dusk"}, "observation_period"),
        (
            {"text": "ok", "occurred_after": datetime(2026, 1, 1)},
            "UTC offset",
        ),
        (
            {
                "text": "ok",
                "occurred_after": datetime(2026, 2, 1, tzinfo=UTC),
                "occurred_before": datetime(2026, 1, 1, tzinfo=UTC),
            },
            "occurred_after",
        ),
    ],
)
def test_search_contract_rejects_unbounded_requests(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        SearchQuery(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"longitude": math.nan, "latitude": 0, "radius_km": 1}, "finite"),
        ({"longitude": 181, "latitude": 0, "radius_km": 1}, "longitude"),
        ({"longitude": 0, "latitude": -91, "radius_km": 1}, "latitude"),
        ({"longitude": 0, "latitude": 0, "radius_km": 0}, "radius_km"),
        ({"longitude": 0, "latitude": 0, "radius_km": 2_001}, "radius_km"),
    ],
)
def test_geo_radius_rejects_invalid_values(kwargs: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        GeoRadius(**kwargs)


def test_rrf_combines_channels_and_exposes_evidence_features() -> None:
    shared = make_event("event-1", title="Severe weather evacuation order")
    lexical_only = make_event("event-2", title="Severe weather advisory")
    dense_only = make_event("event-3", title="Residents told to leave immediately")
    batch = CandidateBatch(
        lexical=(
            candidate(shared, rank=1, score=0.8),
            candidate(lexical_only, rank=2, score=0.5),
        ),
        dense=(
            candidate(dense_only, rank=1, score=0.94, distance_km=8.0),
            candidate(shared, rank=2, score=0.91, distance_km=3.5),
        ),
    )

    hits = fuse_and_rerank(batch, query_text="severe weather", limit=3)

    assert [hit.message.event.event_id for hit in hits] == ["event-1", "event-3", "event-2"]
    first = hits[0]
    assert first.ranking.lexical_rank == 1
    assert first.ranking.dense_rank == 2
    assert first.ranking.exact_phrase_match is True
    assert first.ranking.token_coverage == 1
    assert first.distance_km == 3.5
    assert first.citation.status == "traceable"
    assert hits[1].ranking.lexical_rank is None


def test_ranking_modes_reuse_one_candidate_snapshot_for_honest_ablations() -> None:
    shared = make_event("event-1", title="Severe weather evacuation order")
    lexical_only = make_event("event-2", title="Severe weather advisory")
    dense_only = make_event("event-3", title="Residents told to leave immediately")
    batch = CandidateBatch(
        lexical=(
            candidate(shared, rank=1, score=0.8),
            candidate(lexical_only, rank=2, score=0.5),
        ),
        dense=(
            candidate(dense_only, rank=1, score=0.94),
            candidate(shared, rank=2, score=0.91),
        ),
    )

    orders = {
        mode: [
            hit.message.event.event_id
            for hit in rank_candidates(batch, query_text="severe weather", limit=3, mode=mode)
        ]
        for mode in RANKING_RULES
    }

    assert orders["lexical"] == ["event-1", "event-2"]
    assert orders["dense"] == ["event-3", "event-1"]
    assert orders["rrf"] == ["event-1", "event-3", "event-2"]
    assert orders["hybrid"] == orders["rrf"]


def test_hybrid_evidence_features_break_only_exact_rrf_ties() -> None:
    lexical_only = make_event("alpha-1", source="nws", title="Unrelated advisory")
    dense_only = make_event("beta-2", source="usgs", title="Severe weather")
    batch = CandidateBatch(
        lexical=(candidate(lexical_only, rank=1, score=0.8),),
        dense=(candidate(dense_only, rank=1, score=0.9),),
    )

    rrf = rank_candidates(batch, query_text="severe weather", limit=2, mode="rrf")
    hybrid = rank_candidates(batch, query_text="severe weather", limit=2, mode="hybrid")

    assert [hit.message.event.event_id for hit in rrf] == ["alpha-1", "beta-2"]
    assert [hit.message.event.event_id for hit in hybrid] == ["beta-2", "alpha-1"]
    assert all(hit.ranking.rerank_score == hit.ranking.rrf_score for hit in hybrid)


def test_rrf_ties_are_stable_and_empty_queries_have_zero_coverage() -> None:
    alpha = make_event("alpha-1", source="nws", title="Alpha")
    beta = make_event("beta-2", source="usgs", title="Beta")
    hits = fuse_and_rerank(
        CandidateBatch(
            lexical=(),
            dense=(candidate(beta, rank=1, score=0.5), candidate(alpha, rank=1, score=0.5)),
        ),
        query_text="--",
        limit=1,
    )
    assert hits[0].message.event.event_id == "alpha-1"
    assert hits[0].ranking.token_coverage == 0


async def test_retrieval_indexer_commits_a_bounded_rendered_batch() -> None:
    bus = InMemoryEventBus()
    for event_id in ("event-1", "event-2", "event-3"):
        await bus.publish(make_event(event_id))
    store = RecordingRetrievalStore()
    store.checkpoint_value = "0-1"
    embedder = StubEmbedder()
    service = RetrievalIndexerService(
        event_bus=bus,
        store=store,
        embedder=embedder,
        projection_name="test-retrieval",
        batch_size=2,
    )

    cycle = await service.index_once()

    assert cycle.previous_checkpoint == "0-1"
    assert cycle.checkpoint == "0-3"
    assert cycle.indexed_documents == 2
    assert len(store.indexed) == 1
    assert [doc.message.event.event_id for doc in store.indexed[0]] == ["event-2", "event-3"]
    assert store.indexed[0][0].embedding_model == embedder.model_name
    assert store.indexed[0][0].document_hash == document_hash(store.indexed[0][0].text)


async def test_retrieval_indexer_idle_and_embedding_mismatch_never_advance_checkpoint() -> None:
    idle_store = RecordingRetrievalStore()
    idle_store.checkpoint_value = "10-2"
    idle = RetrievalIndexerService(
        event_bus=InMemoryEventBus(),
        store=idle_store,
        embedder=StubEmbedder(),
        projection_name="test-retrieval",
        batch_size=10,
    )
    idle_cycle = await idle.index_once()
    assert idle_cycle.checkpoint == "10-2"
    assert idle_cycle.indexed_documents == 0

    bus = InMemoryEventBus()
    await bus.publish(make_event("event-1"))
    failed_store = RecordingRetrievalStore()
    failing = RetrievalIndexerService(
        event_bus=bus,
        store=failed_store,
        embedder=StubEmbedder(mismatched=True),
        projection_name="test-retrieval",
        batch_size=10,
    )
    with pytest.raises(RuntimeError, match="embedding count"):
        await failing.index_once()
    assert failed_store.checkpoint_value is None
    assert failed_store.indexed == []


async def test_hybrid_search_service_embeds_fuses_counts_and_closes() -> None:
    event = make_event("event-1")
    channel_candidate = candidate(event, rank=1, score=0.9)
    store = RecordingRetrievalStore(
        batch=CandidateBatch(lexical=(channel_candidate,), dense=(channel_candidate,))
    )
    embedder = StubEmbedder()
    service = HybridSearchService(store=store, embedder=embedder)
    query = SearchQuery(text="earthquake", candidate_limit=10)

    result = await service.search(query)

    assert result.candidates_considered == 1
    assert len(result.hits) == 1
    assert result.embedding_model == embedder.model_name
    assert result.ranking_mode == "hybrid"
    assert result.ranking_rule == RANKING_RULE
    assert result.caveat == RETRIEVAL_CAVEAT
    assert store.query == query
    assert store.embedding == (1.0, 0.0, 0.0)
    assert store.embedding_model == embedder.model_name
    assert await service.is_ready() is True
    await service.close()
    assert store.closed is True


async def test_lexical_search_service_skips_query_embedding_and_dense_candidates() -> None:
    event = make_event("event-1")
    lexical_candidate = candidate(event, rank=1, score=0.9)
    store = RecordingRetrievalStore(batch=CandidateBatch(lexical=(lexical_candidate,), dense=()))
    embedder = StubEmbedder()
    service = HybridSearchService(store=store, embedder=embedder)
    query = SearchQuery(text="earthquake", candidate_limit=10, ranking_mode="lexical")

    result = await service.search(query)

    assert [hit.message.event.event_id for hit in result.hits] == ["event-1"]
    assert result.candidates_considered == 1
    assert result.ranking_mode == "lexical"
    assert embedder.query_calls == []
    assert store.embedding is None


class FakeFastEmbedModel:
    def __init__(self, *, embedding_size: int = 3, extra_document: bool = False) -> None:
        self.embedding_size = embedding_size
        self.extra_document = extra_document

    def passage_embed(self, texts: Iterable[str]) -> tuple[tuple[float, ...], ...]:
        vectors = tuple((3.0, 4.0, 0.0) for _text in texts)
        return vectors + (((1.0, 0.0, 0.0),) if self.extra_document else ())

    def query_embed(self, texts: Iterable[str]) -> tuple[tuple[float, ...], ...]:
        return tuple((0.0, 3.0, 4.0) for _text in texts)


class TestFastEmbedProvider(FastEmbedProvider):
    __test__ = False

    def __init__(self, model: FakeFastEmbedModel) -> None:
        super().__init__(model_name="test/model", dimensions=3, cache_dir="cache", threads=1)
        self.fake_model = model
        self.loads = 0

    def _create_model(self) -> FakeFastEmbedModel:
        self.loads += 1
        return self.fake_model


async def test_fastembed_provider_is_lazy_normalizes_and_reuses_the_model() -> None:
    provider = TestFastEmbedProvider(FakeFastEmbedModel())
    assert await provider.embed_documents(()) == ()
    documents = await provider.embed_documents(("one", "two"))
    query = await provider.embed_query("question")
    assert documents == ((0.6, 0.8, 0.0), (0.6, 0.8, 0.0))
    assert query == (0.0, 0.6, 0.8)
    assert provider.loads == 1


async def test_fastembed_provider_rejects_invalid_model_outputs() -> None:
    extra = TestFastEmbedProvider(FakeFastEmbedModel(extra_document=True))
    with pytest.raises(RuntimeError, match="different number"):
        await extra.embed_documents(("one",))

    provider = TestFastEmbedProvider(FakeFastEmbedModel())
    with pytest.raises(ValueError, match="dimensions"):
        provider._validated((1.0, 2.0))
    with pytest.raises(ValueError, match="finite"):
        provider._validated((1.0, math.inf, 2.0))
    with pytest.raises(ValueError, match="zero vector"):
        provider._validated((0.0, 0.0, 0.0))


def test_fastembed_model_factory_enforces_the_configured_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = ModuleType("fastembed")

    class WrongWidthModel:
        embedding_size = 4

        def __init__(self, **kwargs: object) -> None:
            assert kwargs == {
                "model_name": "test/model",
                "cache_dir": "cache",
                "threads": 1,
                "specific_model_path": None,
                "local_files_only": False,
            }

    fake_module.TextEmbedding = WrongWidthModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)
    provider = FastEmbedProvider(
        model_name="test/model",
        dimensions=3,
        cache_dir="cache",
        threads=1,
    )
    with pytest.raises(ValueError, match="exposes 4 dimensions"):
        provider._create_model()


def test_typed_search_dataclasses_hold_evidence_without_generation() -> None:
    event = make_event("typed-1")
    hit = SearchHit(
        message=StreamMessage(stream_id="1-0", event=event),
        document_text="evidence",
        distance_km=None,
        ranking=RankingExplanation(
            lexical_rank=1,
            lexical_score=1.0,
            dense_rank=None,
            dense_similarity=None,
            rrf_score=0.5,
            exact_phrase_match=True,
            token_coverage=1.0,
            rerank_score=0.575,
        ),
        citation=CitationValidation(
            status="traceable",
            url="https://example.org/evidence",
            source_field="source_url",
            reasons=("public_http_url",),
        ),
    )
    result = SearchResult(
        hits=(hit,),
        candidates_considered=1,
        embedding_model="test/model",
        ranking_mode="hybrid",
        ranking_rule="test-rule",
        caveat="no answer",
    )
    assert result.hits[0].message.event.event_id == "typed-1"

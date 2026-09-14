"""HTTP API behavior tests."""

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from agent_rag_core import Event, GeoPoint

from atlas_pulse.agent_governance import (
    Ed25519LedgerSigner,
    InMemoryAgentRunLedger,
    build_agent_approval,
)
from atlas_pulse.agent_runs import (
    AGENT_AUTHORIZATION_POLICY_VERSION,
    AGENT_RUN_CAVEAT,
    AGENT_RUN_IDENTITY_ALGORITHM,
    AGENT_RUN_RULE_VERSION,
    AGENT_RUN_SCHEMA_VERSION,
)
from atlas_pulse.api import AgentRunPreflightResponse, create_app
from atlas_pulse.correlation import CORRELATION_CAVEAT, CorrelationBatch, CorrelationPair
from atlas_pulse.evidence_packs import (
    EVIDENCE_PACK_CAVEAT,
    EVIDENCE_PACK_IDENTITY_ALGORITHM,
    EVIDENCE_PACK_RULE_VERSION,
    EVIDENCE_PACK_SCHEMA_VERSION,
    EVIDENCE_PACK_TRUST_BOUNDARY,
)
from atlas_pulse.projections import CorrelationQuery, SignalPage, SignalQuery
from atlas_pulse.retrieval import (
    CandidateBatch,
    ChannelCandidate,
    SearchQuery,
    SearchResult,
    document_hash,
    fuse_and_rerank,
)
from atlas_pulse.retrieval.ranking import RANKING_RULE, RETRIEVAL_CAVEAT
from atlas_pulse.streams import InMemoryEventBus
from atlas_pulse.streams.base import StreamMessage


class UnreadyBus(InMemoryEventBus):
    async def is_ready(self) -> bool:
        return False


class CloseTrackingBus(InMemoryEventBus):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FailingCloseBus(CloseTrackingBus):
    async def close(self) -> None:
        await super().close()
        raise RuntimeError("stream close failed")


class StubSignalStore:
    def __init__(
        self,
        *,
        page: SignalPage | None = None,
        ready: bool = True,
        correlation_batch: CorrelationBatch | None = None,
    ) -> None:
        self.page = page or SignalPage(items=(), next_cursor=None, has_more=False)
        self.ready = ready
        self.closed = False
        self.query: SignalQuery | None = None
        self.correlation_batch = correlation_batch or CorrelationBatch(())
        self.correlation_query: CorrelationQuery | None = None

    async def query_current(self, query: SignalQuery) -> SignalPage:
        self.query = query
        return self.page

    async def query_correlations(self, query: CorrelationQuery) -> CorrelationBatch:
        self.correlation_query = query
        return self.correlation_batch

    async def is_ready(self) -> bool:
        return self.ready

    async def close(self) -> None:
        self.closed = True


class StubSearchService:
    def __init__(self, *, result: SearchResult | None = None, ready: bool = True) -> None:
        self.result = result or SearchResult(
            hits=(),
            candidates_considered=0,
            embedding_model="test/local-model",
            ranking_mode="hybrid",
            ranking_rule=RANKING_RULE,
            caveat=RETRIEVAL_CAVEAT,
        )
        self.ready = ready
        self.closed = False
        self.query: SearchQuery | None = None

    async def search(self, query: SearchQuery) -> SearchResult:
        self.query = query
        return self.result

    async def is_ready(self) -> bool:
        return self.ready

    async def close(self) -> None:
        self.closed = True


async def test_health_readiness_and_recent_events() -> None:
    bus = InMemoryEventBus()
    await bus.publish(
        Event(
            event_id="us7000demo",
            event_type="seismic.earthquake",
            source="usgs",
            occurred_at=datetime(2024, 7, 10, tzinfo=UTC),
        )
    )
    transport = httpx.ASGITransport(app=create_app(bus))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/healthz")
        ready = await client.get("/readyz")
        events = await client.get("/v1/events", params={"limit": 1})

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "version": "0.9.0", "commit_sha": "unknown"}
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert events.status_code == 200
    assert events.json()["count"] == 1
    assert events.json()["items"][0]["event"]["event_id"] == "us7000demo"


async def test_health_binds_a_reviewed_build_commit() -> None:
    commit_sha = "a" * 40
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus(), build_commit_sha=commit_sha))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/healthz")
        ready = await client.get("/readyz")

    assert health.json()["commit_sha"] == commit_sha
    assert ready.json()["commit_sha"] == commit_sha


def test_application_rejects_an_invalid_build_commit() -> None:
    with pytest.raises(ValueError, match="build commit SHA"):
        create_app(InMemoryEventBus(), build_commit_sha="moving-main")


async def test_readiness_reports_dependency_failure() -> None:
    transport = httpx.ASGITransport(app=create_app(UnreadyBus()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"detail": "event stream unavailable"}


async def test_events_limit_is_validated() -> None:
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/events", params={"limit": 501})

    assert response.status_code == 422


async def test_application_lifespan_closes_event_bus() -> None:
    bus = CloseTrackingBus()
    store = StubSignalStore()
    search = StubSearchService()
    app = create_app(bus, store, search)

    async with app.router.lifespan_context(app):
        assert bus.closed is False
        assert store.closed is False
        assert search.closed is False

    assert bus.closed is True
    assert store.closed is True
    assert search.closed is True


async def test_application_lifespan_still_closes_store_when_stream_close_fails() -> None:
    bus = FailingCloseBus()
    store = StubSignalStore()
    app = create_app(bus, store)

    with pytest.raises(RuntimeError, match="stream close failed"):
        async with app.router.lifespan_context(app):
            pass

    assert bus.closed is True
    assert store.closed is True


async def test_application_lifespan_closes_search_when_signal_close_fails() -> None:
    class FailingSignalStore(StubSignalStore):
        async def close(self) -> None:
            await super().close()
            raise RuntimeError("projection close failed")

    bus = CloseTrackingBus()
    store = FailingSignalStore()
    search = StubSearchService()
    app = create_app(bus, store, search)

    with pytest.raises(RuntimeError, match="projection close failed"):
        async with app.router.lifespan_context(app):
            pass

    assert bus.closed is True
    assert store.closed is True
    assert search.closed is True


async def test_replay_is_oldest_first_and_cursor_paginated() -> None:
    bus = InMemoryEventBus()
    for event_id in ("one", "two", "three"):
        await bus.publish(
            Event(
                event_id=event_id,
                event_type="seismic.earthquake",
                source="usgs",
                occurred_at=datetime(2024, 7, 10, tzinfo=UTC),
            )
        )
    transport = httpx.ASGITransport(app=create_app(bus))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_page = await client.get("/v1/events/replay", params={"limit": 2})
        second_page = await client.get(
            "/v1/events/replay",
            params={"limit": 2, "after": first_page.json()["next_cursor"]},
        )

    assert first_page.status_code == 200
    assert [item["event"]["event_id"] for item in first_page.json()["items"]] == [
        "one",
        "two",
    ]
    assert first_page.json()["next_cursor"] == "0-2"
    assert first_page.json()["has_more"] is True
    assert first_page.json()["order"] == "oldest_first"
    assert [item["event"]["event_id"] for item in second_page.json()["items"]] == ["three"]
    assert second_page.json()["next_cursor"] == "0-3"
    assert second_page.json()["has_more"] is False


async def test_replay_rejects_an_invalid_stream_cursor() -> None:
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/events/replay", params={"after": "not-a-cursor"})

    assert response.status_code == 422


async def test_current_signals_maps_all_indexed_filters() -> None:
    event = Event(
        event_id="nws-demo",
        event_type="weather.alert",
        source="nws",
        occurred_at=datetime(2024, 7, 10, tzinfo=UTC),
    )
    store = StubSignalStore(
        page=SignalPage(
            items=(StreamMessage(stream_id="2000-4", event=event),),
            next_cursor="2000-4",
            has_more=True,
        )
    )
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus(), store))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/signals",
            params={
                "limit": 25,
                "after": "3000-0",
                "source": "nws",
                "min_severity": 3,
                "occurred_after": "2024-07-01T00:00:00Z",
                "occurred_before": "2024-07-31T00:00:00Z",
                "bbox": "-100,20,-80,40",
                "include_area_only": "true",
                "active_only": "false",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "count": 1,
        "items": [{"stream_id": "2000-4", "event": event.model_dump(mode="json")}],
        "next_cursor": "2000-4",
        "has_more": True,
        "order": "newest_revision_first",
    }
    assert store.query is not None
    assert store.query.limit == 25
    assert store.query.after == "3000-0"
    assert store.query.source == "nws"
    assert store.query.min_severity == 3
    assert store.query.bounds is not None
    assert store.query.bounds.west == -100
    assert store.query.include_area_only is True
    assert store.query.active_only is False


async def test_current_signals_requires_an_injected_projection() -> None:
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/signals")

    assert response.status_code == 503
    assert response.json() == {"detail": "signal projection unavailable"}


@pytest.mark.parametrize("source", ["firms", "gdelt"])
async def test_current_signals_accepts_new_source_filters(source: str) -> None:
    store = StubSignalStore()
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus(), store))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/signals", params={"source": source})

    assert response.status_code == 200
    assert store.query is not None
    assert store.query.source == source


async def test_readiness_reports_projection_failure() -> None:
    transport = httpx.ASGITransport(
        app=create_app(InMemoryEventBus(), StubSignalStore(ready=False))
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"detail": "signal projection unavailable"}


async def test_current_signals_rejects_invalid_spatial_and_time_filters() -> None:
    app = create_app(InMemoryEventBus(), StubSignalStore())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        malformed = await client.get("/v1/signals", params={"bbox": "west,1,2,3"})
        incomplete = await client.get("/v1/signals", params={"bbox": "1,2,3"})
        wrapping = await client.get("/v1/signals", params={"bbox": "20,-5,-10,30"})
        naive_time = await client.get(
            "/v1/signals", params={"occurred_after": "2024-07-01T00:00:00"}
        )
        reversed_time = await client.get(
            "/v1/signals",
            params={
                "occurred_after": "2024-08-01T00:00:00Z",
                "occurred_before": "2024-07-01T00:00:00Z",
            },
        )

    assert malformed.status_code == 422
    assert incomplete.status_code == 422
    assert wrapping.status_code == 422
    assert naive_time.status_code == 422
    assert reversed_time.status_code == 422


async def test_incidents_returns_a_typed_auditable_evidence_graph() -> None:
    occurred = datetime(2026, 9, 12, 12, tzinfo=UTC)
    fire = Event(
        event_id="fire-1",
        event_type="fire.thermal_anomaly",
        source="firms",
        occurred_at=occurred,
        ingested_at=occurred,
        location=GeoPoint(latitude=12, longitude=77, altitude_km=None),
        payload={"place": "Test City", "source_url": "https://example.test/fire"},
    )
    conflict = Event(
        event_id="conflict-1",
        event_type="geopolitical.gdelt_event",
        source="gdelt",
        occurred_at=occurred,
        ingested_at=occurred,
        location=GeoPoint(latitude=12.1, longitude=77.1, altitude_km=None),
        payload={"place": "Test City", "source_url": "https://example.test/report"},
    )
    store = StubSignalStore(
        correlation_batch=CorrelationBatch(
            (
                CorrelationPair(
                    left=StreamMessage(stream_id="10-0", event=fire),
                    right=StreamMessage(stream_id="11-0", event=conflict),
                    distance_km=4.125,
                    time_delta_minutes=0,
                    left_geometry_basis="point",
                    right_geometry_basis="point",
                ),
            )
        )
    )
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus(), store))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/incidents",
            params={
                "limit": 10,
                "radius_km": 25,
                "time_window_minutes": 90,
                "lookback_hours": 48,
                "candidate_edge_limit": 250,
                "bbox": "70,5,85,20",
                "active_only": "false",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["total_incidents"] == 1
    assert body["rule_version"] == "spatiotemporal-v1"
    assert body["caveat"] == CORRELATION_CAVEAT
    assert body["relationship_rule_version"] == "structured-claims-v1"
    assert "not proof of truth" in body["relationship_caveat"]
    assert body["items"][0]["sources"] == ["firms", "gdelt"]
    assert body["items"][0]["node_count"] == 2
    assert body["items"][0]["edge_count"] == 1
    assert body["items"][0]["edges"][0]["distance_km"] == 4.125
    assert body["items"][0]["edges"][0]["relation"] == "spatiotemporal_cooccurrence"
    analysis = body["items"][0]["relationship_analysis"]
    assert analysis["rule_version"] == "structured-claims-v1"
    assert analysis["analyzed_edge_count"] == 1
    assert analysis["corroboration_count"] == 0
    assert analysis["contradiction_count"] == 0
    assert analysis["insufficient_evidence_count"] == 1
    assert analysis["relationships"][0]["label"] == "insufficient_evidence"
    assert {claim["value"] for claim in analysis["claims"]} == {
        "fire_related",
        "material_conflict",
    }
    assert {claim["node_id"] for claim in analysis["claims"]} == {
        "firms:fire-1",
        "gdelt:conflict-1",
    }
    assert body["parameters"] == {
        "radius_km": 25,
        "time_window_minutes": 90,
        "lookback_hours": 48,
        "candidate_edge_limit": 250,
        "incident_limit": 10,
        "active_only": False,
        "bbox": [70, 5, 85, 20],
    }
    assert store.correlation_query is not None
    assert store.correlation_query.edge_limit == 250
    assert store.correlation_query.bounds is not None


async def test_incidents_requires_projection_and_validates_bounds() -> None:
    without_store = httpx.ASGITransport(app=create_app(InMemoryEventBus()))
    async with httpx.AsyncClient(transport=without_store, base_url="http://test") as client:
        unavailable = await client.get("/v1/incidents")
    assert unavailable.status_code == 503

    with_store = httpx.ASGITransport(app=create_app(InMemoryEventBus(), StubSignalStore()))
    async with httpx.AsyncClient(transport=with_store, base_url="http://test") as client:
        invalid = await client.get("/v1/incidents", params={"bbox": "20,-5,-10,30"})
        unbounded = await client.get("/v1/incidents", params={"candidate_edge_limit": 5_001})
    assert invalid.status_code == 422
    assert unbounded.status_code == 422


async def test_search_returns_typed_rank_evidence_and_all_reproducibility_parameters() -> None:
    event = Event(
        event_id="alert-1",
        event_type="weather.alert",
        source="nws",
        occurred_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 12, 12, 1, tzinfo=UTC),
        location=GeoPoint(latitude=35, longitude=-97, altitude_km=None),
        payload={
            "title": "Severe thunderstorm warning",
            "source_url": "https://api.weather.gov/alerts/alert-1",
        },
    )
    candidate = ChannelCandidate(
        message=StreamMessage(stream_id="200-1", event=event),
        document_text="Title: Severe thunderstorm warning",
        rank=1,
        score=0.91,
        distance_km=14.25,
    )
    hits = fuse_and_rerank(
        CandidateBatch(lexical=(candidate,), dense=(candidate,)),
        query_text="severe thunderstorm",
        limit=5,
    )
    search = StubSearchService(
        result=SearchResult(
            hits=hits,
            candidates_considered=1,
            embedding_model="BAAI/bge-small-en-v1.5",
            ranking_mode="rrf",
            ranking_rule="rrf60-v1",
            caveat=RETRIEVAL_CAVEAT,
        )
    )
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus(), StubSignalStore(), search))
    params: dict[str, str | int | float | bool | None] = {
        "q": "  severe   thunderstorm ",
        "limit": 5,
        "candidate_limit": 25,
        "source": "nws",
        "occurred_after": "2026-09-01T00:00:00Z",
        "occurred_before": "2026-09-13T00:00:00Z",
        "bbox": "-100,30,-90,40",
        "near": "-97,35",
        "radius_km": 100,
        "active_only": "false",
        "ranking_mode": "rrf",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/search", params=params)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["candidates_considered"] == 1
    assert body["embedding_model"] == "BAAI/bge-small-en-v1.5"
    assert body["ranking_mode"] == "rrf"
    assert body["ranking_rule"] == "rrf60-v1"
    assert body["caveat"] == RETRIEVAL_CAVEAT
    assert body["items"][0]["event"]["event_id"] == "alert-1"
    assert body["items"][0]["distance_km"] == 14.25
    assert body["items"][0]["ranking"]["lexical_rank"] == 1
    assert body["items"][0]["ranking"]["dense_rank"] == 1
    assert body["items"][0]["ranking"]["exact_phrase_match"] is True
    assert body["items"][0]["citation"] == {
        "status": "traceable",
        "url": "https://api.weather.gov/alerts/alert-1",
        "source_field": "source_url",
        "reasons": ["public_http_url", "source_event_identity_attached"],
    }
    assert body["parameters"] == {
        "query": "severe thunderstorm",
        "limit": 5,
        "candidate_limit": 25,
        "source": "nws",
        "occurred_after": "2026-09-01T00:00:00Z",
        "occurred_before": "2026-09-13T00:00:00Z",
        "active_only": False,
        "bbox": [-100, 30, -90, 40],
        "near": [-97, 35],
        "radius_km": 100,
        "ranking_mode": "rrf",
    }
    assert search.query is not None
    assert search.query.near is not None
    assert search.query.near.longitude == -97
    assert search.query.bounds is not None
    assert search.query.ranking_mode == "rrf"


async def test_search_requires_an_index_and_readiness_reports_index_failure() -> None:
    unavailable_transport = httpx.ASGITransport(
        app=create_app(InMemoryEventBus(), StubSignalStore())
    )
    async with httpx.AsyncClient(transport=unavailable_transport, base_url="http://test") as client:
        unavailable = await client.get("/v1/search", params={"q": "earthquake"})
        unavailable_pack = await client.get("/v1/evidence-packs", params={"q": "earthquake"})
        unavailable_preflight = await client.get(
            "/v1/agent-runs/preflight", params={"q": "earthquake"}
        )
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "retrieval index unavailable"}
    assert unavailable_pack.status_code == 503
    assert unavailable_pack.json() == {"detail": "retrieval index unavailable"}
    assert unavailable_preflight.status_code == 503
    assert unavailable_preflight.json() == {"detail": "retrieval index unavailable"}

    unready_transport = httpx.ASGITransport(
        app=create_app(InMemoryEventBus(), StubSignalStore(), StubSearchService(ready=False))
    )
    async with httpx.AsyncClient(transport=unready_transport, base_url="http://test") as client:
        unready = await client.get("/readyz")
    assert unready.status_code == 503
    assert unready.json() == {"detail": "retrieval index unavailable"}


async def test_evidence_pack_returns_bounded_content_addressed_agent_handoff() -> None:
    traceable_event = Event(
        event_id="alert-1",
        event_type="weather.alert",
        source="nws",
        occurred_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 12, 12, 1, tzinfo=UTC),
        payload={
            "title": "Severe thunderstorm warning",
            "source_url": "https://api.weather.gov/alerts/alert-1",
        },
    )
    missing_event = Event(
        event_id="quake-2",
        event_type="seismic.earthquake",
        source="usgs",
        occurred_at=datetime(2026, 9, 12, 12, 2, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 12, 12, 3, tzinfo=UTC),
        payload={"title": "Earthquake without public evidence URL"},
    )
    traceable = ChannelCandidate(
        message=StreamMessage(stream_id="200-1", event=traceable_event),
        document_text="Title: Severe thunderstorm warning",
        rank=1,
        score=0.91,
        distance_km=14.25,
    )
    missing = ChannelCandidate(
        message=StreamMessage(stream_id="200-2", event=missing_event),
        document_text="Title: Earthquake without public evidence URL",
        rank=2,
        score=0.80,
    )
    hits = fuse_and_rerank(
        CandidateBatch(lexical=(traceable, missing), dense=(traceable, missing)),
        query_text="severe thunderstorm",
        limit=5,
    )
    search = StubSearchService(
        result=SearchResult(
            hits=hits,
            candidates_considered=7,
            embedding_model="BAAI/bge-small-en-v1.5",
            ranking_mode="hybrid",
            ranking_rule=RANKING_RULE,
            caveat=RETRIEVAL_CAVEAT,
        )
    )
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus(), StubSignalStore(), search))
    params: dict[str, str | int] = {
        "q": "  severe   thunderstorm ",
        "retrieval_limit": 5,
        "candidate_limit": 25,
        "max_items": 2,
        "max_characters_per_item": 12,
        "max_total_characters": 12,
        "source": "nws",
        "bbox": "-100,30,-90,40",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/evidence-packs", params=params)

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == EVIDENCE_PACK_SCHEMA_VERSION
    assert body["rule_version"] == EVIDENCE_PACK_RULE_VERSION
    assert body["identity_algorithm"] == EVIDENCE_PACK_IDENTITY_ALGORITHM
    assert body["pack_id"].startswith("pack-") and len(body["pack_id"]) == 69
    identity = {key: value for key, value in body.items() if key != "pack_id"}
    expected_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert body["pack_id"] == f"pack-{expected_hash}"
    assert body["status"] == "traceable_evidence_available"
    assert body["item_count"] == 1
    assert body["exclusion_count"] == 1
    assert body["source_text_characters"] == 12
    assert body["answer_generated"] is False
    assert body["trust_boundary"] == EVIDENCE_PACK_TRUST_BOUNDARY
    assert body["caveat"] == EVIDENCE_PACK_CAVEAT
    assert body["budget"] == {
        "max_items": 2,
        "max_characters_per_item": 12,
        "max_total_characters": 12,
        "character_unit": "unicode_code_points",
    }
    item = body["items"][0]
    assert item["retrieval_rank"] == 1
    assert item["event_id"] == "alert-1"
    assert item["ingested_at"] == "2026-09-12T12:01:00Z"
    assert item["text"] == "Title: Sever"
    assert item["truncated"] is True
    assert item["document_sha256"] == document_hash(traceable.document_text)
    assert item["text_sha256"] == document_hash("Title: Sever")
    assert item["citation"]["status"] == "traceable"
    assert item["ranking"]["lexical_rank"] == 1
    assert body["exclusions"][0]["event_id"] == "quake-2"
    assert body["exclusions"][0]["reason"] == "citation_missing"
    assert body["exclusions"][0]["ranking"]["dense_rank"] == 2
    assert body["exclusions"][0]["occurred_at"] == "2026-09-12T12:02:00Z"
    assert "Earthquake without public evidence URL" not in response.text
    assert body["retrieval"] == {
        "candidates_considered": 7,
        "returned_hits": 2,
        "embedding_model": "BAAI/bge-small-en-v1.5",
        "ranking_mode": "hybrid",
        "ranking_rule": RANKING_RULE,
        "caveat": RETRIEVAL_CAVEAT,
        "parameters": {
            "query": "severe thunderstorm",
            "limit": 5,
            "candidate_limit": 25,
            "source": "nws",
            "occurred_after": None,
            "occurred_before": None,
            "active_only": True,
            "bbox": [-100.0, 30.0, -90.0, 40.0],
            "near": None,
            "radius_km": None,
            "ranking_mode": "hybrid",
        },
    }
    assert search.query is not None
    assert search.query.limit == 5


async def test_agent_run_preflight_chains_pack_and_manifest_without_executing() -> None:
    event = Event(
        event_id="alert-1",
        event_type="weather.alert",
        source="nws",
        occurred_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 12, 12, 1, tzinfo=UTC),
        payload={
            "title": "Severe thunderstorm warning",
            "source_url": "https://api.weather.gov/alerts/alert-1",
        },
    )
    candidate = ChannelCandidate(
        message=StreamMessage(stream_id="200-1", event=event),
        document_text="Title: Severe thunderstorm warning",
        rank=1,
        score=0.91,
    )
    hits = fuse_and_rerank(
        CandidateBatch(lexical=(candidate,), dense=(candidate,)),
        query_text="severe thunderstorm",
        limit=5,
    )
    search = StubSearchService(
        result=SearchResult(
            hits=hits,
            candidates_considered=3,
            embedding_model="BAAI/bge-small-en-v1.5",
            ranking_mode="hybrid",
            ranking_rule=RANKING_RULE,
            caveat=RETRIEVAL_CAVEAT,
        )
    )
    transport = httpx.ASGITransport(app=create_app(InMemoryEventBus(), StubSignalStore(), search))
    params: dict[str, str | int] = {
        "q": "severe thunderstorm",
        "retrieval_limit": 5,
        "candidate_limit": 25,
        "max_items": 2,
        "max_characters_per_item": 20,
        "max_total_characters": 40,
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/agent-runs/preflight", params=params)

    assert response.status_code == 200
    body = response.json()
    pack = body["evidence_pack"]
    manifest = body["manifest"]
    assert pack["status"] == "traceable_evidence_available"
    assert manifest["schema_version"] == AGENT_RUN_SCHEMA_VERSION
    assert manifest["rule_version"] == AGENT_RUN_RULE_VERSION
    assert manifest["identity_algorithm"] == AGENT_RUN_IDENTITY_ALGORITHM
    assert manifest["manifest_id"].startswith("manifest-")
    assert len(manifest["manifest_id"]) == 73
    identity = {key: value for key, value in manifest.items() if key != "manifest_id"}
    expected_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert manifest["manifest_id"] == f"manifest-{expected_hash}"
    assert manifest["status"] == "blocked"
    assert manifest["evidence"]["pack_id"] == pack["pack_id"]
    assert manifest["evidence"]["evidence_ids"] == [pack["items"][0]["evidence_id"]]
    assert manifest["policy"]["policy_version"] == AGENT_AUTHORIZATION_POLICY_VERSION
    assert manifest["release"]["status"] == "not_supplied"
    assert manifest["authorization"]["passed_check_count"] == 3
    assert manifest["authorization"]["blocked_check_count"] == 8
    assert manifest["authorization"]["blocking_reasons"] == [
        "model_adapter_not_selected",
        "live_relationship_benchmark_incomplete",
        "grounded_answer_evaluation_missing",
        "agent_trajectory_evaluation_missing",
        "trajectory_drift_evidence_missing",
        "release_thresholds_not_met",
        "human_release_not_granted",
        "execution_disabled",
    ]
    assert manifest["execution"] == {
        "status": "not_started",
        "agent_model_invoked": False,
        "agent_network_accessed": False,
        "agent_tools_invoked": False,
        "answer_generated": False,
        "agent_side_effects_performed": False,
    }
    assert manifest["caveat"] == AGENT_RUN_CAVEAT
    assert search.query is not None
    assert search.query.limit == 5

    invalid_count = copy.deepcopy(body)
    invalid_count["manifest"]["authorization"]["passed_check_count"] = 99
    with pytest.raises(ValueError, match="passed_check_count does not match checks"):
        AgentRunPreflightResponse.model_validate(invalid_count)

    duplicate_check = copy.deepcopy(body)
    duplicate_check["manifest"]["authorization"]["checks"][-1] = duplicate_check["manifest"][
        "authorization"
    ]["checks"][0]
    with pytest.raises(ValueError, match="each v3 check exactly once"):
        AgentRunPreflightResponse.model_validate(duplicate_check)

    invalid_reason = copy.deepcopy(body)
    invalid_reason["manifest"]["authorization"]["checks"][0]["blocking_reason"] = (
        "execution_disabled"
    )
    with pytest.raises(ValueError, match="passed checks cannot have a blocking reason"):
        AgentRunPreflightResponse.model_validate(invalid_reason)

    invalid_approval = copy.deepcopy(body)
    invalid_approval["manifest"]["approval"]["approval_id"] = f"approval-{'a' * 64}"
    with pytest.raises(ValueError, match="not_supplied approval state"):
        AgentRunPreflightResponse.model_validate(invalid_approval)

    mismatched_pack = copy.deepcopy(body)
    mismatched_pack["manifest"]["evidence"]["pack_id"] = f"pack-{'f' * 64}"
    with pytest.raises(ValueError, match="manifest is not bound to the returned pack"):
        AgentRunPreflightResponse.model_validate(mismatched_pack)


async def test_agent_run_preflight_resolves_a_trusted_approval_but_stays_blocked() -> None:
    event = Event(
        event_id="alert-approved",
        event_type="weather.alert",
        source="nws",
        occurred_at=datetime(2026, 9, 14, 8, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 14, 8, 1, tzinfo=UTC),
        payload={
            "title": "Severe thunderstorm warning",
            "source_url": "https://api.weather.gov/alerts/alert-approved",
        },
    )
    candidate = ChannelCandidate(
        message=StreamMessage(stream_id="300-1", event=event),
        document_text="Title: Severe thunderstorm warning",
        rank=1,
        score=0.91,
    )
    search = StubSearchService(
        result=SearchResult(
            hits=fuse_and_rerank(
                CandidateBatch(lexical=(candidate,), dense=(candidate,)),
                query_text="severe thunderstorm",
                limit=5,
            ),
            candidates_considered=1,
            embedding_model="BAAI/bge-small-en-v1.5",
            ranking_mode="hybrid",
            ranking_rule=RANKING_RULE,
            caveat=RETRIEVAL_CAVEAT,
        )
    )
    params: dict[str, str | int] = {
        "q": "severe thunderstorm",
        "retrieval_limit": 5,
        "candidate_limit": 25,
    }
    initial_transport = httpx.ASGITransport(
        app=create_app(InMemoryEventBus(), StubSignalStore(), search)
    )
    async with httpx.AsyncClient(transport=initial_transport, base_url="http://test") as client:
        initial = (await client.get("/v1/agent-runs/preflight", params=params)).json()["manifest"]

    signer = Ed25519LedgerSigner.generate()
    ledger = InMemoryAgentRunLedger(trusted_key_ids={signer.key_id})
    issued_at = datetime.now(UTC) - timedelta(minutes=1)
    approval = build_agent_approval(
        proposal_id=initial["proposal_id"],
        source_manifest_id=initial["manifest_id"],
        approver_id="github:12345",
        issued_at=issued_at,
        expires_at=issued_at + timedelta(hours=1),
        reason="Reviewed the exact evidence-bound proposal.",
    )
    await ledger.append(approval, signer=signer)
    approved_transport = httpx.ASGITransport(
        app=create_app(InMemoryEventBus(), StubSignalStore(), search, ledger)
    )
    params["approval_id"] = approval.approval_id
    async with httpx.AsyncClient(transport=approved_transport, base_url="http://test") as client:
        response = await client.get("/v1/agent-runs/preflight", params=params)

    assert response.status_code == 200
    manifest = response.json()["manifest"]
    assert manifest["approval"]["status"] == "active"
    assert manifest["approval"]["approver_id"] == "github:12345"
    assert manifest["authorization"]["passed_check_count"] == 4
    assert manifest["authorization"]["blocked_check_count"] == 7
    assert "human_release_not_granted" not in manifest["authorization"]["blocking_reasons"]
    assert manifest["status"] == "blocked"
    assert manifest["execution"]["status"] == "not_started"


async def test_agent_run_preflight_fails_closed_when_approval_ledger_is_unavailable() -> None:
    transport = httpx.ASGITransport(
        app=create_app(InMemoryEventBus(), StubSignalStore(), StubSearchService())
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/agent-runs/preflight",
            params={"q": "earthquake", "approval_id": f"approval-{'a' * 64}"},
        )

    assert response.status_code == 200
    manifest = response.json()["manifest"]
    assert manifest["approval"]["status"] == "ledger_unavailable"
    assert "approval_ledger_unavailable" in manifest["authorization"]["blocking_reasons"]
    assert manifest["execution"]["status"] == "not_started"


@pytest.mark.parametrize(
    "params",
    [
        {"q": "earthquake", "retrieval_limit": 10, "candidate_limit": 5},
        {"q": "earthquake", "max_items": 0},
        {"q": "earthquake", "max_characters_per_item": 8_001},
        {"q": "earthquake", "max_total_characters": 64_001},
        {"q": "earthquake", "near": "181,1"},
    ],
)
async def test_evidence_pack_rejects_invalid_budgets_and_filters(
    params: dict[str, str | int],
) -> None:
    transport = httpx.ASGITransport(
        app=create_app(InMemoryEventBus(), StubSignalStore(), StubSearchService())
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/evidence-packs", params=params)
        preflight = await client.get("/v1/agent-runs/preflight", params=params)
    assert response.status_code == 422
    assert preflight.status_code == 422


@pytest.mark.parametrize(
    "params",
    [
        {"q": "earthquake", "limit": 10, "candidate_limit": 5},
        {"q": "earthquake", "near": "west,north"},
        {"q": "earthquake", "near": "1"},
        {"q": "earthquake", "near": "181,1"},
        {"q": "earthquake", "bbox": "20,-5,-10,30"},
        {"q": "earthquake", "ranking_mode": "unknown"},
        {"q": "earthquake", "occurred_after": "2026-09-01T00:00:00"},
        {
            "q": "earthquake",
            "occurred_after": "2026-09-12T00:00:00Z",
            "occurred_before": "2026-09-01T00:00:00Z",
        },
    ],
)
async def test_search_rejects_invalid_bounded_filters(
    params: dict[str, str | int | float | bool | None],
) -> None:
    transport = httpx.ASGITransport(
        app=create_app(InMemoryEventBus(), StubSignalStore(), StubSearchService())
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/search", params=params)
    assert response.status_code == 422

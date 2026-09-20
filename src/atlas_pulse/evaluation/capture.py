"""Capture blinded candidate pools from the deployed retrieval API."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from atlas_pulse.api import SearchHitResponse, SearchParametersResponse, SearchResponse
from atlas_pulse.evaluation.base import (
    CandidatePool,
    CapturedRun,
    EvaluationQuery,
    EvaluationQuerySet,
    PooledCandidate,
    PooledQuery,
)
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.retrieval import RankingMode

_MAX_RATE_LIMIT_RETRIES = 3
_MAX_RATE_LIMIT_WAIT_SECONDS = 300
_MAX_TOTAL_RATE_LIMIT_WAIT_SECONDS = 900

Sleeper = Callable[[float], Awaitable[None]]
WaitReporter = Callable[[int, str], None]


def _rate_limit_wait_seconds(response: httpx.Response, header: str) -> int:
    raw_value = response.headers.get(header)
    if raw_value is None or not raw_value.isascii() or not raw_value.isdigit():
        raise RuntimeError(f"retrieval API returned an invalid {header} header")
    seconds = int(raw_value)
    if not 1 <= seconds <= _MAX_RATE_LIMIT_WAIT_SECONDS:
        raise RuntimeError(
            f"retrieval API {header} exceeds the "
            f"{_MAX_RATE_LIMIT_WAIT_SECONDS}-second capture bound"
        )
    return seconds


def _next_window_wait(response: httpx.Response) -> int | None:
    remaining = response.headers.get("X-RateLimit-Remaining")
    reset_after = response.headers.get("X-RateLimit-Reset-After")
    if remaining is None and reset_after is None:
        return None
    if remaining is None or reset_after is None:
        raise RuntimeError("retrieval API returned incomplete rate-limit headers")
    if not remaining.isascii() or not remaining.isdigit():
        raise RuntimeError("retrieval API returned an invalid X-RateLimit-Remaining header")
    if int(remaining) > 0:
        return None
    return _rate_limit_wait_seconds(response, "X-RateLimit-Reset-After")


class RetrievalApiPacer:
    """Honor the retrieval API budget without contaminating request latency."""

    def __init__(
        self,
        *,
        sleeper: Sleeper,
        report_wait: WaitReporter | None,
    ) -> None:
        self._sleeper = sleeper
        self._report_wait = report_wait
        self._pending_wait_seconds: int | None = None
        self._total_wait_seconds = 0

    async def _wait(self, seconds: int, reason: str) -> None:
        if self._total_wait_seconds + seconds > _MAX_TOTAL_RATE_LIMIT_WAIT_SECONDS:
            raise RuntimeError(
                "retrieval capture exceeded the "
                f"{_MAX_TOTAL_RATE_LIMIT_WAIT_SECONDS}-second total rate-limit wait bound"
            )
        if self._report_wait is not None:
            self._report_wait(seconds, reason)
        await self._sleeper(float(seconds))
        self._total_wait_seconds += seconds

    async def get(
        self,
        client: httpx.AsyncClient,
        *,
        params: dict[str, str | int | float],
    ) -> tuple[httpx.Response, float]:
        """Return one terminal response and the successful request's own latency."""
        if self._pending_wait_seconds is not None:
            pending = self._pending_wait_seconds
            self._pending_wait_seconds = None
            await self._wait(pending, "API budget reset")

        for attempt in range(1, _MAX_RATE_LIMIT_RETRIES + 2):
            started = time.perf_counter()
            response = await client.get("/v1/search", params=params)
            latency_ms = (time.perf_counter() - started) * 1_000
            if response.status_code != httpx.codes.TOO_MANY_REQUESTS:
                if response.is_success:
                    self._pending_wait_seconds = _next_window_wait(response)
                return response, latency_ms
            if attempt > _MAX_RATE_LIMIT_RETRIES:
                raise RuntimeError(
                    f"retrieval API remained rate-limited after {attempt} bounded request attempts"
                )
            await self._wait(
                _rate_limit_wait_seconds(response, "Retry-After"),
                "HTTP 429",
            )

        raise AssertionError(
            "rate-limit retry loop completed without a response"
        )  # pragma: no cover


def safe_endpoint(value: str) -> str:
    parsed = urlsplit(value.strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an absolute HTTP(S) endpoint")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base URL must not contain embedded credentials")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def query_parameters(
    query: EvaluationQuery,
    *,
    mode: RankingMode,
    pool_depth: int,
) -> dict[str, str | int | float]:
    filters = query.filters
    parameters: dict[str, str | int | float] = {
        "q": query.text,
        "limit": pool_depth,
        "candidate_limit": min(200, max(50, pool_depth * 4)),
        "active_only": str(filters.active_only).lower(),
        "ranking_mode": mode,
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
    if filters.min_magnitude is not None:
        parameters["min_magnitude"] = filters.min_magnitude
    if filters.max_depth_km is not None:
        parameters["max_depth_km"] = filters.max_depth_km
    if filters.tsunami is not None:
        parameters["tsunami"] = str(filters.tsunami).lower()
    if filters.alert_type is not None:
        parameters["alert_type"] = filters.alert_type
    if filters.min_confidence_rank is not None:
        parameters["min_confidence_rank"] = filters.min_confidence_rank
    if filters.observation_period is not None:
        parameters["observation_period"] = filters.observation_period
    return parameters


def _candidate(hit: SearchHitResponse) -> PooledCandidate:
    payload = hit.event.payload
    title = next(
        (
            value.strip()
            for key in ("title", "headline", "alert_type", "category", "place")
            if isinstance((value := payload.get(key)), str) and value.strip()
        ),
        f"{hit.event.event_type} {hit.event.event_id}",
    )
    return PooledCandidate(
        document_id=f"{hit.event.source}:{hit.event.event_id}",
        source=hit.event.source,
        event_id=hit.event.event_id,
        title=title,
        occurred_at=hit.event.occurred_at,
        document_text=hit.document_text,
        document_hash=hashlib.sha256(hit.document_text.encode()).hexdigest(),
        citation_status=hit.citation.status,
        citation_url=hit.citation.url,
    )


def expected_parameters(
    query: EvaluationQuery,
    *,
    mode: RankingMode,
    pool_depth: int,
) -> SearchParametersResponse:
    production = query.filters.search_query(
        text=query.text,
        limit=pool_depth,
        candidate_limit=min(200, max(50, pool_depth * 4)),
        ranking_mode=mode,
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
        min_magnitude=production.min_magnitude,
        max_depth_km=production.max_depth_km,
        tsunami=production.tsunami,
        alert_type=production.alert_type,
        min_confidence_rank=production.min_confidence_rank,
        observation_period=production.observation_period,
        ranking_mode=production.ranking_mode,
    )


async def _capture_run(
    client: httpx.AsyncClient,
    pacer: RetrievalApiPacer,
    query: EvaluationQuery,
    *,
    mode: RankingMode,
    pool_depth: int,
) -> tuple[CapturedRun, tuple[PooledCandidate, ...]]:
    response, latency_ms = await pacer.get(
        client,
        params=query_parameters(query, mode=mode, pool_depth=pool_depth),
    )
    response.raise_for_status()
    result = SearchResponse.model_validate(response.json())
    if result.ranking_mode != mode:
        raise RuntimeError(
            f"retrieval API returned mode {result.ranking_mode!r}; requested {mode!r}"
        )
    if result.parameters != expected_parameters(query, mode=mode, pool_depth=pool_depth):
        raise RuntimeError("retrieval API did not echo the exact requested query and filters")
    if result.count != len(result.items) or result.candidates_considered < result.count:
        raise RuntimeError("retrieval API returned inconsistent result counts")
    candidates = tuple(_candidate(hit) for hit in result.items)
    return (
        CapturedRun(
            mode=mode,
            ranking_rule=result.ranking_rule,
            embedding_model=result.embedding_model,
            latency_ms=round(latency_ms, 3),
            document_ids=tuple(candidate.document_id for candidate in candidates),
        ),
        candidates,
    )


def _blinded_candidates(
    query_set_hash: str,
    query_id: str,
    candidates: dict[str, PooledCandidate],
) -> tuple[PooledCandidate, ...]:
    """Order candidates independently from every system rank to reduce assessor bias."""

    def blind_key(candidate: PooledCandidate) -> str:
        value = f"{query_set_hash}:{query_id}:{candidate.document_id}"
        return hashlib.sha256(value.encode()).hexdigest()

    return tuple(sorted(candidates.values(), key=blind_key))


async def capture_pool(
    query_set: EvaluationQuerySet,
    *,
    base_url: str,
    client: httpx.AsyncClient | None = None,
    sleeper: Sleeper = asyncio.sleep,
    on_rate_limit_wait: WaitReporter | None = None,
) -> CandidatePool:
    """Pool top results from every configured mode into one blinded review artifact."""
    endpoint = safe_endpoint(base_url)
    query_set_hash = canonical_sha256(query_set)
    captured_at = datetime.now(UTC)
    owns_client = client is None
    active_client = client or httpx.AsyncClient(base_url=endpoint, timeout=30.0)
    pacer = RetrievalApiPacer(
        sleeper=sleeper,
        report_wait=on_rate_limit_wait,
    )
    pooled_queries: list[PooledQuery] = []
    try:
        for query in query_set.queries:
            runs: list[CapturedRun] = []
            candidates: dict[str, PooledCandidate] = {}
            for mode in query_set.modes:
                run, run_candidates = await _capture_run(
                    active_client,
                    pacer,
                    query,
                    mode=mode,
                    pool_depth=query_set.pool_depth,
                )
                runs.append(run)
                for candidate in run_candidates:
                    existing = candidates.get(candidate.document_id)
                    if existing is not None and existing != candidate:
                        raise RuntimeError(
                            f"document changed during capture: {candidate.document_id}"
                        )
                    candidates[candidate.document_id] = candidate
            pooled_queries.append(
                PooledQuery(
                    query=query,
                    candidates=_blinded_candidates(
                        query_set_hash,
                        query.query_id,
                        candidates,
                    ),
                    runs=tuple(runs),
                )
            )
    finally:
        if owns_client:
            await active_client.aclose()

    timestamp = captured_at.strftime("%Y%m%dt%H%M%Sz").casefold()
    return CandidatePool(
        pool_id=f"{query_set.query_set_id}-{timestamp}",
        query_set_id=query_set.query_set_id,
        query_set_sha256=query_set_hash,
        captured_at=captured_at,
        endpoint=endpoint,
        queries=tuple(pooled_queries),
    )

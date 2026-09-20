"""Commit-pinned source-to-retrieval readiness diagnostics for live capture."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal, Self

import httpx
from pydantic import Field, field_validator, model_validator

from atlas_pulse.api import HealthResponse, SearchResponse, SignalsResponse
from atlas_pulse.evaluation.base import (
    EvaluationFilters,
    EvaluationQuery,
    EvaluationQuerySet,
    StrictModel,
)
from atlas_pulse.evaluation.capture import (
    RetrievalApiPacer,
    expected_parameters,
    query_parameters,
    safe_endpoint,
)
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.projections.base import SourceName
from atlas_pulse.source_polling import SourceFreshnessItem, SourceFreshnessResponse

RETRIEVAL_READINESS_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
RETRIEVAL_READINESS_RULE_VERSION: Literal["retrieval-capture-readiness-v1"] = (
    "retrieval-capture-readiness-v1"
)
RETRIEVAL_READINESS_IDENTITY_ALGORITHM: Literal["sha256-canonical-json-v1"] = (
    "sha256-canonical-json-v1"
)
RETRIEVAL_READINESS_CAVEATS = (
    "Readiness is a point-in-time observation of one API revision, not an availability SLA or "
    "upstream completeness guarantee.",
    "Source freshness is worker-written state evaluated by the API clock; it is not an "
    "independent observation of the upstream provider.",
    "Visibility probes request at most one record. They prove existence, not source or index "
    "cardinality, and arrivals or expiry can change immediately after the diagnostic.",
    "A dense result proves that at least one document satisfies the exact structured predicates; "
    "it does not establish relevance or retrieval quality.",
    "An empty exact query is reported as an eligibility warning because the live corpus may "
    "legitimately contain no matching event; only a required source-pipeline failure blocks capture.",
    "A ready result permits a fresh blinded capture only. It does not permit judgment reuse from "
    "a different query-set identity or support a quality claim without human review.",
)

ProbeKind = Literal["signal_projection", "retrieval_index", "evaluation_query"]
SourceBlocker = Literal[
    "source_poll_not_configured",
    "source_freshness_failed",
    "signal_projection_empty",
    "retrieval_index_empty",
    "current_projection_not_indexed",
]
QueryFailureReason = Literal[
    "source_pipeline_unready",
    "no_active_indexed_documents",
    "no_indexed_documents",
    "no_eligible_candidate",
]

Sleeper = Callable[[float], Awaitable[None]]
WaitReporter = Callable[[int, str], None]


def _normalize_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _require_utc(value: datetime, field: str) -> datetime:
    normalized = _normalize_utc(value, field)
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must use UTC")
    return normalized


class CorpusVisibilityProbe(StrictModel):
    """One bounded existence check without copying source text into the report."""

    kind: ProbeKind
    active_only: bool
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    returned_count: int = Field(ge=0, le=1)
    candidates_considered: int | None = Field(default=None, ge=0)
    document_id: str | None = Field(default=None, pattern=r"^[a-z0-9_-]+:.+$")
    occurred_at: datetime | None = None

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        visible = self.returned_count == 1
        if visible != (self.document_id is not None) or visible != (self.occurred_at is not None):
            raise ValueError("visible probes require one document identity and occurrence time")
        if self.occurred_at is not None:
            _require_utc(self.occurred_at, "probe occurred_at")
        is_search = self.kind in {"retrieval_index", "evaluation_query"}
        if is_search != (self.candidates_considered is not None):
            raise ValueError("only retrieval probes report candidates considered")
        if (
            self.candidates_considered is not None
            and self.candidates_considered < self.returned_count
        ):
            raise ValueError("candidates considered cannot be smaller than returned count")
        return self


class SourceRetrievalReadiness(StrictModel):
    """Freshness, projection, and retrieval-index evidence for one required source."""

    source: SourceName
    freshness: SourceFreshnessItem | None
    current_signals: CorpusVisibilityProbe
    retained_signals: CorpusVisibilityProbe
    current_index: CorpusVisibilityProbe
    retained_index: CorpusVisibilityProbe
    blocking_reasons: tuple[SourceBlocker, ...]
    passed: bool

    @field_validator("blocking_reasons")
    @classmethod
    def validate_blocker_order(cls, value: tuple[SourceBlocker, ...]) -> tuple[SourceBlocker, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("source blockers must be unique and canonically ordered")
        return value

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        probes = (
            (self.current_signals, "signal_projection", True),
            (self.retained_signals, "signal_projection", False),
            (self.current_index, "retrieval_index", True),
            (self.retained_index, "retrieval_index", False),
        )
        for probe, kind, active_only in probes:
            if probe.kind != kind or probe.active_only is not active_only:
                raise ValueError("source readiness contains a mismatched visibility probe")
            if probe.document_id is not None and not probe.document_id.startswith(
                f"{self.source}:"
            ):
                raise ValueError("source probe document identity must match its source")
        if self.freshness is not None and self.freshness.source != self.source:
            raise ValueError("source freshness item must match its source")
        expected: list[SourceBlocker] = []
        if self.freshness is None:
            expected.append("source_poll_not_configured")
        elif not self.freshness.passed:
            expected.append("source_freshness_failed")
        if self.retained_signals.returned_count == 0:
            expected.append("signal_projection_empty")
        if self.retained_index.returned_count == 0:
            expected.append("retrieval_index_empty")
        if self.current_signals.returned_count == 1 and self.current_index.returned_count == 0:
            expected.append("current_projection_not_indexed")
        if self.blocking_reasons != tuple(sorted(expected)):
            raise ValueError("source blockers do not match the observed pipeline evidence")
        if self.passed != (not expected):
            raise ValueError("source pass status must match its blockers")
        return self


class QueryRetrievalReadiness(StrictModel):
    """Exact dense eligibility check for one frozen evaluation query."""

    query_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    source: SourceName | None
    active_only: bool
    probe: CorpusVisibilityProbe
    failure_reason: QueryFailureReason | None = None
    passed: bool

    @model_validator(mode="after")
    def validate_query(self) -> Self:
        if self.probe.kind != "evaluation_query" or self.probe.active_only is not self.active_only:
            raise ValueError("query readiness contains a mismatched eligibility probe")
        if (
            self.source is not None
            and self.probe.document_id is not None
            and not self.probe.document_id.startswith(f"{self.source}:")
        ):
            raise ValueError("query probe document identity must match its source filter")
        expected_pass = self.probe.returned_count == 1
        if self.passed != expected_pass:
            raise ValueError("query pass status must match candidate visibility")
        if expected_pass != (self.failure_reason is None):
            raise ValueError("only empty query probes require a failure reason")
        return self


class RetrievalCaptureReadinessReport(StrictModel):
    """Content-addressed decision record produced immediately before live capture."""

    schema_version: Literal["1.0.0"] = RETRIEVAL_READINESS_SCHEMA_VERSION
    rule_version: Literal["retrieval-capture-readiness-v1"] = RETRIEVAL_READINESS_RULE_VERSION
    identity_algorithm: Literal["sha256-canonical-json-v1"] = RETRIEVAL_READINESS_IDENTITY_ALGORITHM
    report_id: str = Field(pattern=r"^retrieval-readiness-[0-9a-f]{20}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    query_set_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{2,79}$")
    query_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    endpoint: str
    expected_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    observed_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    application_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    embedding_model: str = Field(min_length=1)
    ranking_rule: str = Field(min_length=1)
    source_freshness_generated_at: datetime
    generated_at: datetime
    sources: tuple[SourceRetrievalReadiness, ...]
    queries: tuple[QueryRetrievalReadiness, ...] = Field(min_length=1)
    blocked_sources: tuple[SourceName, ...]
    empty_query_ids: tuple[str, ...]
    ready_for_capture: bool
    caveats: tuple[str, ...] = RETRIEVAL_READINESS_CAVEATS

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        _require_utc(self.source_freshness_generated_at, "source_freshness_generated_at")
        _require_utc(self.generated_at, "generated_at")
        if self.expected_commit_sha != self.observed_commit_sha:
            raise ValueError("retrieval readiness must observe the expected deployment commit")
        source_names = tuple(item.source for item in self.sources)
        if source_names != tuple(sorted(set(source_names))):
            raise ValueError("source readiness items must be unique and canonically ordered")
        query_ids = tuple(item.query_id for item in self.queries)
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("query readiness items must be unique")
        expected_blocked_sources = tuple(item.source for item in self.sources if not item.passed)
        expected_empty_queries = tuple(item.query_id for item in self.queries if not item.passed)
        if self.blocked_sources != expected_blocked_sources:
            raise ValueError("blocked sources must match source readiness")
        if self.empty_query_ids != expected_empty_queries:
            raise ValueError("empty query IDs must match query readiness")
        expected_ready = not expected_blocked_sources
        if self.ready_for_capture != expected_ready:
            raise ValueError("capture readiness must match required source-pipeline evidence")
        if self.caveats != RETRIEVAL_READINESS_CAVEATS:
            raise ValueError("retrieval readiness must retain its evidence caveats")
        digest = retrieval_readiness_sha256(self)
        if self.report_sha256 != digest or self.report_id != f"retrieval-readiness-{digest[:20]}":
            raise ValueError("retrieval readiness identity must match its exact content")
        return self


def retrieval_readiness_sha256(report: RetrievalCaptureReadinessReport) -> str:
    """Hash every report field except its derived identity fields."""
    return canonical_sha256(
        report.model_dump(
            mode="json",
            exclude={"report_id", "report_sha256"},
            exclude_none=False,
        )
    )


def _visibility_probe(
    *,
    kind: ProbeKind,
    active_only: bool,
    response: httpx.Response,
    document_id: str | None,
    occurred_at: datetime | None,
    candidates_considered: int | None = None,
) -> CorpusVisibilityProbe:
    return CorpusVisibilityProbe(
        kind=kind,
        active_only=active_only,
        response_sha256=hashlib.sha256(response.content).hexdigest(),
        returned_count=1 if document_id is not None else 0,
        candidates_considered=candidates_considered,
        document_id=document_id,
        occurred_at=(
            _normalize_utc(occurred_at, "probe occurred_at") if occurred_at is not None else None
        ),
    )


async def _signals_probe(
    client: httpx.AsyncClient,
    *,
    source: SourceName,
    active_only: bool,
) -> CorpusVisibilityProbe:
    response = await client.get(
        "/v1/signals",
        params={
            "limit": 1,
            "source": source,
            "active_only": str(active_only).lower(),
        },
    )
    if not response.is_success:
        raise RuntimeError(
            f"signal projection probe for {source} returned HTTP {response.status_code}"
        )
    result = SignalsResponse.model_validate_json(response.content)
    if result.count != len(result.items) or result.count > 1:
        raise RuntimeError("signal projection probe returned inconsistent result counts")
    if any(item.event.source != source for item in result.items):
        raise RuntimeError("signal projection probe returned a different source")
    item = result.items[0] if result.items else None
    return _visibility_probe(
        kind="signal_projection",
        active_only=active_only,
        response=response,
        document_id=f"{source}:{item.event.event_id}" if item is not None else None,
        occurred_at=item.event.occurred_at if item is not None else None,
    )


async def _search_probe(
    client: httpx.AsyncClient,
    pacer: RetrievalApiPacer,
    query: EvaluationQuery,
    *,
    kind: Literal["retrieval_index", "evaluation_query"],
) -> tuple[CorpusVisibilityProbe, str, str]:
    response, _latency_ms = await pacer.get(
        client,
        params=query_parameters(query, mode="dense", pool_depth=1),
    )
    if not response.is_success:
        raise RuntimeError(f"dense probe for {query.query_id} returned HTTP {response.status_code}")
    result = SearchResponse.model_validate_json(response.content)
    expected = expected_parameters(query, mode="dense", pool_depth=1)
    if result.ranking_mode != "dense" or result.parameters != expected:
        raise RuntimeError("retrieval API did not echo the exact dense diagnostic request")
    if result.count != len(result.items) or result.count > 1:
        raise RuntimeError("dense diagnostic probe returned inconsistent result counts")
    if result.candidates_considered < result.count:
        raise RuntimeError("dense diagnostic probe returned an invalid candidate count")
    item = result.items[0] if result.items else None
    return (
        _visibility_probe(
            kind=kind,
            active_only=query.filters.active_only,
            response=response,
            candidates_considered=result.candidates_considered,
            document_id=(
                f"{item.event.source}:{item.event.event_id}" if item is not None else None
            ),
            occurred_at=item.event.occurred_at if item is not None else None,
        ),
        result.embedding_model,
        result.ranking_rule,
    )


def _source_query(source: SourceName, *, active_only: bool) -> EvaluationQuery:
    suffix = "current" if active_only else "retained"
    return EvaluationQuery(
        query_id=f"diagnostic-{source}-{suffix}",
        text="operational source inventory",
        slices=("diagnostic",),
        filters=EvaluationFilters(source=source, active_only=active_only),
    )


def _source_blockers(
    freshness: SourceFreshnessItem | None,
    current_signals: CorpusVisibilityProbe,
    retained_signals: CorpusVisibilityProbe,
    current_index: CorpusVisibilityProbe,
    retained_index: CorpusVisibilityProbe,
) -> tuple[SourceBlocker, ...]:
    blockers: list[SourceBlocker] = []
    if freshness is None:
        blockers.append("source_poll_not_configured")
    elif not freshness.passed:
        blockers.append("source_freshness_failed")
    if retained_signals.returned_count == 0:
        blockers.append("signal_projection_empty")
    if retained_index.returned_count == 0:
        blockers.append("retrieval_index_empty")
    if current_signals.returned_count == 1 and current_index.returned_count == 0:
        blockers.append("current_projection_not_indexed")
    return tuple(sorted(blockers))


def _query_failure_reason(
    query: EvaluationQuery,
    source_readiness: dict[SourceName, SourceRetrievalReadiness],
) -> QueryFailureReason:
    source = query.filters.source
    if source is None:
        return "no_eligible_candidate"
    readiness = source_readiness[source]
    if query.filters.active_only and readiness.current_index.returned_count == 0:
        return "no_active_indexed_documents"
    if not query.filters.active_only and readiness.retained_index.returned_count == 0:
        return "no_indexed_documents"
    if not readiness.passed:
        return "source_pipeline_unready"
    return "no_eligible_candidate"


async def diagnose_retrieval_readiness(
    query_set: EvaluationQuerySet,
    *,
    base_url: str,
    expected_commit_sha: str,
    client: httpx.AsyncClient | None = None,
    sleeper: Sleeper = asyncio.sleep,
    on_rate_limit_wait: WaitReporter | None = None,
) -> RetrievalCaptureReadinessReport:
    """Trace required sources through poll, projection, index, and exact query eligibility."""
    if re.fullmatch(r"[0-9a-f]{40}", expected_commit_sha) is None:
        raise ValueError("expected commit SHA must be 40 lowercase hexadecimal characters")
    endpoint = safe_endpoint(base_url)
    owns_client = client is None
    active_client = client or httpx.AsyncClient(base_url=endpoint, timeout=30.0)
    pacer = RetrievalApiPacer(sleeper=sleeper, report_wait=on_rate_limit_wait)
    embedding_models: set[str] = set()
    ranking_rules: set[str] = set()
    try:
        health_response = await active_client.get("/healthz")
        readiness_response = await active_client.get("/readyz")
        if not health_response.is_success or not readiness_response.is_success:
            raise RuntimeError(
                "deployment health/readiness check failed: "
                f"health={health_response.status_code}, readiness={readiness_response.status_code}"
            )
        health = HealthResponse.model_validate_json(health_response.content)
        readiness = HealthResponse.model_validate_json(readiness_response.content)
        if health.status != "ok" or readiness.status != "ready":
            raise RuntimeError("deployment returned an unexpected health/readiness status")
        if health.version != readiness.version or health.commit_sha != readiness.commit_sha:
            raise RuntimeError("health and readiness endpoints identify different deployments")
        if health.commit_sha != expected_commit_sha:
            raise RuntimeError(
                f"deployment commit mismatch: expected {expected_commit_sha}, "
                f"observed {health.commit_sha}"
            )

        freshness_response = await active_client.get("/v1/source-freshness")
        if not freshness_response.is_success:
            raise RuntimeError(
                f"source freshness probe returned HTTP {freshness_response.status_code}"
            )
        freshness = SourceFreshnessResponse.model_validate_json(freshness_response.content)
        freshness_by_source = {item.source: item for item in freshness.items}
        required_sources = tuple(
            sorted(
                {
                    query.filters.source
                    for query in query_set.queries
                    if query.filters.source is not None
                }
            )
        )

        source_items: list[SourceRetrievalReadiness] = []
        for source in required_sources:
            current_signals = await _signals_probe(active_client, source=source, active_only=True)
            retained_signals = await _signals_probe(active_client, source=source, active_only=False)
            current_index, model, rule = await _search_probe(
                active_client,
                pacer,
                _source_query(source, active_only=True),
                kind="retrieval_index",
            )
            embedding_models.add(model)
            ranking_rules.add(rule)
            retained_index, model, rule = await _search_probe(
                active_client,
                pacer,
                _source_query(source, active_only=False),
                kind="retrieval_index",
            )
            embedding_models.add(model)
            ranking_rules.add(rule)
            source_freshness = freshness_by_source.get(source)
            blockers = _source_blockers(
                source_freshness,
                current_signals,
                retained_signals,
                current_index,
                retained_index,
            )
            source_items.append(
                SourceRetrievalReadiness(
                    source=source,
                    freshness=source_freshness,
                    current_signals=current_signals,
                    retained_signals=retained_signals,
                    current_index=current_index,
                    retained_index=retained_index,
                    blocking_reasons=blockers,
                    passed=not blockers,
                )
            )

        source_readiness = {item.source: item for item in source_items}
        query_items: list[QueryRetrievalReadiness] = []
        for query in query_set.queries:
            probe, model, rule = await _search_probe(
                active_client,
                pacer,
                query,
                kind="evaluation_query",
            )
            embedding_models.add(model)
            ranking_rules.add(rule)
            passed = probe.returned_count == 1
            query_items.append(
                QueryRetrievalReadiness(
                    query_id=query.query_id,
                    source=query.filters.source,
                    active_only=query.filters.active_only,
                    probe=probe,
                    failure_reason=(
                        None if passed else _query_failure_reason(query, source_readiness)
                    ),
                    passed=passed,
                )
            )
    finally:
        if owns_client:
            await active_client.aclose()

    if len(embedding_models) != 1 or len(ranking_rules) != 1:
        raise RuntimeError("retrieval model or dense ranking rule changed during diagnostics")
    blocked_sources = tuple(item.source for item in source_items if not item.passed)
    empty_query_ids = tuple(item.query_id for item in query_items if not item.passed)
    query_set_sha256 = canonical_sha256(query_set)
    embedding_model = next(iter(embedding_models))
    ranking_rule = next(iter(ranking_rules))
    generated_at = datetime.now(UTC)
    ready_for_capture = not blocked_sources
    draft = RetrievalCaptureReadinessReport.model_construct(
        report_id="retrieval-readiness-" + "0" * 20,
        report_sha256="0" * 64,
        query_set_id=query_set.query_set_id,
        query_set_sha256=query_set_sha256,
        endpoint=endpoint,
        expected_commit_sha=expected_commit_sha,
        observed_commit_sha=health.commit_sha,
        application_version=health.version,
        embedding_model=embedding_model,
        ranking_rule=ranking_rule,
        source_freshness_generated_at=freshness.generated_at,
        generated_at=generated_at,
        sources=tuple(source_items),
        queries=tuple(query_items),
        blocked_sources=blocked_sources,
        empty_query_ids=empty_query_ids,
        ready_for_capture=ready_for_capture,
    )
    digest = retrieval_readiness_sha256(draft)
    return RetrievalCaptureReadinessReport(
        report_id=f"retrieval-readiness-{digest[:20]}",
        report_sha256=digest,
        query_set_id=query_set.query_set_id,
        query_set_sha256=query_set_sha256,
        endpoint=endpoint,
        expected_commit_sha=expected_commit_sha,
        observed_commit_sha=health.commit_sha,
        application_version=health.version,
        embedding_model=embedding_model,
        ranking_rule=ranking_rule,
        source_freshness_generated_at=freshness.generated_at,
        generated_at=generated_at,
        sources=tuple(source_items),
        queries=tuple(query_items),
        blocked_sources=blocked_sources,
        empty_query_ids=empty_query_ids,
        ready_for_capture=ready_for_capture,
    )


def render_retrieval_readiness_markdown(report: RetrievalCaptureReadinessReport) -> str:
    """Render the source-to-query decision without turning it into a quality claim."""

    def visible(probe: CorpusVisibilityProbe) -> str:
        return "yes" if probe.returned_count else "no"

    status = (
        "BLOCKED"
        if not report.ready_for_capture
        else "READY WITH QUERY GAPS"
        if report.empty_query_ids
        else "READY"
    )
    lines = [
        f"# Retrieval capture readiness: {report.report_id}",
        "",
        f"> **Capture status: {status}.** Point-in-time pipeline evidence only.",
        "",
        f"- Query set: `{report.query_set_id}`",
        f"- Query-set SHA-256: `{report.query_set_sha256}`",
        f"- Endpoint: `{report.endpoint}`",
        f"- Deployment commit: `{report.observed_commit_sha}`",
        f"- Application version: `{report.application_version}`",
        f"- Embedding model: `{report.embedding_model}`",
        f"- Dense ranking rule: `{report.ranking_rule}`",
        f"- Generated: `{report.generated_at.isoformat()}`",
        "",
        "## Source pipeline",
        "",
        "| Source | Fresh | Current signals | Retained signals | Current index | Retained index | Status |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for source_item in report.sources:
        reasons = ", ".join(f"`{reason}`" for reason in source_item.blocking_reasons)
        lines.append(
            f"| {source_item.source} "
            f"| {'yes' if source_item.freshness and source_item.freshness.passed else 'no'} "
            f"| {visible(source_item.current_signals)} "
            f"| {visible(source_item.retained_signals)} "
            f"| {visible(source_item.current_index)} "
            f"| {visible(source_item.retained_index)} "
            f"| {'pass' if source_item.passed else reasons} |"
        )
    lines.extend(
        [
            "",
            "## Exact query eligibility",
            "",
            "| Query | Source | Active only | Dense candidate | Status |",
            "| --- | --- | ---: | ---: | --- |",
        ]
    )
    for query_item in report.queries:
        lines.append(
            f"| `{query_item.query_id}` | {query_item.source or 'all'} | "
            f"{'yes' if query_item.active_only else 'no'} | {visible(query_item.probe)} | "
            f"{'pass' if query_item.passed else f'`{query_item.failure_reason}`'} |"
        )
    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {caveat}" for caveat in report.caveats)
    return "\n".join(lines) + "\n"

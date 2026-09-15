"""Production ASGI process entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING

from atlas_pulse.agent_ledger import PostgresAgentRunLedger
from atlas_pulse.api import create_app
from atlas_pulse.config import get_settings
from atlas_pulse.logging import configure_logging
from atlas_pulse.projections import PostgresSignalStore
from atlas_pulse.rate_limit import ApiRateLimitConfig, RateLimitPolicy, ValkeyRateLimiter
from atlas_pulse.retrieval import FastEmbedProvider, HybridSearchService, PostgresRetrievalStore
from atlas_pulse.source_poll_store import ValkeySourcePollStore
from atlas_pulse.streams import ValkeyEventBus
from atlas_pulse.telemetry import configure_telemetry, instrument_fastapi

if TYPE_CHECKING:
    from atlas_pulse.agent_trajectory import AgentReleaseAssessment


def _load_agent_release_assessment() -> AgentReleaseAssessment | None:
    if settings.agent_release_assessment_path is None:
        return None
    from atlas_pulse.agent_trajectory import load_agent_release_assessment

    return load_agent_release_assessment(settings.agent_release_assessment_path)


settings = get_settings()
configure_logging(settings.log_level)
configure_telemetry(settings)

event_bus = ValkeyEventBus(
    url=settings.valkey_url,
    stream=settings.event_stream,
    max_length=settings.stream_max_length,
    dedupe_ttl_seconds=settings.dedupe_ttl_seconds,
)
signal_store = PostgresSignalStore(database_url=settings.database_url)
retrieval_store = PostgresRetrievalStore(database_url=settings.database_url)
embedder = FastEmbedProvider(
    model_name=settings.embedding_model,
    dimensions=settings.embedding_dimensions,
    cache_dir=str(settings.embedding_cache_dir),
    threads=settings.embedding_threads,
    model_path=str(settings.embedding_model_path) if settings.embedding_model_path else None,
    local_files_only=settings.embedding_local_files_only,
)
search_service = HybridSearchService(store=retrieval_store, embedder=embedder)
agent_run_ledger = PostgresAgentRunLedger(
    database_url=settings.database_url,
    trusted_key_ids=settings.agent_approval_trusted_key_ids,
)
source_poll_store = ValkeySourcePollStore(
    url=settings.valkey_url,
    history_stream=settings.source_poll_history_stream,
    history_max_length=settings.source_poll_history_max_length,
)
rate_limiter = ValkeyRateLimiter(url=settings.valkey_url)
rate_limit_config = ApiRateLimitConfig(
    client_secret=settings.api_rate_limit_client_secret.get_secret_value().encode(),
    general_policy=RateLimitPolicy(
        name="general",
        requests=settings.api_rate_limit_requests,
        window_seconds=settings.api_rate_limit_window_seconds,
    ),
    expensive_policy=RateLimitPolicy(
        name="expensive",
        requests=settings.api_expensive_rate_limit_requests,
        window_seconds=settings.api_rate_limit_window_seconds,
    ),
)
agent_release_assessment = _load_agent_release_assessment()
app = create_app(
    event_bus,
    signal_store,
    search_service,
    agent_run_ledger,
    agent_release_assessment,
    build_commit_sha=settings.build_commit_sha,
    source_poll_store=source_poll_store,
    source_poll_policies=settings.source_poll_policies(),
    rate_limiter=rate_limiter,
    rate_limit_config=rate_limit_config,
)
instrument_fastapi(app)

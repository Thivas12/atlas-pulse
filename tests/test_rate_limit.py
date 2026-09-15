"""Request-budget unit and API-boundary tests."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from atlas_pulse.api import create_app
from atlas_pulse.rate_limit import (
    ApiRateLimitConfig,
    RateLimitDecision,
    RateLimiterUnavailable,
    RateLimitPolicy,
    ValkeyRateLimiter,
)
from atlas_pulse.streams.memory import InMemoryEventBus

_DEFAULT_RESPONSE = object()


def _config(*, general: int = 10, expensive: int = 2) -> ApiRateLimitConfig:
    return ApiRateLimitConfig(
        client_secret=b"test-rate-limit-client-secret-value",
        general_policy=RateLimitPolicy(
            name="general",
            requests=general,
            window_seconds=60,
        ),
        expensive_policy=RateLimitPolicy(
            name="expensive",
            requests=expensive,
            window_seconds=60,
        ),
    )


class FakeRateLimitClient:
    def __init__(
        self,
        response: object | Callable[[int], object] = _DEFAULT_RESPONSE,
    ) -> None:
        self.response = response
        self.calls: list[tuple[str, int, tuple[str | bytes | int, ...]]] = []
        self.closed = False

    async def eval(self, script: str, numkeys: int, *keys_and_args: str | bytes | int) -> object:
        self.calls.append((script, numkeys, keys_and_args))
        if callable(self.response):
            return self.response(len(self.calls))
        if self.response is not _DEFAULT_RESPONSE:
            return self.response
        return [len(self.calls), 59_001]

    async def aclose(self) -> None:
        self.closed = True


class StubRateLimiter:
    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.counts: dict[tuple[str, str], int] = {}
        self.closed = False

    async def consume(self, client_key: str, policy: RateLimitPolicy) -> RateLimitDecision:
        if self.unavailable:
            raise RateLimiterUnavailable("offline")
        key = (client_key, policy.name)
        count = self.counts.get(key, 0) + 1
        self.counts[key] = count
        return RateLimitDecision(
            policy=policy.name,
            allowed=count <= policy.requests,
            limit=policy.requests,
            remaining=max(policy.requests - count, 0),
            reset_after_seconds=60,
        )

    async def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("General", 1, 1), "lowercase ASCII"),
        (("général", 1, 1), "lowercase ASCII"),
        (("general", 0, 1), "requests must be positive"),
        (("general", 1, 0), "window must be positive"),
    ],
)
def test_rate_limit_policy_rejects_invalid_values(
    arguments: tuple[str, int, int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        RateLimitPolicy(*arguments)


def test_rate_limit_config_pseudonymizes_canonical_addresses_and_selects_routes() -> None:
    config = _config()

    ipv6 = config.client_key(forwarded_address="2001:0db8::1", peer_address="127.0.0.1")
    canonical_ipv6 = config.client_key(
        forwarded_address="2001:db8:0:0:0:0:0:1",
        peer_address="127.0.0.1",
    )
    fallback = config.client_key(forwarded_address="not-an-ip", peer_address="127.0.0.1")
    direct = config.client_key(forwarded_address=None, peer_address="127.0.0.1")
    mapped = config.client_key(forwarded_address="::ffff:127.0.0.1", peer_address=None)
    unresolved = config.client_key(forwarded_address="bad", peer_address="also-bad")

    assert ipv6 == canonical_ipv6
    assert fallback == direct
    assert mapped == direct
    assert unresolved != direct
    assert len(ipv6) == 64
    assert "2001" not in ipv6
    assert [policy.name for policy in config.policies_for_path("/v1/search")] == [
        "general",
        "expensive",
    ]
    assert [policy.name for policy in config.policies_for_path("/v1/events")] == ["general"]
    assert config.policies_for_path("/healthz") == ()


@pytest.mark.parametrize(
    "config",
    [
        lambda: ApiRateLimitConfig(
            client_secret=b"short",
            general_policy=RateLimitPolicy("general", 10, 60),
            expensive_policy=RateLimitPolicy("expensive", 2, 60),
        ),
        lambda: ApiRateLimitConfig(
            client_secret=b"a" * 32,
            general_policy=RateLimitPolicy("same", 10, 60),
            expensive_policy=RateLimitPolicy("same", 2, 60),
        ),
        lambda: ApiRateLimitConfig(
            client_secret=b"a" * 32,
            general_policy=RateLimitPolicy("general", 2, 60),
            expensive_policy=RateLimitPolicy("expensive", 3, 60),
        ),
        lambda: ApiRateLimitConfig(
            client_secret=b"a" * 32,
            general_policy=RateLimitPolicy("general", 10, 60),
            expensive_policy=RateLimitPolicy("expensive", 2, 30),
        ),
        lambda: ApiRateLimitConfig(
            client_secret=b"a" * 32,
            general_policy=RateLimitPolicy("general", 10, 60),
            expensive_policy=RateLimitPolicy("expensive", 2, 60),
            client_header="X-Forwarded-For",
        ),
    ],
)
def test_rate_limit_config_rejects_unreviewed_boundaries(
    config: Callable[[], ApiRateLimitConfig],
) -> None:
    with pytest.raises(ValueError):
        config()


async def test_valkey_limiter_consumes_atomic_budget_without_raw_client_data() -> None:
    client = FakeRateLimitClient()
    limiter = ValkeyRateLimiter(url="valkey://unused", client=client)
    policy = RateLimitPolicy("general", 2, 60)
    client_key = "a" * 64

    first = await limiter.consume(client_key, policy)
    second = await limiter.consume(client_key, policy)
    third = await limiter.consume(client_key, policy)

    assert first == RateLimitDecision("general", True, 2, 1, 60)
    assert second == RateLimitDecision("general", True, 2, 0, 60)
    assert third == RateLimitDecision("general", False, 2, 0, 60)
    assert all(call[1] == 1 for call in client.calls)
    assert all(call[2] == (f"{{atlas-rate:{client_key}}}:general", 60_000) for call in client.calls)
    await limiter.close()
    assert client.closed is False


async def test_valkey_limiter_rejects_an_unhashed_client_key() -> None:
    limiter = ValkeyRateLimiter(url="valkey://unused", client=FakeRateLimitClient())

    with pytest.raises(RateLimiterUnavailable):
        await limiter.consume("203.0.113.10", RateLimitPolicy("general", 2, 60))


@pytest.mark.parametrize(
    "response",
    [None, [1], [1, 1000, 2], [0, 1000], [1, 0], [True, 1000], [b"bad", b"1000"]],
)
async def test_valkey_limiter_fails_closed_on_invalid_results(response: object) -> None:
    client = FakeRateLimitClient(response=response)
    limiter = ValkeyRateLimiter(url="valkey://unused", client=client)

    with pytest.raises(RateLimiterUnavailable):
        await limiter.consume("a" * 64, RateLimitPolicy("general", 2, 60))


async def test_valkey_limiter_closes_an_owned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeRateLimitClient()
    monkeypatch.setattr(
        "atlas_pulse.rate_limit.Valkey.from_url",
        lambda *_args, **_kwargs: client,
    )
    limiter = ValkeyRateLimiter(url="valkey://owned")

    await limiter.close()

    assert client.closed is True


async def test_api_middleware_exempts_health_and_enforces_general_budget() -> None:
    limiter = StubRateLimiter()
    app = create_app(
        InMemoryEventBus(),
        rate_limiter=limiter,
        rate_limit_config=_config(general=1, expensive=1),
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"X-Atlas-Client-IP": "203.0.113.10"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/healthz", headers=headers)
        allowed = await client.get("/v1/events", headers=headers)
        denied = await client.get("/v1/events", headers=headers)

    assert health.status_code == 200
    assert "x-ratelimit-limit" not in health.headers
    assert allowed.status_code == 200
    assert allowed.headers["x-ratelimit-policy"] == "general"
    assert allowed.headers["x-ratelimit-remaining"] == "0"
    assert allowed.headers["x-ratelimit-reset-after"] == "60"
    assert denied.status_code == 429
    assert denied.json() == {"detail": "request rate limit exceeded"}
    assert denied.headers["retry-after"] == "60"
    assert denied.headers["cache-control"] == "no-store"
    assert len(limiter.counts) == 1


async def test_expensive_api_routes_consume_both_budgets_before_handlers() -> None:
    limiter = StubRateLimiter()
    app = create_app(
        InMemoryEventBus(),
        rate_limiter=limiter,
        rate_limit_config=_config(general=10, expensive=1),
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"X-Atlas-Client-IP": "198.51.100.20"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/v1/search", params={"q": "earthquake"}, headers=headers)
        second = await client.get("/v1/search", params={"q": "earthquake"}, headers=headers)

    assert first.status_code == 503
    assert first.json() == {"detail": "retrieval index unavailable"}
    assert first.headers["x-ratelimit-policy"] == "expensive"
    assert second.status_code == 429
    assert second.headers["x-ratelimit-policy"] == "expensive"
    assert sorted(count for count in limiter.counts.values()) == [2, 2]


async def test_api_middleware_fails_closed_when_request_budget_is_unavailable() -> None:
    limiter = StubRateLimiter(unavailable=True)
    app = create_app(
        InMemoryEventBus(),
        rate_limiter=limiter,
        rate_limit_config=_config(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/events")

    assert response.status_code == 503
    assert response.json() == {"detail": "request rate limit unavailable"}
    assert response.headers["retry-after"] == "1"
    assert response.headers["cache-control"] == "no-store"


async def test_rate_limiter_configuration_and_lifespan_are_bound_together() -> None:
    limiter = StubRateLimiter()
    config = _config()

    with pytest.raises(ValueError, match="must be supplied together"):
        create_app(InMemoryEventBus(), rate_limiter=limiter)
    with pytest.raises(ValueError, match="must be supplied together"):
        create_app(InMemoryEventBus(), rate_limit_config=config)

    app = create_app(
        InMemoryEventBus(),
        rate_limiter=limiter,
        rate_limit_config=config,
    )
    async with app.router.lifespan_context(app):
        pass

    assert limiter.closed is True

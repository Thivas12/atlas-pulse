"""Atomic Valkey-backed request budgets for the public read API."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from hmac import new as hmac_new
from ipaddress import IPv6Address, ip_address
from string import ascii_lowercase
from typing import Protocol, cast

from valkey.asyncio import Valkey
from valkey.exceptions import ValkeyError

_CONSUME_BUDGET = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('PTTL', KEYS[1])
if ttl < 1 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""
_EXPENSIVE_API_PATHS = frozenset(
    {
        "/v1/agent-runs/preflight",
        "/v1/evidence-packs",
        "/v1/incidents",
        "/v1/search",
    }
)


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """One named fixed-window request budget."""

    name: str
    requests: int
    window_seconds: int

    def __post_init__(self) -> None:
        if not self.name or any(character not in ascii_lowercase + "-" for character in self.name):
            raise ValueError("rate-limit policy names require lowercase ASCII letters or '-'")
        if self.requests < 1:
            raise ValueError("rate-limit requests must be positive")
        if self.window_seconds < 1:
            raise ValueError("rate-limit window must be positive")


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Atomic result for one consumed request budget."""

    policy: str
    allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: int


@dataclass(frozen=True, slots=True)
class ApiRateLimitConfig:
    """Trusted client identity and route policies for the public API."""

    client_secret: bytes = field(repr=False)
    general_policy: RateLimitPolicy
    expensive_policy: RateLimitPolicy
    client_header: str = "X-Atlas-Client-IP"

    def __post_init__(self) -> None:
        if not 32 <= len(self.client_secret) <= 128:
            raise ValueError("rate-limit client secret must contain 32-128 bytes")
        if self.general_policy.name == self.expensive_policy.name:
            raise ValueError("rate-limit policies require distinct names")
        if self.expensive_policy.requests > self.general_policy.requests:
            raise ValueError("expensive request budget cannot exceed the general budget")
        if self.expensive_policy.window_seconds != self.general_policy.window_seconds:
            raise ValueError("API rate-limit policies must use one shared window")
        if self.client_header != "X-Atlas-Client-IP":
            raise ValueError("the reviewed client identity header cannot be changed")

    @staticmethod
    def _canonical_address(value: str | None) -> str | None:
        if value is None:
            return None
        try:
            address = ip_address(value.strip())
            if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
                return address.ipv4_mapped.compressed
            return address.compressed
        except ValueError:
            return None

    def client_key(self, *, forwarded_address: str | None, peer_address: str | None) -> str:
        """Pseudonymize the normalized trusted header, falling back to the direct peer."""
        address = self._canonical_address(forwarded_address)
        if address is None:
            address = self._canonical_address(peer_address) or "unresolved"
        return hmac_new(self.client_secret, address.encode(), sha256).hexdigest()

    def policies_for_path(self, path: str) -> tuple[RateLimitPolicy, ...]:
        """Return the general budget plus the expensive-route budget when applicable."""
        if not path.startswith("/v1/"):
            return ()
        if path in _EXPENSIVE_API_PATHS:
            return self.general_policy, self.expensive_policy
        return (self.general_policy,)


class RateLimiterUnavailable(RuntimeError):
    """The request budget could not be evaluated safely."""


class AsyncRateLimitClient(Protocol):
    """Narrow Valkey client surface required by the limiter."""

    async def eval(
        self, script: str, numkeys: int, *keys_and_args: str | bytes | int
    ) -> object: ...

    async def aclose(self) -> None: ...


class RateLimiter(Protocol):
    """Request-budget interface consumed by the HTTP middleware."""

    async def consume(self, client_key: str, policy: RateLimitPolicy) -> RateLimitDecision: ...

    async def close(self) -> None: ...


def _integer(value: object, label: str) -> int:
    if isinstance(value, bytes | str):
        try:
            return int(value)
        except ValueError as error:
            raise ValueError(f"rate-limit {label} must be an integer") from error
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"rate-limit {label} must be an integer")


class ValkeyRateLimiter:
    """Consume fixed-window budgets atomically across API processes."""

    def __init__(self, *, url: str, client: AsyncRateLimitClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or cast(
            AsyncRateLimitClient,
            Valkey.from_url(url, decode_responses=False),
        )

    @staticmethod
    def _budget_key(client_key: str, policy: RateLimitPolicy) -> str:
        if len(client_key) != 64 or any(
            character not in "0123456789abcdef" for character in client_key
        ):
            raise ValueError("rate-limit client keys must be lowercase SHA-256 digests")
        return f"{{atlas-rate:{client_key}}}:{policy.name}"

    @staticmethod
    def _decision(result: object, policy: RateLimitPolicy) -> RateLimitDecision:
        if not isinstance(result, Sequence) or isinstance(result, (str, bytes)):
            raise TypeError("rate-limit script returned an invalid response")
        if len(result) != 2:
            raise ValueError("rate-limit script returned an unexpected response length")
        count = _integer(result[0], "count")
        ttl_milliseconds = _integer(result[1], "TTL")
        if count < 1 or ttl_milliseconds < 1:
            raise ValueError("rate-limit script returned values outside the valid range")
        remaining = max(policy.requests - count, 0)
        return RateLimitDecision(
            policy=policy.name,
            allowed=count <= policy.requests,
            limit=policy.requests,
            remaining=remaining,
            reset_after_seconds=max(1, (ttl_milliseconds + 999) // 1_000),
        )

    async def consume(self, client_key: str, policy: RateLimitPolicy) -> RateLimitDecision:
        """Increment and evaluate one client's named budget."""
        try:
            result = await self._client.eval(
                _CONSUME_BUDGET,
                1,
                self._budget_key(client_key, policy),
                policy.window_seconds * 1_000,
            )
            return self._decision(result, policy)
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            TypeError,
            ValueError,
            ValkeyError,
        ) as error:
            raise RateLimiterUnavailable("request budget unavailable") from error

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

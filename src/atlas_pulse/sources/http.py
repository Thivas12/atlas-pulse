"""Shared bounded-retry HTTP transport for authoritative public sources."""

from datetime import UTC, datetime
from typing import Self

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from atlas_pulse.sources.base import FetchedDocument


class RetryableSourceError(RuntimeError):
    """A transient source failure that may succeed on another attempt."""


class RetryingHttpClient:
    """Fetch source documents with bounded retries and explicit identity headers."""

    def __init__(
        self,
        *,
        source_name: str,
        url: str,
        timeout_seconds: float,
        max_attempts: int,
        user_agent: str,
        accept: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._source_name = source_name
        self._url = url
        self._max_attempts = max_attempts
        self._headers = {"Accept": accept, "User-Agent": user_agent}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            headers=self._headers,
        )

    async def fetch(self) -> FetchedDocument:
        """Retry transport, rate-limit, and server failures but not permanent 4xx."""
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
            retry=retry_if_exception_type((httpx.TransportError, RetryableSourceError)),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                response = await self._client.get(self._url, headers=self._headers)
                if response.status_code == 429 or response.status_code >= 500:
                    raise RetryableSourceError(
                        f"{self._source_name} returned retryable HTTP {response.status_code}"
                    )
                response.raise_for_status()
                return FetchedDocument(
                    raw=response.content,
                    fetched_at=datetime.now(UTC),
                    source_url=str(response.url),
                    content_type=response.headers.get("content-type"),
                )
        raise AssertionError("retry loop completed without a response")  # pragma: no cover

    async def close(self) -> None:
        """Close only clients created by this transport."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

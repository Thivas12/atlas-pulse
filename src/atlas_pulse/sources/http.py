"""Shared bounded-retry HTTP transport for authoritative public sources."""

from datetime import UTC, datetime
from typing import Self

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from atlas_pulse.sources.base import FetchedDocument


class SourceFetchError(RuntimeError):
    """Credential-safe source failure with the number of actual transport attempts."""

    def __init__(self, message: str, *, attempt_count: int) -> None:
        super().__init__(message)
        if attempt_count < 1:
            raise ValueError("source fetch attempt count must be positive")
        self.attempt_count = attempt_count


class RetryableSourceError(SourceFetchError):
    """A transient source failure that may succeed on another attempt."""


class PermanentSourceError(SourceFetchError):
    """A non-retryable source response reported without leaking its request URL."""


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
        public_source_url: str | None = None,
        max_response_bytes: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if max_response_bytes is not None and max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        self._source_name = source_name
        self._url = url
        self._public_source_url = public_source_url
        self._max_response_bytes = max_response_bytes
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
            retry=retry_if_exception_type(RetryableSourceError),
            reraise=True,
        )
        async for attempt in retrying:
            attempt_number = attempt.retry_state.attempt_number
            with attempt:
                try:
                    async with self._client.stream(
                        "GET", self._url, headers=self._headers
                    ) as response:
                        if response.status_code == 429 or response.status_code >= 500:
                            raise RetryableSourceError(
                                f"{self._source_name} returned retryable HTTP "
                                f"{response.status_code}",
                                attempt_count=attempt_number,
                            )
                        if not 200 <= response.status_code < 300:
                            raise PermanentSourceError(
                                f"{self._source_name} returned permanent HTTP "
                                f"{response.status_code}",
                                attempt_count=attempt_number,
                            )
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            if (
                                self._max_response_bytes is not None
                                and len(raw) + len(chunk) > self._max_response_bytes
                            ):
                                raise PermanentSourceError(
                                    f"{self._source_name} response exceeded the configured byte limit",
                                    attempt_count=attempt_number,
                                )
                            raw.extend(chunk)
                        source_url = self._public_source_url or str(response.url)
                        content_type = response.headers.get("content-type")
                except httpx.TransportError as error:
                    raise RetryableSourceError(
                        f"{self._source_name} transport failed ({type(error).__name__})",
                        attempt_count=attempt_number,
                    ) from None
                return FetchedDocument(
                    raw=bytes(raw),
                    fetched_at=datetime.now(UTC),
                    source_url=source_url,
                    content_type=content_type,
                    transport_attempts=attempt_number,
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

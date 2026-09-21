"""Bounded public probes and local backup hashing for operational evidence."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import shutil
import socket
import ssl
import stat
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import dns.exception
import dns.message
import dns.query
import dns.rcode
import dns.rdatatype
import httpx
from pydantic import ValidationError

from atlas_pulse.operational_evidence.base import (
    PROBE_PATHS,
    BackupEvidence,
    BackupStorageScope,
    CertificateObservation,
    DeploymentProbeEvidence,
    DeploymentTarget,
    EndpointObservation,
    ExecutionBoundaryObservation,
    HostResourceObservation,
    ProbeFailureCode,
    ProbeName,
    ResourceSnapshotEvidence,
    ResourceSnapshotSubmission,
    ServiceHealth,
    ServiceResourceObservation,
    ServiceState,
    SourceVisibilityObservation,
    _model_sha256,
    _require_utc,
    build_resource_snapshot,
)
from atlas_pulse.projections.base import SourceName
from atlas_pulse.source_polling import SourceFreshnessItem, SourceFreshnessResponse

_MAX_RESPONSE_BYTES = 1_048_576
_READ_CHUNK_BYTES = 1_048_576
_SOURCE_NAMES = frozenset({"usgs", "nws", "firms", "gdelt"})
_PUBLIC_DOH_RESOLVERS = (
    ("https://cloudflare-dns.com/dns-query", "1.1.1.1"),
    ("https://dns.google/dns-query", "8.8.8.8"),
)


@dataclass(frozen=True, slots=True)
class _Exchange:
    status_code: int | None
    latency_ms: float
    body: bytes
    body_truncated: bool
    failure_code: ProbeFailureCode | None

    def observation(
        self,
        name: ProbeName,
        *,
        semantic_passed: bool,
        semantic_failure: ProbeFailureCode = "invalid_payload",
    ) -> EndpointObservation:
        failure = self.failure_code
        if failure is None and not semantic_passed:
            failure = semantic_failure
        passed = failure is None and semantic_passed and self.status_code == 200
        return EndpointObservation(
            name=name,
            path=PROBE_PATHS[name],
            status_code=self.status_code,
            latency_ms=self.latency_ms,
            body_bytes=len(self.body),
            response_sha256=(hashlib.sha256(self.body).hexdigest() if self.status_code else None),
            body_truncated=self.body_truncated,
            passed=passed,
            failure_code=failure,
        )


CertificateLoader = Callable[[DeploymentTarget, datetime, float], CertificateObservation]
CommandRunner = Callable[[Sequence[str], Path], str]
HostResourceLoader = Callable[[Path], HostResourceObservation]


def _json_object(body: bytes) -> dict[str, object] | None:
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        return None
    return cast(dict[str, object], value)


def _failure_code(error: httpx.HTTPError) -> ProbeFailureCode:
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    return "dns_or_connect_error"


def _exchange(client: httpx.Client, name: ProbeName) -> _Exchange:
    started = time.perf_counter()
    try:
        with client.stream("GET", PROBE_PATHS[name]) as response:
            body = bytearray()
            truncated = False
            for chunk in response.iter_bytes():
                remaining = _MAX_RESPONSE_BYTES - len(body)
                if len(chunk) > remaining:
                    body.extend(chunk[:remaining])
                    truncated = True
                    break
                body.extend(chunk)
            status_code = response.status_code
    except httpx.HTTPError as error:
        return _Exchange(
            status_code=None,
            latency_ms=(time.perf_counter() - started) * 1_000,
            body=b"",
            body_truncated=False,
            failure_code=_failure_code(error),
        )
    elapsed = (time.perf_counter() - started) * 1_000
    if truncated:
        failure: ProbeFailureCode | None = "response_too_large"
    elif status_code != 200:
        failure = "http_status"
    else:
        failure = None
    return _Exchange(status_code, elapsed, bytes(body), truncated, failure)


def _parse_health(
    exchange: _Exchange,
    *,
    expected_status: str,
    expected_version: str,
    expected_commit_sha: str,
) -> tuple[EndpointObservation, str | None, str | None]:
    document = _json_object(exchange.body) if exchange.failure_code is None else None
    if document is None:
        return exchange.observation("health", semantic_passed=False), None, None
    status = document.get("status")
    version = document.get("version")
    commit_sha = document.get("commit_sha")
    valid_shape = (
        isinstance(status, str) and isinstance(version, str) and isinstance(commit_sha, str)
    )
    passed = (
        valid_shape
        and status == expected_status
        and version == expected_version
        and commit_sha == expected_commit_sha
    )
    return (
        exchange.observation(
            "health",
            semantic_passed=passed,
            semantic_failure="unexpected_value" if valid_shape else "invalid_payload",
        ),
        version if isinstance(version, str) else None,
        commit_sha if isinstance(commit_sha, str) else None,
    )


def _parse_readiness(
    exchange: _Exchange,
    *,
    expected_version: str,
    expected_commit_sha: str,
) -> EndpointObservation:
    document = _json_object(exchange.body) if exchange.failure_code is None else None
    if document is None:
        return exchange.observation("readiness", semantic_passed=False)
    status = document.get("status")
    version = document.get("version")
    commit_sha = document.get("commit_sha")
    valid_shape = (
        isinstance(status, str) and isinstance(version, str) and isinstance(commit_sha, str)
    )
    return exchange.observation(
        "readiness",
        semantic_passed=(
            valid_shape
            and status == "ready"
            and version == expected_version
            and commit_sha == expected_commit_sha
        ),
        semantic_failure="unexpected_value" if valid_shape else "invalid_payload",
    )


def _utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _parse_events(
    exchange: _Exchange,
    *,
    observed_at: datetime,
) -> tuple[EndpointObservation, int, bool, tuple[SourceVisibilityObservation, ...]]:
    document = _json_object(exchange.body) if exchange.failure_code is None else None
    if document is None:
        return exchange.observation("events", semantic_passed=False), 0, False, ()
    count = document.get("count")
    items = document.get("items")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or not 0 <= count <= 500
        or not isinstance(items, list)
        or len(items) != count
    ):
        return exchange.observation("events", semantic_passed=False), 0, False, ()

    latest: dict[SourceName, datetime] = {}
    counts: dict[SourceName, int] = {}
    for raw_item in items:
        if not isinstance(raw_item, Mapping):
            return exchange.observation("events", semantic_passed=False), 0, False, ()
        event = raw_item.get("event")
        if not isinstance(event, Mapping):
            return exchange.observation("events", semantic_passed=False), 0, False, ()
        source_value = event.get("source")
        ingested_at = _utc_timestamp(event.get("ingested_at"))
        if source_value not in _SOURCE_NAMES or ingested_at is None:
            return exchange.observation("events", semantic_passed=False), 0, False, ()
        source = cast(SourceName, source_value)
        counts[source] = counts.get(source, 0) + 1
        latest[source] = max(latest.get(source, ingested_at), ingested_at)
    visibility = tuple(
        SourceVisibilityObservation(
            source=source,
            event_count=counts[source],
            latest_ingested_at=latest[source],
            age_seconds=(observed_at - latest[source]).total_seconds(),
        )
        for source in sorted(latest)
    )
    return exchange.observation("events", semantic_passed=True), count, count == 500, visibility


def _parse_source_freshness(
    exchange: _Exchange,
    *,
    target: DeploymentTarget,
    observed_at: datetime,
) -> tuple[EndpointObservation, datetime | None, tuple[SourceFreshnessItem, ...]]:
    if exchange.failure_code is not None:
        return exchange.observation("source_freshness", semantic_passed=False), None, ()
    try:
        response = SourceFreshnessResponse.model_validate_json(exchange.body)
    except (ValidationError, ValueError):
        return exchange.observation("source_freshness", semantic_passed=False), None, ()
    if abs((observed_at - response.generated_at).total_seconds()) > 60:
        return (
            exchange.observation(
                "source_freshness",
                semantic_passed=False,
                semantic_failure="unexpected_value",
            ),
            None,
            (),
        )
    by_source = {item.source: item for item in response.items}
    required_passed = all(
        source in by_source and by_source[source].passed for source in target.required_sources
    )
    return (
        exchange.observation(
            "source_freshness",
            semantic_passed=required_passed,
            semantic_failure="unexpected_value",
        ),
        response.generated_at,
        response.items,
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _parse_preflight(
    exchange: _Exchange,
) -> tuple[EndpointObservation, ExecutionBoundaryObservation]:
    empty = ExecutionBoundaryObservation(passed=False)
    document = _json_object(exchange.body) if exchange.failure_code is None else None
    if document is None:
        return exchange.observation("agent_preflight", semantic_passed=False), empty
    manifest = document.get("manifest")
    if not isinstance(manifest, Mapping):
        return exchange.observation("agent_preflight", semantic_passed=False), empty
    release = manifest.get("release")
    authorization = manifest.get("authorization")
    execution = manifest.get("execution")
    if not all(isinstance(value, Mapping) for value in (release, authorization, execution)):
        return exchange.observation("agent_preflight", semantic_passed=False), empty
    release_map = cast(Mapping[str, object], release)
    authorization_map = cast(Mapping[str, object], authorization)
    execution_map = cast(Mapping[str, object], execution)
    checks = authorization_map.get("checks")
    boundary = ExecutionBoundaryObservation(
        manifest_status=_optional_string(manifest.get("status")),
        release_status=_optional_string(release_map.get("status")),
        authorization_check_count=len(checks) if isinstance(checks, list) else None,
        execution_status=_optional_string(execution_map.get("status")),
        agent_model_invoked=_optional_bool(execution_map.get("agent_model_invoked")),
        agent_network_accessed=_optional_bool(execution_map.get("agent_network_accessed")),
        agent_tools_invoked=_optional_bool(execution_map.get("agent_tools_invoked")),
        answer_generated=_optional_bool(execution_map.get("answer_generated")),
        agent_side_effects_performed=_optional_bool(
            execution_map.get("agent_side_effects_performed")
        ),
        passed=(
            manifest.get("status") == "blocked"
            and release_map.get("status") == "not_supplied"
            and isinstance(checks, list)
            and len(checks) == 11
            and execution_map.get("status") == "not_started"
            and execution_map.get("agent_model_invoked") is False
            and execution_map.get("agent_network_accessed") is False
            and execution_map.get("agent_tools_invoked") is False
            and execution_map.get("answer_generated") is False
            and execution_map.get("agent_side_effects_performed") is False
        ),
    )
    return (
        exchange.observation(
            "agent_preflight",
            semantic_passed=boundary.passed,
            semantic_failure="unexpected_value",
        ),
        boundary,
    )


def _failed_certificate(observed_at: datetime, code: ProbeFailureCode) -> CertificateObservation:
    return CertificateObservation(
        checked_at=observed_at,
        hostname_verified=False,
        passed=False,
        failure_code=code,
    )


def observe_certificate(
    target: DeploymentTarget,
    observed_at: datetime,
    timeout_seconds: float,
) -> CertificateObservation:
    """Open one verified TLS connection and retain only bounded leaf metadata."""
    parsed = urlsplit(target.origin)
    assert parsed.hostname is not None
    try:
        context = ssl.create_default_context()
        with (
            socket.create_connection((parsed.hostname, 443), timeout=timeout_seconds) as raw,
            context.wrap_socket(raw, server_hostname=parsed.hostname) as wrapped,
        ):
            der = wrapped.getpeercert(binary_form=True)
            decoded = wrapped.getpeercert()
            tls_version = wrapped.version()
        if not isinstance(der, bytes) or not der or not decoded or not tls_version:
            return _failed_certificate(observed_at, "invalid_payload")
        not_before_raw = decoded.get("notBefore")
        not_after_raw = decoded.get("notAfter")
        if not isinstance(not_before_raw, str) or not isinstance(not_after_raw, str):
            return _failed_certificate(observed_at, "invalid_payload")
        not_before = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_before_raw), tz=UTC)
        not_after = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after_raw), tz=UTC)
        remaining = (not_after - observed_at).total_seconds()
        return CertificateObservation(
            checked_at=observed_at,
            leaf_sha256=hashlib.sha256(der).hexdigest(),
            not_before=not_before,
            not_after=not_after,
            remaining_seconds=remaining,
            hostname_verified=True,
            tls_version=tls_version,
            passed=not_before <= observed_at < not_after,
        )
    except TimeoutError:
        return _failed_certificate(observed_at, "timeout")
    except ssl.SSLError:
        return _failed_certificate(observed_at, "tls_error")
    except OSError:
        return _failed_certificate(observed_at, "dns_or_connect_error")


def _ordered_global_addresses(values: Sequence[str]) -> tuple[str, ...]:
    try:
        addresses = {ipaddress.ip_address(value) for value in values}
    except ValueError:
        return ()
    if not addresses or any(not address.is_global for address in addresses):
        return ()
    return tuple(
        str(address) for address in sorted(addresses, key=lambda item: (item.version, int(item)))
    )


def _system_public_addresses(origin: str) -> tuple[str, ...]:
    parsed = urlsplit(origin)
    assert parsed.hostname is not None
    try:
        answers = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except OSError:
        return ()
    return _ordered_global_addresses(tuple(str(answer[4][0]) for answer in answers))


def _funnel_public_addresses(origin: str, timeout_seconds: float) -> tuple[str, ...]:
    """Resolve a Funnel name through public DNS rather than local MagicDNS."""
    parsed = urlsplit(origin)
    assert parsed.hostname is not None
    deadline = time.monotonic() + timeout_seconds
    addresses: list[str] = []
    for record_type in (dns.rdatatype.A, dns.rdatatype.AAAA):
        query = dns.message.make_query(parsed.hostname, record_type)
        for resolver_url, bootstrap_address in _PUBLIC_DOH_RESOLVERS:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                response = dns.query.https(
                    query,
                    resolver_url,
                    timeout=min(remaining, 5.0),
                    bootstrap_address=bootstrap_address,
                    verify=True,
                )
            except (dns.exception.DNSException, httpx.HTTPError, OSError, ValueError):
                continue
            if response.rcode() != dns.rcode.NOERROR:
                continue
            records = [
                str(record)
                for answer in response.answer
                if answer.rdtype == record_type
                for record in answer
            ]
            addresses.extend(records)
            if records:
                break
    return _ordered_global_addresses(addresses)


def _public_addresses(target: DeploymentTarget, timeout_seconds: float) -> tuple[str, ...]:
    if target.environment == "workstation-funnel-public":
        return _funnel_public_addresses(target.origin, timeout_seconds)
    return _system_public_addresses(target.origin)


def _pinned_exchange(
    hostname: str,
    addresses: tuple[str, ...],
    name: ProbeName,
    timeout_seconds: float,
) -> _Exchange:
    """Issue one fixed HTTPS request to a prevalidated global address."""
    started = time.perf_counter()
    deadline = time.monotonic() + timeout_seconds
    failures: set[ProbeFailureCode] = set()
    request = (
        f"GET {PROBE_PATHS[name]} HTTP/1.1\r\n"
        f"Host: {hostname}\r\n"
        "Accept: application/json\r\n"
        "Connection: close\r\n"
        "User-Agent: AtlasPulse-Operational-Evidence/1.1\r\n\r\n"
    ).encode("ascii")
    for address in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            failures.add("timeout")
            break
        try:
            context = ssl.create_default_context()
            with (
                socket.create_connection((address, 443), timeout=remaining) as raw,
                context.wrap_socket(raw, server_hostname=hostname) as wrapped,
            ):
                wrapped.settimeout(max(deadline - time.monotonic(), 0.001))
                wrapped.sendall(request)
                response = http.client.HTTPResponse(wrapped)
                response.begin()
                body = response.read(_MAX_RESPONSE_BYTES + 1)
                status_code = response.status
        except TimeoutError:
            failures.add("timeout")
            continue
        except ssl.SSLError:
            failures.add("tls_error")
            continue
        except (OSError, http.client.HTTPException):
            failures.add("dns_or_connect_error")
            continue
        truncated = len(body) > _MAX_RESPONSE_BYTES
        bounded = body[:_MAX_RESPONSE_BYTES]
        elapsed = (time.perf_counter() - started) * 1_000
        if truncated:
            failure: ProbeFailureCode | None = "response_too_large"
        elif status_code != 200:
            failure = "http_status"
        else:
            failure = None
        return _Exchange(status_code, elapsed, bounded, truncated, failure)
    failure_code: ProbeFailureCode
    if "tls_error" in failures:
        failure_code = "tls_error"
    elif "timeout" in failures:
        failure_code = "timeout"
    else:
        failure_code = "dns_or_connect_error"
    return _Exchange(
        status_code=None,
        latency_ms=(time.perf_counter() - started) * 1_000,
        body=b"",
        body_truncated=False,
        failure_code=failure_code,
    )


def _observe_certificate_at_addresses(
    target: DeploymentTarget,
    observed_at: datetime,
    timeout_seconds: float,
    addresses: tuple[str, ...],
) -> CertificateObservation:
    parsed = urlsplit(target.origin)
    assert parsed.hostname is not None
    deadline = time.monotonic() + timeout_seconds
    failures: set[ProbeFailureCode] = set()
    for address in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            failures.add("timeout")
            break
        try:
            context = ssl.create_default_context()
            with (
                socket.create_connection((address, 443), timeout=remaining) as raw,
                context.wrap_socket(raw, server_hostname=parsed.hostname) as wrapped,
            ):
                der = wrapped.getpeercert(binary_form=True)
                decoded = wrapped.getpeercert()
                tls_version = wrapped.version()
            if not isinstance(der, bytes) or not der or not decoded or not tls_version:
                return _failed_certificate(observed_at, "invalid_payload")
            not_before_raw = decoded.get("notBefore")
            not_after_raw = decoded.get("notAfter")
            if not isinstance(not_before_raw, str) or not isinstance(not_after_raw, str):
                return _failed_certificate(observed_at, "invalid_payload")
            not_before = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_before_raw), tz=UTC)
            not_after = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after_raw), tz=UTC)
            return CertificateObservation(
                checked_at=observed_at,
                leaf_sha256=hashlib.sha256(der).hexdigest(),
                not_before=not_before,
                not_after=not_after,
                remaining_seconds=(not_after - observed_at).total_seconds(),
                hostname_verified=True,
                tls_version=tls_version,
                passed=not_before <= observed_at < not_after,
            )
        except TimeoutError:
            failures.add("timeout")
        except ssl.SSLError:
            failures.add("tls_error")
        except OSError:
            failures.add("dns_or_connect_error")
    if "tls_error" in failures:
        return _failed_certificate(observed_at, "tls_error")
    if "timeout" in failures:
        return _failed_certificate(observed_at, "timeout")
    return _failed_certificate(observed_at, "dns_or_connect_error")


def _unreachable_probe(
    target: DeploymentTarget,
    observed_at: datetime,
) -> DeploymentProbeEvidence:
    endpoints = tuple(
        _Exchange(None, 0, b"", False, "dns_or_connect_error").observation(
            name, semantic_passed=False
        )
        for name in PROBE_PATHS
    )
    return _build_probe(
        target,
        observed_at=observed_at,
        endpoints=endpoints,
        certificate=_failed_certificate(observed_at, "dns_or_connect_error"),
        observed_version=None,
        observed_commit_sha=None,
        events_returned=0,
        events_truncated=False,
        source_visibility=(),
        source_freshness_generated_at=None,
        source_freshness=(),
        execution_boundary=ExecutionBoundaryObservation(passed=False),
    )


def _build_probe(
    target: DeploymentTarget,
    *,
    observed_at: datetime,
    endpoints: tuple[EndpointObservation, ...],
    certificate: CertificateObservation,
    observed_version: str | None,
    observed_commit_sha: str | None,
    events_returned: int,
    events_truncated: bool,
    source_visibility: tuple[SourceVisibilityObservation, ...],
    source_freshness_generated_at: datetime | None,
    source_freshness: tuple[SourceFreshnessItem, ...],
    execution_boundary: ExecutionBoundaryObservation,
) -> DeploymentProbeEvidence:
    passed = (
        all(endpoint.passed for endpoint in endpoints)
        and certificate.passed
        and observed_version == target.application_version
        and observed_commit_sha == target.commit_sha
        and execution_boundary.passed
    )
    draft = DeploymentProbeEvidence.model_construct(
        evidence_id="deployment-probe-" + "0" * 20,
        evidence_sha256="0" * 64,
        target_id=target.target_id,
        target_sha256=target.target_sha256,
        observed_at=observed_at,
        expected_version=target.application_version,
        observed_version=observed_version,
        expected_commit_sha=target.commit_sha,
        observed_commit_sha=observed_commit_sha,
        endpoints=endpoints,
        certificate=certificate,
        events_returned=events_returned,
        events_truncated=events_truncated,
        source_visibility=source_visibility,
        source_freshness_generated_at=source_freshness_generated_at,
        source_freshness=source_freshness,
        execution_boundary=execution_boundary,
        passed=passed,
    )
    digest = _model_sha256(draft, identity_fields={"evidence_id", "evidence_sha256"})
    return DeploymentProbeEvidence(
        **draft.model_dump(mode="python", exclude={"evidence_id", "evidence_sha256"}),
        evidence_id=f"deployment-probe-{digest[:20]}",
        evidence_sha256=digest,
    )


def capture_deployment_probe(
    target: DeploymentTarget,
    *,
    observed_at: datetime | None = None,
    timeout_seconds: float = 10.0,
    transport: httpx.BaseTransport | None = None,
    certificate_loader: CertificateLoader = observe_certificate,
) -> DeploymentProbeEvidence:
    """Capture five fixed public endpoints and TLS without following redirects."""
    if not 0 < timeout_seconds <= 60:
        raise ValueError("probe timeout must be greater than zero and at most 60 seconds")
    timestamp = _require_utc(observed_at or datetime.now(UTC), "observed_at")
    addresses: tuple[str, ...] = ()
    if transport is None:
        addresses = _public_addresses(target, timeout_seconds)
        if not addresses:
            return _unreachable_probe(target, timestamp)
        hostname = urlsplit(target.origin).hostname
        assert hostname is not None
        exchanges = tuple(
            _pinned_exchange(hostname, addresses, name, timeout_seconds) for name in PROBE_PATHS
        )
        (
            health_exchange,
            readiness_exchange,
            freshness_exchange,
            events_exchange,
            preflight_exchange,
        ) = exchanges
    else:
        with httpx.Client(
            base_url=target.origin,
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
            trust_env=False,
            headers={"User-Agent": "AtlasPulse-Operational-Evidence/1.1"},
        ) as client:
            health_exchange = _exchange(client, "health")
            readiness_exchange = _exchange(client, "readiness")
            freshness_exchange = _exchange(client, "source_freshness")
            events_exchange = _exchange(client, "events")
            preflight_exchange = _exchange(client, "agent_preflight")
    health, observed_version, observed_commit_sha = _parse_health(
        health_exchange,
        expected_status="ok",
        expected_version=target.application_version,
        expected_commit_sha=target.commit_sha,
    )
    readiness = _parse_readiness(
        readiness_exchange,
        expected_version=target.application_version,
        expected_commit_sha=target.commit_sha,
    )
    freshness, freshness_generated_at, source_freshness = _parse_source_freshness(
        freshness_exchange,
        target=target,
        observed_at=timestamp,
    )
    events, event_count, truncated, visibility = _parse_events(
        events_exchange,
        observed_at=timestamp,
    )
    preflight, boundary = _parse_preflight(preflight_exchange)
    if transport is None and certificate_loader is observe_certificate:
        certificate = _observe_certificate_at_addresses(
            target,
            timestamp,
            timeout_seconds,
            addresses,
        )
    else:
        certificate = certificate_loader(target, timestamp, timeout_seconds)
    return _build_probe(
        target,
        observed_at=timestamp,
        endpoints=(health, readiness, freshness, events, preflight),
        certificate=certificate,
        observed_version=observed_version,
        observed_commit_sha=observed_commit_sha,
        events_returned=event_count,
        events_truncated=truncated,
        source_visibility=visibility,
        source_freshness_generated_at=freshness_generated_at,
        source_freshness=source_freshness,
        execution_boundary=boundary,
    )


def _run_command(arguments: Sequence[str], cwd: Path) -> str:
    try:
        completed = subprocess.run(
            arguments,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"resource collector could not run {arguments[0]}") from error
    if completed.returncode != 0:
        raise ValueError(
            f"resource collector command {arguments[0]} {arguments[1]} failed with "
            f"status {completed.returncode}"
        )
    return completed.stdout


def _json_records(value: str, field: str) -> tuple[Mapping[str, object], ...]:
    stripped = value.strip()
    if not stripped:
        return ()
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        try:
            decoded = [json.loads(line) for line in stripped.splitlines() if line.strip()]
        except json.JSONDecodeError as error:
            raise ValueError(f"{field} output is not JSON") from error
    if isinstance(decoded, Mapping):
        decoded = [decoded]
    if not isinstance(decoded, list) or any(not isinstance(item, Mapping) for item in decoded):
        raise ValueError(f"{field} output must contain JSON objects")
    return tuple(cast(Mapping[str, object], item) for item in decoded)


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"resource collector field {field} must be a non-empty string")
    return value


def _bytes(value: str) -> int:
    normalized = value.strip().replace(" ", "")
    units = {
        "B": 1,
        "kB": 1_000,
        "KB": 1_000,
        "KiB": 1_024,
        "MB": 1_000**2,
        "MiB": 1_024**2,
        "GB": 1_000**3,
        "GiB": 1_024**3,
        "TB": 1_000**4,
        "TiB": 1_024**4,
    }
    for unit in sorted(units, key=len, reverse=True):
        if normalized.endswith(unit):
            try:
                number = float(normalized[: -len(unit)])
            except ValueError as error:
                raise ValueError("Docker memory value is not numeric") from error
            if number < 0:
                raise ValueError("Docker memory value cannot be negative")
            return round(number * units[unit])
    raise ValueError("Docker memory value uses an unsupported unit")


def observe_host_resources(project_dir: Path) -> HostResourceObservation:
    """Read bounded host load, memory, and filesystem capacity without a new dependency."""
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        physical_pages = os.sysconf("SC_PHYS_PAGES")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
        load_1m = os.getloadavg()[0]
        disk = shutil.disk_usage(project_dir)
    except (OSError, ValueError) as error:
        raise ValueError("host resource values are unavailable") from error
    return HostResourceObservation(
        load_1m=load_1m,
        memory_total_bytes=page_size * physical_pages,
        memory_available_bytes=page_size * available_pages,
        disk_total_bytes=disk.total,
        disk_available_bytes=disk.free,
    )


def _compose_arguments(target: DeploymentTarget) -> list[str]:
    arguments = ["docker", "compose"]
    for compose_file in target.compose_files:
        arguments.extend(("-f", compose_file))
    return arguments


def capture_resource_snapshot(
    target: DeploymentTarget,
    project_dir: Path,
    *,
    observed_at: datetime | None = None,
    command_runner: CommandRunner = _run_command,
    host_loader: HostResourceLoader = observe_host_resources,
) -> ResourceSnapshotEvidence:
    """Capture Docker Compose status/stats and host resources without reading logs or environment."""
    timestamp = _require_utc(observed_at or datetime.now(UTC), "observed_at")
    root = project_dir.resolve()
    if not root.is_dir():
        raise ValueError("resource collector project directory does not exist")
    for compose_file in target.compose_files:
        candidate = (root / compose_file).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise ValueError("resource collector Compose files must exist beneath the project")

    compose = _compose_arguments(target)
    ps_records = _json_records(
        command_runner((*compose, "ps", "--all", "--format", "json"), root),
        "docker compose ps",
    )
    container_ids = tuple(
        _required_string(record.get("ID"), "ID").casefold() for record in ps_records
    )
    state_values = tuple(
        _required_string(record.get("State"), "State").casefold() for record in ps_records
    )
    running_container_ids = tuple(
        container_id
        for container_id, state in zip(container_ids, state_values, strict=True)
        if state == "running"
    )
    stats_records = (
        _json_records(
            command_runner(
                (
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{json .}}",
                    *running_container_ids,
                ),
                root,
            ),
            "docker stats",
        )
        if running_container_ids
        else ()
    )
    stats_by_id: dict[str, Mapping[str, object]] = {}
    for record in stats_records:
        identifier = _required_string(record.get("ID"), "stats.ID").casefold()
        if identifier in stats_by_id:
            raise ValueError("Docker stats contains duplicate container IDs")
        stats_by_id[identifier] = record

    services: list[ServiceResourceObservation] = []
    seen_services: set[str] = set()
    valid_states = {"created", "running", "restarting", "exited", "paused", "dead"}
    valid_health = {"healthy", "unhealthy", "starting"}
    for record, container_id, state_value in zip(
        ps_records, container_ids, state_values, strict=True
    ):
        service = _required_string(record.get("Service"), "Service")
        if service in seen_services:
            raise ValueError("Docker Compose contains duplicate service observations")
        seen_services.add(service)
        state = cast(ServiceState, state_value if state_value in valid_states else "unknown")
        health_value = record.get("Health")
        health_text = health_value.casefold() if isinstance(health_value, str) else ""
        health = cast(
            ServiceHealth,
            health_text
            if health_text in valid_health
            else "not_configured"
            if not health_text
            else "unknown",
        )
        stats = (
            next(
                (
                    value
                    for identifier, value in stats_by_id.items()
                    if identifier.startswith(container_id) or container_id.startswith(identifier)
                ),
                None,
            )
            if state == "running"
            else None
        )
        if stats is None:
            cpu_percent = None
            memory_used = None
            memory_limit = None
            pids = None
        else:
            cpu_raw = _required_string(stats.get("CPUPerc"), "CPUPerc").removesuffix("%")
            memory_raw = _required_string(stats.get("MemUsage"), "MemUsage")
            memory_parts = memory_raw.split("/")
            if len(memory_parts) != 2:
                raise ValueError("Docker memory usage must contain used and limit values")
            try:
                cpu_percent = float(cpu_raw)
                pids = int(_required_string(stats.get("PIDs"), "PIDs"))
            except ValueError as error:
                raise ValueError("Docker CPU or PID value is not numeric") from error
            memory_used = _bytes(memory_parts[0])
            memory_limit = _bytes(memory_parts[1])
        services.append(
            ServiceResourceObservation(
                service=service,
                container_id=container_id,
                state=state,
                health=health,
                cpu_percent=cpu_percent,
                memory_used_bytes=memory_used,
                memory_limit_bytes=memory_limit,
                pids=pids,
                restart_count=None,
            )
        )
    submission = ResourceSnapshotSubmission(
        observed_at=timestamp,
        host=host_loader(root),
        services=tuple(sorted(services, key=lambda item: item.service)),
    )
    return build_resource_snapshot(target, submission)


def build_backup_evidence(
    target: DeploymentTarget,
    path: Path,
    *,
    storage_scope: BackupStorageScope,
    encrypted_at_rest: bool,
    observed_at: datetime | None = None,
) -> BackupEvidence:
    """Hash one stable, regular PostgreSQL custom-format dump without modifying it."""
    timestamp = _require_utc(observed_at or datetime.now(UTC), "observed_at")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError(f"cannot open backup file: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
            raise ValueError("backup must be a non-empty regular file")
        digest = hashlib.sha256()
        prefix = b""
        while chunk := os.read(descriptor, _READ_CHUNK_BYTES):
            if len(prefix) < 5:
                prefix += chunk[: 5 - len(prefix)]
            digest.update(chunk)
        after = os.fstat(descriptor)
        stable = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if not stable:
            raise ValueError("backup file changed while it was being hashed")
        if prefix != b"PGDMP":
            raise ValueError("backup file is not a PostgreSQL custom-format dump")
    finally:
        os.close(descriptor)
    draft = BackupEvidence.model_construct(
        evidence_id="backup-evidence-" + "0" * 20,
        evidence_sha256="0" * 64,
        target_id=target.target_id,
        target_sha256=target.target_sha256,
        observed_at=timestamp,
        file_name=path.name,
        file_size_bytes=before.st_size,
        file_sha256=digest.hexdigest(),
        storage_scope=storage_scope,
        encrypted_at_rest=encrypted_at_rest,
    )
    evidence_digest = _model_sha256(draft, identity_fields={"evidence_id", "evidence_sha256"})
    return BackupEvidence(
        **draft.model_dump(mode="python", exclude={"evidence_id", "evidence_sha256"}),
        evidence_id=f"backup-evidence-{evidence_digest[:20]}",
        evidence_sha256=evidence_digest,
    )

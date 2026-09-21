"""Strict, content-addressed evidence for AtlasPulse public operations."""

from __future__ import annotations

import ipaddress
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from atlas_pulse.evaluation.base import StrictModel
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.projections.base import SourceName
from atlas_pulse.source_polling import (
    SourceFreshnessItem,
    SourcePollFailureCode,
    SourcePollHistoryResponse,
    SourcePollStage,
    SourcePollTransition,
)

OPERATIONAL_EVIDENCE_SCHEMA_VERSION = "1.1.0"
OPERATIONAL_EVIDENCE_IDENTITY_ALGORITHM = "sha256-canonical-json-v1"
DEPLOYMENT_TARGET_RULE_VERSION = "public-deployment-target-v1"
OPERATIONAL_OBSERVATION_RULE_VERSION = "operational-observation-v2"

DEPLOYMENT_TARGET_CAVEATS = (
    "The target identifies one exact public origin, reviewed commit, and deployment profile; it "
    "does not prove that the commit is currently serving at that origin.",
    "The revision is operator-supplied image build metadata, not a reproducible-build or hardware "
    "attestation.",
    "The public evidence target keeps agent execution disabled. Deployment monitoring cannot "
    "grant human approval or satisfy the independent execution-release gate.",
)
PROBE_CAVEATS = (
    "A probe is a point-in-time observation from the machine that ran the collector, not an SLA "
    "or an independent availability monitor.",
    "The exact revision check matches operator-supplied image metadata reported by the API; it is "
    "not an independent software-supply-chain attestation.",
    "Source-poll freshness is worker-written state evaluated with the API host clock. It is not "
    "an independent monitor, upstream completeness proof, or availability SLA.",
    "Event visibility remains a separate bounded publication observation; unchanged source "
    "responses may be deduplicated without making the poll heartbeat stale.",
    "Response bodies are represented by SHA-256 plus minimal typed observations; source text and "
    "agent evidence are not copied into the operational artifact.",
    "The probe verifies the public default-deny boundary and never invokes a model, agent, tool, "
    "approval action, or side effect.",
)
RESOURCE_CAVEATS = (
    "Resource values are point-in-time collector observations, not capacity or load-test claims.",
    "A healthy container snapshot does not prove end-to-end service correctness or availability.",
)
DRILL_CAVEATS = (
    "Restart and restore records bind operator-observed checks; they are not a substitute for an "
    "independent disaster-recovery audit.",
    "No drill record authorizes agent execution or a production data deletion.",
)
SOURCE_POLL_RECOVERY_DRILL_CAVEATS = (
    "Fault timing, the expected bounded failure code, and the no-deletion statement are "
    "operator assertions; the artifact validates observations but does not independently attest "
    "the cause of the failure.",
    "The bound history is worker-written, retention-bounded state from the deployment under test. "
    "It is not an independent monitor, durable audit log, availability SLA, or proof of upstream "
    "completeness.",
    "A passing drill shows one observed degraded state followed by one passing state. It does not "
    "prove uninterrupted recovery after the final probe.",
    "The recorder only reads supplied artifacts. It does not inject a fault, restart a service, "
    "delete production data, authorize an agent, or enable execution.",
)
BACKUP_CAVEATS = (
    "A file digest proves the bytes observed by the collector, not durability at a remote storage "
    "provider.",
    "An off-host or encryption label is an operator assertion until a separate restore drill "
    "successfully binds the same backup digest.",
)

ProbeName = Literal["health", "readiness", "source_freshness", "events", "agent_preflight"]
ProbeFailureCode = Literal[
    "dns_or_connect_error",
    "tls_error",
    "timeout",
    "http_status",
    "response_too_large",
    "invalid_json",
    "invalid_payload",
    "unexpected_value",
]
ServiceState = Literal["created", "running", "restarting", "exited", "paused", "dead", "unknown"]
ServiceHealth = Literal["healthy", "unhealthy", "starting", "not_configured", "unknown"]
RestartTrigger = Literal["planned_host_reboot", "planned_stack_restart", "failure_recovery"]
BackupStorageScope = Literal["local_only", "off_host_verified"]
RestoreCheckName = Literal[
    "backup_digest_verified",
    "database_readable",
    "migration_head_matches",
    "readiness_passed",
    "agent_default_deny",
]

PROBE_PATHS: dict[ProbeName, str] = {
    "health": "/api/healthz",
    "readiness": "/api/readyz",
    "source_freshness": "/api/v1/source-freshness",
    "events": "/api/v1/events?limit=500",
    "agent_preflight": "/api/v1/agent-runs/preflight?q=operational%20boundary",
}
RESTORE_CHECKS: tuple[RestoreCheckName, ...] = (
    "agent_default_deny",
    "backup_digest_verified",
    "database_readable",
    "migration_head_matches",
    "readiness_passed",
)


def _require_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    normalized = value.astimezone(UTC)
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must use UTC")
    return normalized


def _normalize_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("deployment origin must be an absolute HTTPS origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("deployment origin cannot contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("deployment origin cannot contain a path")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("deployment origin contains an invalid port") from error
    if port not in {None, 443}:
        raise ValueError("deployment origin must use the standard HTTPS port")
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("deployment origin must not identify a local hostname")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            canonical_host = hostname.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise ValueError("deployment origin hostname is invalid") from error
    else:
        if not address.is_global:
            raise ValueError("deployment origin IP must be globally routable")
        canonical_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"https://{canonical_host}"


def _model_sha256(value: StrictModel, *, identity_fields: set[str]) -> str:
    return canonical_sha256(
        value.model_dump(mode="json", exclude=identity_fields, exclude_none=False)
    )


def _validate_identity(
    value: StrictModel,
    *,
    prefix: str,
    identifier: str,
    digest: str,
    identity_fields: set[str],
) -> None:
    expected = _model_sha256(value, identity_fields=identity_fields)
    if digest != expected:
        raise ValueError(f"{prefix} SHA-256 must match its exact content")
    if identifier != f"{prefix}-{expected[:20]}":
        raise ValueError(f"{prefix} ID must match its SHA-256")


class DeploymentTarget(StrictModel):
    """One immutable public deployment scope monitored by an evidence campaign."""

    schema_version: Literal["1.1.0"] = "1.1.0"
    rule_version: Literal["public-deployment-target-v1"] = "public-deployment-target-v1"
    identity_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    target_id: str = Field(pattern=r"^deployment-target-[0-9a-f]{20}$")
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin: str = Field(min_length=12, max_length=253)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    application_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    environment: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    deployed_at: datetime
    required_sources: tuple[SourceName, ...] = ("gdelt", "usgs")
    required_services: tuple[str, ...] = (
        "api",
        "edge",
        "ingestor",
        "postgres",
        "projector",
        "retrieval-indexer",
        "valkey",
        "web",
    )
    compose_files: tuple[str, ...] = ("compose.yaml", "deploy/free-tier/compose.yaml")
    execution_enabled: Literal[False] = False
    caveats: tuple[str, ...] = DEPLOYMENT_TARGET_CAVEATS

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return _normalize_origin(value)

    @field_validator("required_sources")
    @classmethod
    def validate_required_sources(cls, value: tuple[SourceName, ...]) -> tuple[SourceName, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("required sources must be non-empty, unique, and ordered")
        return value

    @field_validator("required_services")
    @classmethod
    def validate_required_services(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("required services must be non-empty, unique, and ordered")
        if any(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", item) is None for item in value):
            raise ValueError("required service names must be normalized Compose service names")
        return value

    @field_validator("compose_files")
    @classmethod
    def validate_compose_files(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(sorted(set(value))):
            raise ValueError("Compose files must be non-empty, unique, and ordered")
        for item in value:
            path = PurePosixPath(item)
            if (
                path.is_absolute()
                or ".." in path.parts
                or path.name in {"", "."}
                or path.as_posix() != item
            ):
                raise ValueError("Compose files must be normalized repository-relative paths")
        return value

    @model_validator(mode="after")
    def validate_target(self) -> DeploymentTarget:
        _require_utc(self.deployed_at, "deployed_at")
        if self.execution_enabled is not False or self.caveats != DEPLOYMENT_TARGET_CAVEATS:
            raise ValueError("deployment targets must retain the closed execution boundary")
        if self.environment == "workstation-funnel-public":
            hostname = urlsplit(self.origin).hostname
            assert hostname is not None
            labels = hostname.split(".")
            valid_label = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
            if (
                len(labels) < 4
                or labels[-2:] != ["ts", "net"]
                or any(valid_label.fullmatch(label) is None for label in labels)
            ):
                raise ValueError("workstation Funnel targets require a full Tailscale DNS origin")
            if "deploy/workstation-funnel/compose.yaml" not in self.compose_files:
                raise ValueError(
                    "workstation Funnel targets require the workstation Funnel Compose overlay"
                )
        _validate_identity(
            self,
            prefix="deployment-target",
            identifier=self.target_id,
            digest=self.target_sha256,
            identity_fields={"target_id", "target_sha256"},
        )
        return self


def deployment_target_sha256(target: DeploymentTarget) -> str:
    return _model_sha256(target, identity_fields={"target_id", "target_sha256"})


def build_deployment_target(
    *,
    origin: str,
    commit_sha: str,
    application_version: str,
    environment: str,
    deployed_at: datetime,
    required_sources: tuple[SourceName, ...] = ("gdelt", "usgs"),
    required_services: tuple[str, ...] = DeploymentTarget.model_fields["required_services"].default,
    compose_files: tuple[str, ...] = DeploymentTarget.model_fields["compose_files"].default,
) -> DeploymentTarget:
    """Create a content-addressed public deployment target with execution disabled."""
    timestamp = _require_utc(deployed_at, "deployed_at")
    draft = DeploymentTarget.model_construct(
        target_id="deployment-target-" + "0" * 20,
        target_sha256="0" * 64,
        origin=_normalize_origin(origin),
        commit_sha=commit_sha,
        application_version=application_version,
        environment=environment,
        deployed_at=timestamp,
        required_sources=tuple(sorted(required_sources)),
        required_services=tuple(sorted(required_services)),
        compose_files=tuple(sorted(compose_files)),
    )
    digest = deployment_target_sha256(draft)
    return DeploymentTarget(
        **draft.model_dump(mode="python", exclude={"target_id", "target_sha256"}),
        target_id=f"deployment-target-{digest[:20]}",
        target_sha256=digest,
    )


class EndpointObservation(StrictModel):
    """Bounded HTTP exchange metadata for one fixed public endpoint."""

    name: ProbeName
    path: str = Field(min_length=1, max_length=160)
    status_code: int | None = Field(default=None, ge=100, le=599)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    body_bytes: int = Field(ge=0, le=1_048_576)
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    body_truncated: bool = False
    passed: bool
    failure_code: ProbeFailureCode | None = None

    @model_validator(mode="after")
    def validate_endpoint(self) -> EndpointObservation:
        if self.path != PROBE_PATHS[self.name]:
            raise ValueError("probe path does not match its fixed endpoint name")
        received = self.status_code is not None
        if received != (self.response_sha256 is not None):
            raise ValueError("received HTTP responses require a response digest")
        if not received and self.body_bytes != 0:
            raise ValueError("failed connections cannot report response bytes")
        if self.body_truncated != (self.failure_code == "response_too_large"):
            raise ValueError("response truncation must use the response_too_large failure")
        if self.passed:
            if self.status_code != 200 or self.failure_code is not None or self.body_truncated:
                raise ValueError("passed endpoints require HTTP 200 and no failure")
        elif self.failure_code is None:
            raise ValueError("failed endpoints require a bounded failure code")
        return self


class CertificateObservation(StrictModel):
    """Verified leaf-certificate metadata observed at one UTC instant."""

    checked_at: datetime
    leaf_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    not_before: datetime | None = None
    not_after: datetime | None = None
    remaining_seconds: float | None = Field(default=None, allow_inf_nan=False)
    hostname_verified: bool
    tls_version: str | None = Field(default=None, min_length=1, max_length=32)
    passed: bool
    failure_code: ProbeFailureCode | None = None

    @model_validator(mode="after")
    def validate_certificate(self) -> CertificateObservation:
        checked_at = _require_utc(self.checked_at, "certificate.checked_at")
        values = (self.leaf_sha256, self.not_before, self.not_after, self.remaining_seconds)
        if self.failure_code is None:
            if any(value is None for value in values) or self.tls_version is None:
                raise ValueError("successful TLS observations require complete certificate data")
            assert self.not_before is not None
            assert self.not_after is not None
            assert self.remaining_seconds is not None
            _require_utc(self.not_before, "certificate.not_before")
            _require_utc(self.not_after, "certificate.not_after")
            expected = (self.not_after - checked_at).total_seconds()
            if abs(self.remaining_seconds - expected) > 1e-6:
                raise ValueError("certificate remaining seconds must match its expiry")
            should_pass = (
                self.hostname_verified
                and self.not_before <= checked_at < self.not_after
                and self.remaining_seconds > 0
            )
            if self.passed != should_pass:
                raise ValueError("certificate pass status does not match the observation")
        else:
            if any(value is not None for value in values) or self.tls_version is not None:
                raise ValueError("failed TLS observations cannot contain certificate metadata")
            if self.hostname_verified or self.passed:
                raise ValueError("failed TLS observations cannot pass hostname verification")
        return self


class SourceVisibilityObservation(StrictModel):
    """Latest bounded event visibility for one source at probe time."""

    source: SourceName
    event_count: int = Field(ge=1, le=500)
    latest_ingested_at: datetime
    age_seconds: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_visibility(self) -> SourceVisibilityObservation:
        _require_utc(self.latest_ingested_at, "latest_ingested_at")
        return self


class ExecutionBoundaryObservation(StrictModel):
    """Minimal public preflight fields proving the expected default-deny response."""

    manifest_status: str | None = Field(default=None, max_length=64)
    release_status: str | None = Field(default=None, max_length=64)
    authorization_check_count: int | None = Field(default=None, ge=0, le=64)
    execution_status: str | None = Field(default=None, max_length=64)
    agent_model_invoked: bool | None = None
    agent_network_accessed: bool | None = None
    agent_tools_invoked: bool | None = None
    answer_generated: bool | None = None
    agent_side_effects_performed: bool | None = None
    passed: bool

    @model_validator(mode="after")
    def validate_boundary(self) -> ExecutionBoundaryObservation:
        expected = (
            self.manifest_status == "blocked"
            and self.release_status == "not_supplied"
            and self.authorization_check_count == 11
            and self.execution_status == "not_started"
            and self.agent_model_invoked is False
            and self.agent_network_accessed is False
            and self.agent_tools_invoked is False
            and self.answer_generated is False
            and self.agent_side_effects_performed is False
        )
        if self.passed != expected:
            raise ValueError("execution-boundary pass status does not match the observed fields")
        return self


class DeploymentProbeEvidence(StrictModel):
    """One content-addressed public HTTPS and closed-boundary observation."""

    kind: Literal["deployment_probe"] = "deployment_probe"
    schema_version: Literal["1.1.0"] = "1.1.0"
    rule_version: Literal["operational-observation-v2"] = "operational-observation-v2"
    identity_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    evidence_id: str = Field(pattern=r"^deployment-probe-[0-9a-f]{20}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_id: str = Field(pattern=r"^deployment-target-[0-9a-f]{20}$")
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    expected_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    observed_version: str | None = Field(default=None, max_length=32)
    expected_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    observed_commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    endpoints: tuple[EndpointObservation, ...] = Field(min_length=5, max_length=5)
    certificate: CertificateObservation
    events_returned: int = Field(ge=0, le=500)
    events_truncated: bool
    source_visibility: tuple[SourceVisibilityObservation, ...]
    source_freshness_generated_at: datetime | None = None
    source_freshness: tuple[SourceFreshnessItem, ...]
    execution_boundary: ExecutionBoundaryObservation
    passed: bool
    execution_enabled: Literal[False] = False
    caveats: tuple[str, ...] = PROBE_CAVEATS

    @model_validator(mode="after")
    def validate_probe(self) -> DeploymentProbeEvidence:
        observed_at = _require_utc(self.observed_at, "observed_at")
        if self.certificate.checked_at != observed_at:
            raise ValueError("certificate time must equal probe observation time")
        names = tuple(endpoint.name for endpoint in self.endpoints)
        if names != tuple(PROBE_PATHS):
            raise ValueError("probe endpoints must use the complete canonical order")
        sources = tuple(item.source for item in self.source_visibility)
        if sources != tuple(sorted(set(sources))):
            raise ValueError("source visibility must be unique and canonically ordered")
        if sum(item.event_count for item in self.source_visibility) > self.events_returned:
            raise ValueError("source visibility counts cannot exceed returned events")
        for item in self.source_visibility:
            expected_age = (observed_at - item.latest_ingested_at).total_seconds()
            if abs(item.age_seconds - expected_age) > 1e-6:
                raise ValueError("source visibility age must match the observation time")
        freshness_sources = tuple(item.source for item in self.source_freshness)
        if freshness_sources != tuple(sorted(set(freshness_sources))):
            raise ValueError("source freshness must be unique and canonically ordered")
        if (self.source_freshness_generated_at is None) != (not self.source_freshness):
            raise ValueError("source freshness time and observations must be present together")
        if self.source_freshness_generated_at is not None:
            generated_at = _require_utc(
                self.source_freshness_generated_at,
                "source_freshness_generated_at",
            )
            if abs((observed_at - generated_at).total_seconds()) > 60:
                raise ValueError("source freshness and probe clocks must be within 60 seconds")
            for freshness_item in self.source_freshness:
                if freshness_item.last_success_at is not None:
                    assert freshness_item.last_success_age_seconds is not None
                    assert freshness_item.last_source_generated_at is not None
                    assert freshness_item.source_age_seconds is not None
                    if (
                        abs(
                            freshness_item.last_success_age_seconds
                            - (generated_at - freshness_item.last_success_at).total_seconds()
                        )
                        > 1e-6
                    ):
                        raise ValueError("source last-success age must match freshness time")
                    if (
                        abs(
                            freshness_item.source_age_seconds
                            - (
                                generated_at - freshness_item.last_source_generated_at
                            ).total_seconds()
                        )
                        > 1e-6
                    ):
                        raise ValueError("source data age must match freshness time")
        expected_pass = (
            all(endpoint.passed for endpoint in self.endpoints)
            and self.certificate.passed
            and self.observed_version == self.expected_version
            and self.observed_commit_sha == self.expected_commit_sha
            and self.execution_boundary.passed
        )
        if self.passed != expected_pass:
            raise ValueError("deployment probe pass status does not match its checks")
        if self.execution_enabled is not False or self.caveats != PROBE_CAVEATS:
            raise ValueError("deployment probes must retain the closed execution boundary")
        _validate_identity(
            self,
            prefix="deployment-probe",
            identifier=self.evidence_id,
            digest=self.evidence_sha256,
            identity_fields={"evidence_id", "evidence_sha256"},
        )
        return self


class HostResourceObservation(StrictModel):
    """Host-level values supplied by the deployment collector."""

    load_1m: float = Field(ge=0, allow_inf_nan=False)
    memory_total_bytes: int = Field(gt=0)
    memory_available_bytes: int = Field(ge=0)
    disk_total_bytes: int = Field(gt=0)
    disk_available_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_capacity(self) -> HostResourceObservation:
        if self.memory_available_bytes > self.memory_total_bytes:
            raise ValueError("available memory cannot exceed total memory")
        if self.disk_available_bytes > self.disk_total_bytes:
            raise ValueError("available disk cannot exceed total disk")
        return self


class ServiceResourceObservation(StrictModel):
    """One Docker service snapshot without logs, environment, or secret-bearing fields."""

    service: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    container_id: str = Field(pattern=r"^[0-9a-f]{12,64}$")
    state: ServiceState
    health: ServiceHealth
    cpu_percent: float | None = Field(default=None, ge=0, le=10_000, allow_inf_nan=False)
    memory_used_bytes: int | None = Field(default=None, ge=0)
    memory_limit_bytes: int | None = Field(default=None, gt=0)
    pids: int | None = Field(default=None, ge=0, le=1_000_000)
    restart_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_memory(self) -> ServiceResourceObservation:
        metrics = (self.cpu_percent, self.memory_used_bytes, self.memory_limit_bytes, self.pids)
        if any(value is None for value in metrics) != all(value is None for value in metrics):
            raise ValueError("container runtime metrics must be either complete or absent")
        if (
            self.memory_used_bytes is not None
            and self.memory_limit_bytes is not None
            and self.memory_used_bytes > self.memory_limit_bytes
        ):
            raise ValueError("container memory use cannot exceed its observed limit")
        return self


class ResourceSnapshotSubmission(StrictModel):
    """Protected operator/collector input before deployment identity is attached."""

    schema_version: Literal["1.1.0"] = "1.1.0"
    observed_at: datetime
    host: HostResourceObservation
    services: tuple[ServiceResourceObservation, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_submission(self) -> ResourceSnapshotSubmission:
        _require_utc(self.observed_at, "observed_at")
        names = tuple(item.service for item in self.services)
        if names != tuple(sorted(set(names))):
            raise ValueError("resource services must be unique and canonically ordered")
        return self


class ResourceSnapshotEvidence(StrictModel):
    """Content-addressed host/container resource observation for one deployment target."""

    kind: Literal["resource_snapshot"] = "resource_snapshot"
    schema_version: Literal["1.1.0"] = "1.1.0"
    rule_version: Literal["operational-observation-v2"] = "operational-observation-v2"
    identity_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    evidence_id: str = Field(pattern=r"^resource-snapshot-[0-9a-f]{20}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_id: str = Field(pattern=r"^deployment-target-[0-9a-f]{20}$")
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    required_services: tuple[str, ...]
    host: HostResourceObservation
    services: tuple[ServiceResourceObservation, ...] = Field(min_length=1, max_length=64)
    passed: bool
    execution_enabled: Literal[False] = False
    caveats: tuple[str, ...] = RESOURCE_CAVEATS

    @model_validator(mode="after")
    def validate_snapshot(self) -> ResourceSnapshotEvidence:
        _require_utc(self.observed_at, "observed_at")
        names = tuple(item.service for item in self.services)
        if names != tuple(sorted(set(names))):
            raise ValueError("resource services must be unique and canonically ordered")
        observed = {item.service: item for item in self.services}
        expected_pass = all(
            name in observed
            and observed[name].state == "running"
            and observed[name].health in {"healthy", "not_configured"}
            and observed[name].cpu_percent is not None
            for name in self.required_services
        )
        if self.passed != expected_pass:
            raise ValueError("resource snapshot pass status does not match required services")
        if self.execution_enabled is not False or self.caveats != RESOURCE_CAVEATS:
            raise ValueError("resource snapshots must retain the closed execution boundary")
        _validate_identity(
            self,
            prefix="resource-snapshot",
            identifier=self.evidence_id,
            digest=self.evidence_sha256,
            identity_fields={"evidence_id", "evidence_sha256"},
        )
        return self


def build_resource_snapshot(
    target: DeploymentTarget,
    submission: ResourceSnapshotSubmission,
) -> ResourceSnapshotEvidence:
    """Bind a strict resource submission to one exact deployment target."""
    observed = {item.service: item for item in submission.services}
    passed = all(
        name in observed
        and observed[name].state == "running"
        and observed[name].health in {"healthy", "not_configured"}
        and observed[name].cpu_percent is not None
        for name in target.required_services
    )
    draft = ResourceSnapshotEvidence.model_construct(
        evidence_id="resource-snapshot-" + "0" * 20,
        evidence_sha256="0" * 64,
        target_id=target.target_id,
        target_sha256=target.target_sha256,
        observed_at=submission.observed_at,
        required_services=target.required_services,
        host=submission.host,
        services=submission.services,
        passed=passed,
    )
    digest = _model_sha256(draft, identity_fields={"evidence_id", "evidence_sha256"})
    return ResourceSnapshotEvidence(
        **draft.model_dump(mode="python", exclude={"evidence_id", "evidence_sha256"}),
        evidence_id=f"resource-snapshot-{digest[:20]}",
        evidence_sha256=digest,
    )


class RestartRecoveryEvidence(StrictModel):
    """Recovery timing bound to exact passing/failing probes before and after a restart."""

    kind: Literal["restart_recovery"] = "restart_recovery"
    schema_version: Literal["1.1.0"] = "1.1.0"
    rule_version: Literal["operational-observation-v2"] = "operational-observation-v2"
    identity_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    evidence_id: str = Field(pattern=r"^restart-recovery-[0-9a-f]{20}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_id: str = Field(pattern=r"^deployment-target-[0-9a-f]{20}$")
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    trigger: RestartTrigger
    services: tuple[str, ...] = Field(min_length=1)
    before_probe_id: str = Field(pattern=r"^deployment-probe-[0-9a-f]{20}$")
    after_probe_id: str = Field(pattern=r"^deployment-probe-[0-9a-f]{20}$")
    restart_started_at: datetime
    ready_observed_at: datetime
    recovery_seconds: float = Field(ge=0, allow_inf_nan=False)
    before_probe_passed: bool
    after_probe_passed: bool
    passed: bool
    execution_enabled: Literal[False] = False
    caveats: tuple[str, ...] = DRILL_CAVEATS

    @model_validator(mode="after")
    def validate_recovery(self) -> RestartRecoveryEvidence:
        observed_at = _require_utc(self.observed_at, "observed_at")
        started = _require_utc(self.restart_started_at, "restart_started_at")
        ready = _require_utc(self.ready_observed_at, "ready_observed_at")
        if ready < started or observed_at != ready:
            raise ValueError("restart recovery times must be chronological and end at observed_at")
        if abs(self.recovery_seconds - (ready - started).total_seconds()) > 1e-6:
            raise ValueError("restart recovery seconds must match the observed times")
        if self.services != tuple(sorted(set(self.services))):
            raise ValueError("restart services must be unique and canonically ordered")
        if self.before_probe_id == self.after_probe_id:
            raise ValueError("restart evidence requires distinct before and after probes")
        if self.passed != (self.before_probe_passed and self.after_probe_passed):
            raise ValueError("restart pass status must match the bound probes")
        if self.execution_enabled is not False or self.caveats != DRILL_CAVEATS:
            raise ValueError("restart evidence must retain the closed execution boundary")
        _validate_identity(
            self,
            prefix="restart-recovery",
            identifier=self.evidence_id,
            digest=self.evidence_sha256,
            identity_fields={"evidence_id", "evidence_sha256"},
        )
        return self


def build_restart_recovery(
    target: DeploymentTarget,
    before: DeploymentProbeEvidence,
    after: DeploymentProbeEvidence,
    *,
    trigger: RestartTrigger,
    services: tuple[str, ...],
    restart_started_at: datetime,
) -> RestartRecoveryEvidence:
    """Bind one restart window to exact public probes without performing the restart."""
    for probe in (before, after):
        if probe.target_id != target.target_id or probe.target_sha256 != target.target_sha256:
            raise ValueError("restart probes must match the exact deployment target")
    started = _require_utc(restart_started_at, "restart_started_at")
    if before.observed_at > started or after.observed_at < started:
        raise ValueError("restart start must fall between the before and after probes")
    if not set(services) <= set(target.required_services):
        raise ValueError("restart services must belong to the deployment target")
    draft = RestartRecoveryEvidence.model_construct(
        evidence_id="restart-recovery-" + "0" * 20,
        evidence_sha256="0" * 64,
        target_id=target.target_id,
        target_sha256=target.target_sha256,
        observed_at=after.observed_at,
        trigger=trigger,
        services=tuple(sorted(services)),
        before_probe_id=before.evidence_id,
        after_probe_id=after.evidence_id,
        restart_started_at=started,
        ready_observed_at=after.observed_at,
        recovery_seconds=(after.observed_at - started).total_seconds(),
        before_probe_passed=before.passed,
        after_probe_passed=after.passed,
        passed=before.passed and after.passed,
    )
    digest = _model_sha256(draft, identity_fields={"evidence_id", "evidence_sha256"})
    return RestartRecoveryEvidence(
        **draft.model_dump(mode="python", exclude={"evidence_id", "evidence_sha256"}),
        evidence_id=f"restart-recovery-{digest[:20]}",
        evidence_sha256=digest,
    )


class SourcePollRecoveryDrillSubmission(StrictModel):
    """Operator-declared fault window and exact attempts for one source-poll drill."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    source: SourceName
    fault_started_at: datetime
    fault_cleared_at: datetime
    failure_attempt_id: str = Field(pattern=r"^source-poll-[0-9a-f]{32}$")
    recovery_attempt_id: str = Field(pattern=r"^source-poll-[0-9a-f]{32}$")
    expected_failure_code: SourcePollFailureCode
    operator_fault_injected: Literal[True] = True
    production_data_deleted: Literal[False] = False
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_submission(self) -> SourcePollRecoveryDrillSubmission:
        started = _require_utc(self.fault_started_at, "fault_started_at")
        cleared = _require_utc(self.fault_cleared_at, "fault_cleared_at")
        if cleared < started:
            raise ValueError("fault clearance cannot precede fault injection")
        if self.failure_attempt_id == self.recovery_attempt_id:
            raise ValueError("failure and recovery attempts must be distinct")
        if (
            self.operator_fault_injected is not True
            or self.production_data_deleted is not False
            or self.execution_enabled is not False
        ):
            raise ValueError("source-poll drill submissions must retain the safe boundary")
        return self


class SourcePollRecoveryDrillEvidence(StrictModel):
    """Content-addressed evidence for one bounded, operator-run source-poll recovery drill."""

    kind: Literal["source_poll_recovery_drill"] = "source_poll_recovery_drill"
    schema_version: Literal["1.0.0"] = "1.0.0"
    rule_version: Literal["source-poll-recovery-drill-v1"] = "source-poll-recovery-drill-v1"
    identity_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    evidence_id: str = Field(pattern=r"^source-poll-recovery-[0-9a-f]{20}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_id: str = Field(pattern=r"^deployment-target-[0-9a-f]{20}$")
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: SourceName
    expected_failure_code: SourcePollFailureCode
    observed_failure_code: SourcePollFailureCode
    observed_failure_stage: SourcePollStage
    fault_started_at: datetime
    fault_cleared_at: datetime
    fault_window_seconds: float = Field(ge=0, allow_inf_nan=False)
    before_observed_at: datetime
    failure_observed_at: datetime
    observed_at: datetime
    recovery_observation_seconds: float = Field(ge=0, allow_inf_nan=False)
    history_generated_at: datetime
    history_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    history_item_count: int = Field(ge=4, le=100)
    before_probe_id: str = Field(pattern=r"^deployment-probe-[0-9a-f]{20}$")
    before_probe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    failure_probe_id: str = Field(pattern=r"^deployment-probe-[0-9a-f]{20}$")
    failure_probe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recovery_probe_id: str = Field(pattern=r"^deployment-probe-[0-9a-f]{20}$")
    recovery_probe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    failure_attempt_id: str = Field(pattern=r"^source-poll-[0-9a-f]{32}$")
    recovery_attempt_id: str = Field(pattern=r"^source-poll-[0-9a-f]{32}$")
    failure_attempt_started_at: datetime
    failure_terminal_at: datetime
    recovery_attempt_started_at: datetime
    recovery_terminal_at: datetime
    failure_started_stream_id: str = Field(pattern=r"^[0-9]+-[0-9]+$")
    failure_terminal_stream_id: str = Field(pattern=r"^[0-9]+-[0-9]+$")
    recovery_started_stream_id: str = Field(pattern=r"^[0-9]+-[0-9]+$")
    recovery_terminal_stream_id: str = Field(pattern=r"^[0-9]+-[0-9]+$")
    before_probe_passed: bool
    failure_probe_passed: bool
    recovery_probe_passed: bool
    before_source_current: bool
    failure_source_degraded: bool
    recovery_source_current: bool
    failure_code_matched: bool
    other_required_sources_maintained: bool
    failure_surface_isolated: bool
    readiness_maintained: bool
    execution_boundary_maintained: bool
    passed: bool
    operator_fault_injected: Literal[True] = True
    production_data_deleted: Literal[False] = False
    recorder_mutated_services: Literal[False] = False
    execution_enabled: Literal[False] = False
    caveats: tuple[str, ...] = SOURCE_POLL_RECOVERY_DRILL_CAVEATS

    @model_validator(mode="after")
    def validate_recovery(self) -> SourcePollRecoveryDrillEvidence:
        fault_started = _require_utc(self.fault_started_at, "fault_started_at")
        fault_cleared = _require_utc(self.fault_cleared_at, "fault_cleared_at")
        before = _require_utc(self.before_observed_at, "before_observed_at")
        failed = _require_utc(self.failure_observed_at, "failure_observed_at")
        recovered = _require_utc(self.observed_at, "observed_at")
        failure_started = _require_utc(
            self.failure_attempt_started_at, "failure_attempt_started_at"
        )
        failure_terminal = _require_utc(self.failure_terminal_at, "failure_terminal_at")
        recovery_started = _require_utc(
            self.recovery_attempt_started_at, "recovery_attempt_started_at"
        )
        recovery_terminal = _require_utc(self.recovery_terminal_at, "recovery_terminal_at")
        history_generated = _require_utc(self.history_generated_at, "history_generated_at")
        if not (
            before
            <= fault_started
            <= failure_started
            <= failure_terminal
            <= failed
            <= fault_cleared
            <= recovery_started
            <= recovery_terminal
            <= recovered
            <= history_generated
        ):
            raise ValueError("source-poll recovery times must follow the declared drill sequence")
        if abs(self.fault_window_seconds - (fault_cleared - fault_started).total_seconds()) > 1e-6:
            raise ValueError("fault-window seconds must match the declared times")
        if (
            abs(self.recovery_observation_seconds - (recovered - fault_cleared).total_seconds())
            > 1e-6
        ):
            raise ValueError("recovery-observation seconds must match the declared times")
        if len({self.before_probe_id, self.failure_probe_id, self.recovery_probe_id}) != 3:
            raise ValueError("source-poll recovery evidence requires three distinct probes")
        for identifier, digest in (
            (self.before_probe_id, self.before_probe_sha256),
            (self.failure_probe_id, self.failure_probe_sha256),
            (self.recovery_probe_id, self.recovery_probe_sha256),
        ):
            if identifier != f"deployment-probe-{digest[:20]}":
                raise ValueError("source-poll recovery probe IDs must match their full SHA-256")
        if self.failure_attempt_id == self.recovery_attempt_id:
            raise ValueError("failure and recovery attempts must be distinct")
        stream_positions = tuple(
            _source_poll_stream_position(value)
            for value in (
                self.failure_started_stream_id,
                self.failure_terminal_stream_id,
                self.recovery_started_stream_id,
                self.recovery_terminal_stream_id,
            )
        )
        if stream_positions != tuple(sorted(set(stream_positions))):
            raise ValueError("bound source-poll transitions must be unique and chronological")
        if self.failure_code_matched != (self.expected_failure_code == self.observed_failure_code):
            raise ValueError("failure-code match must reflect the expected and observed values")
        expected_pass = (
            self.before_probe_passed
            and not self.failure_probe_passed
            and self.recovery_probe_passed
            and self.before_source_current
            and self.failure_source_degraded
            and self.recovery_source_current
            and self.failure_code_matched
            and self.other_required_sources_maintained
            and self.failure_surface_isolated
            and self.readiness_maintained
            and self.execution_boundary_maintained
        )
        if self.passed != expected_pass:
            raise ValueError("source-poll recovery pass status must match every bounded check")
        if (
            self.operator_fault_injected is not True
            or self.production_data_deleted is not False
            or self.recorder_mutated_services is not False
            or self.execution_enabled is not False
            or self.caveats != SOURCE_POLL_RECOVERY_DRILL_CAVEATS
        ):
            raise ValueError("source-poll recovery evidence must retain the safe boundary")
        _validate_identity(
            self,
            prefix="source-poll-recovery",
            identifier=self.evidence_id,
            digest=self.evidence_sha256,
            identity_fields={"evidence_id", "evidence_sha256"},
        )
        return self


def _source_poll_stream_position(value: str) -> tuple[int, int]:
    first, separator, second = value.partition("-")
    if separator != "-" or not first.isascii() or not second.isascii():
        raise ValueError("source-poll stream ID must contain ASCII digits")
    if not first.isdigit() or not second.isdigit():
        raise ValueError("source-poll stream ID must contain ASCII digits")
    return int(first), int(second)


def _bound_transition(
    history: SourcePollHistoryResponse,
    *,
    attempt_id: str,
    transition: Literal["started", "succeeded", "failed"],
) -> SourcePollTransition:
    matches = tuple(
        item
        for item in history.items
        if item.attempt.attempt_id == attempt_id and item.transition == transition
    )
    if len(matches) != 1:
        raise ValueError(
            f"source-poll history must contain exactly one {transition} transition for {attempt_id}"
        )
    return matches[0]


def _freshness_item(
    probe: DeploymentProbeEvidence, source: SourceName
) -> SourceFreshnessItem | None:
    return next((item for item in probe.source_freshness if item.source == source), None)


def _endpoint_passed(probe: DeploymentProbeEvidence, name: ProbeName) -> bool:
    return next(item for item in probe.endpoints if item.name == name).passed


def build_source_poll_recovery_drill(
    target: DeploymentTarget,
    before: DeploymentProbeEvidence,
    failure: DeploymentProbeEvidence,
    recovery: DeploymentProbeEvidence,
    history: SourcePollHistoryResponse,
    submission: SourcePollRecoveryDrillSubmission,
) -> SourcePollRecoveryDrillEvidence:
    """Bind an operator-run fault window to exact probes and retained poll transitions."""
    for probe in (before, failure, recovery):
        if probe.target_id != target.target_id or probe.target_sha256 != target.target_sha256:
            raise ValueError("source-poll recovery probes must match the exact deployment target")
        if (
            probe.expected_version != target.application_version
            or probe.expected_commit_sha != target.commit_sha
        ):
            raise ValueError("source-poll recovery probes must retain exact target metadata")
    if submission.source not in target.required_sources:
        raise ValueError("source-poll recovery source must be required by the deployment target")

    failure_started = _bound_transition(
        history,
        attempt_id=submission.failure_attempt_id,
        transition="started",
    )
    failure_terminal = _bound_transition(
        history,
        attempt_id=submission.failure_attempt_id,
        transition="failed",
    )
    recovery_started = _bound_transition(
        history,
        attempt_id=submission.recovery_attempt_id,
        transition="started",
    )
    recovery_terminal = _bound_transition(
        history,
        attempt_id=submission.recovery_attempt_id,
        transition="succeeded",
    )
    transitions = (failure_started, failure_terminal, recovery_started, recovery_terminal)
    if any(item.source != submission.source for item in transitions):
        raise ValueError("bound source-poll transitions must match the submitted source")
    if (
        failure_started.attempt.started_at != failure_terminal.attempt.started_at
        or recovery_started.attempt.started_at != recovery_terminal.attempt.started_at
    ):
        raise ValueError("terminal source-poll transitions must match their exact starts")
    if failure_terminal.attempt.completed_at is None:
        raise ValueError("failed source-poll transition must contain its completion time")
    if recovery_terminal.attempt.completed_at is None:
        raise ValueError("successful source-poll transition must contain its completion time")
    if tuple(_source_poll_stream_position(item.stream_id) for item in transitions) != tuple(
        sorted(_source_poll_stream_position(item.stream_id) for item in transitions)
    ):
        raise ValueError("bound source-poll transitions must be chronological")
    if not (
        before.observed_at
        <= submission.fault_started_at
        <= failure_started.attempt.started_at
        <= failure_terminal.attempt.completed_at
        <= failure.observed_at
        <= submission.fault_cleared_at
        <= recovery_started.attempt.started_at
        <= recovery_terminal.attempt.completed_at
        <= recovery.observed_at
        <= history.generated_at
    ):
        raise ValueError("source-poll recovery artifacts must follow the declared drill sequence")

    before_item = _freshness_item(before, submission.source)
    failure_item = _freshness_item(failure, submission.source)
    recovery_item = _freshness_item(recovery, submission.source)
    before_source_current = bool(
        before_item is not None
        and before_item.passed
        and before_item.poll_status == "healthy"
        and before_item.source_data_status == "current"
        and before_item.last_outcome == "succeeded"
    )
    observed_failure_code = failure_terminal.attempt.failure_code
    assert observed_failure_code is not None
    failure_source_degraded = bool(
        failure_item is not None
        and not failure_item.passed
        and failure_item.poll_status == "degraded"
        and failure_item.last_outcome == "failed"
        and failure_item.last_attempt_at == failure_terminal.attempt.started_at
        and failure_item.last_stage == failure_terminal.attempt.stage
        and failure_item.last_failure_code == observed_failure_code
        and failure_item.transport_attempts == failure_terminal.attempt.transport_attempts
        and failure_item.consecutive_failures >= 1
    )
    recovery_source_current = bool(
        recovery_item is not None
        and recovery_item.passed
        and recovery_item.poll_status == "healthy"
        and recovery_item.source_data_status == "current"
        and recovery_item.last_outcome == "succeeded"
        and recovery_item.last_stage == "complete"
        and recovery_item.last_attempt_at == recovery_terminal.attempt.started_at
        and recovery_item.last_success_at == recovery_terminal.attempt.completed_at
        and recovery_item.last_source_generated_at == recovery_terminal.attempt.source_generated_at
        and recovery_item.timestamp_basis == recovery_terminal.attempt.timestamp_basis
        and recovery_item.transport_attempts == recovery_terminal.attempt.transport_attempts
        and recovery_item.consecutive_failures == 0
    )
    failure_by_source = {item.source: item for item in failure.source_freshness}
    other_sources_maintained = all(
        source in failure_by_source and failure_by_source[source].passed
        for source in target.required_sources
        if source != submission.source
    )
    failure_freshness = next(item for item in failure.endpoints if item.name == "source_freshness")
    failure_surface_isolated = (
        not failure_freshness.passed
        and failure_freshness.failure_code == "unexpected_value"
        and all(item.passed for item in failure.endpoints if item.name != "source_freshness")
        and failure.certificate.passed
        and failure.observed_version == target.application_version
        and failure.observed_commit_sha == target.commit_sha
    )
    readiness_maintained = all(
        _endpoint_passed(probe, "readiness") for probe in (before, failure, recovery)
    )
    execution_boundary_maintained = all(
        probe.execution_boundary.passed for probe in (before, failure, recovery)
    )
    failure_code_matched = observed_failure_code == submission.expected_failure_code
    passed = (
        before.passed
        and not failure.passed
        and recovery.passed
        and before_source_current
        and failure_source_degraded
        and recovery_source_current
        and failure_code_matched
        and other_sources_maintained
        and failure_surface_isolated
        and readiness_maintained
        and execution_boundary_maintained
    )
    draft = SourcePollRecoveryDrillEvidence.model_construct(
        evidence_id="source-poll-recovery-" + "0" * 20,
        evidence_sha256="0" * 64,
        target_id=target.target_id,
        target_sha256=target.target_sha256,
        source=submission.source,
        expected_failure_code=submission.expected_failure_code,
        observed_failure_code=observed_failure_code,
        observed_failure_stage=failure_terminal.attempt.stage,
        fault_started_at=submission.fault_started_at,
        fault_cleared_at=submission.fault_cleared_at,
        fault_window_seconds=(
            submission.fault_cleared_at - submission.fault_started_at
        ).total_seconds(),
        before_observed_at=before.observed_at,
        failure_observed_at=failure.observed_at,
        observed_at=recovery.observed_at,
        recovery_observation_seconds=(
            recovery.observed_at - submission.fault_cleared_at
        ).total_seconds(),
        history_generated_at=history.generated_at,
        history_sha256=canonical_sha256(history.model_dump(mode="json", exclude_none=False)),
        history_item_count=history.count,
        before_probe_id=before.evidence_id,
        before_probe_sha256=before.evidence_sha256,
        failure_probe_id=failure.evidence_id,
        failure_probe_sha256=failure.evidence_sha256,
        recovery_probe_id=recovery.evidence_id,
        recovery_probe_sha256=recovery.evidence_sha256,
        failure_attempt_id=submission.failure_attempt_id,
        recovery_attempt_id=submission.recovery_attempt_id,
        failure_attempt_started_at=failure_started.attempt.started_at,
        failure_terminal_at=failure_terminal.attempt.completed_at,
        recovery_attempt_started_at=recovery_started.attempt.started_at,
        recovery_terminal_at=recovery_terminal.attempt.completed_at,
        failure_started_stream_id=failure_started.stream_id,
        failure_terminal_stream_id=failure_terminal.stream_id,
        recovery_started_stream_id=recovery_started.stream_id,
        recovery_terminal_stream_id=recovery_terminal.stream_id,
        before_probe_passed=before.passed,
        failure_probe_passed=failure.passed,
        recovery_probe_passed=recovery.passed,
        before_source_current=before_source_current,
        failure_source_degraded=failure_source_degraded,
        recovery_source_current=recovery_source_current,
        failure_code_matched=failure_code_matched,
        other_required_sources_maintained=other_sources_maintained,
        failure_surface_isolated=failure_surface_isolated,
        readiness_maintained=readiness_maintained,
        execution_boundary_maintained=execution_boundary_maintained,
        passed=passed,
    )
    digest = _model_sha256(draft, identity_fields={"evidence_id", "evidence_sha256"})
    return SourcePollRecoveryDrillEvidence(
        **draft.model_dump(mode="python", exclude={"evidence_id", "evidence_sha256"}),
        evidence_id=f"source-poll-recovery-{digest[:20]}",
        evidence_sha256=digest,
    )


class BackupEvidence(StrictModel):
    """Digest and bounded metadata for one PostgreSQL custom-format backup file."""

    kind: Literal["backup"] = "backup"
    schema_version: Literal["1.1.0"] = "1.1.0"
    rule_version: Literal["operational-observation-v2"] = "operational-observation-v2"
    identity_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    evidence_id: str = Field(pattern=r"^backup-evidence-[0-9a-f]{20}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_id: str = Field(pattern=r"^deployment-target-[0-9a-f]{20}$")
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    file_name: str = Field(min_length=1, max_length=255)
    file_size_bytes: int = Field(gt=0)
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    backup_format: Literal["postgres_custom"] = "postgres_custom"
    storage_scope: BackupStorageScope
    encrypted_at_rest: bool
    execution_enabled: Literal[False] = False
    caveats: tuple[str, ...] = BACKUP_CAVEATS

    @model_validator(mode="after")
    def validate_backup(self) -> BackupEvidence:
        _require_utc(self.observed_at, "observed_at")
        if PurePosixPath(self.file_name).name != self.file_name:
            raise ValueError("backup evidence stores a file name, never a path")
        if self.execution_enabled is not False or self.caveats != BACKUP_CAVEATS:
            raise ValueError("backup evidence must retain the closed execution boundary")
        _validate_identity(
            self,
            prefix="backup-evidence",
            identifier=self.evidence_id,
            digest=self.evidence_sha256,
            identity_fields={"evidence_id", "evidence_sha256"},
        )
        return self


class RestoreCheck(StrictModel):
    """One bounded result from a disposable restore environment."""

    name: RestoreCheckName
    passed: bool
    observed: str = Field(min_length=1, max_length=256)


class RestoreDrillSubmission(StrictModel):
    """Operator-completed restore checks before exact backup/target binding."""

    schema_version: Literal["1.1.0"] = "1.1.0"
    environment_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    started_at: datetime
    completed_at: datetime
    restored_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    production_data_overwritten: Literal[False] = False
    checks: tuple[RestoreCheck, ...] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_submission(self) -> RestoreDrillSubmission:
        started = _require_utc(self.started_at, "started_at")
        completed = _require_utc(self.completed_at, "completed_at")
        if completed < started:
            raise ValueError("restore completion cannot precede its start")
        if tuple(check.name for check in self.checks) != RESTORE_CHECKS:
            raise ValueError("restore checks must use the complete canonical set")
        return self


class RestoreDrillEvidence(StrictModel):
    """Content-addressed isolated restore result bound to one exact backup digest."""

    kind: Literal["restore_drill"] = "restore_drill"
    schema_version: Literal["1.1.0"] = "1.1.0"
    rule_version: Literal["operational-observation-v2"] = "operational-observation-v2"
    identity_algorithm: Literal["sha256-canonical-json-v1"] = "sha256-canonical-json-v1"
    evidence_id: str = Field(pattern=r"^restore-drill-[0-9a-f]{20}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_id: str = Field(pattern=r"^deployment-target-[0-9a-f]{20}$")
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    backup_evidence_id: str = Field(pattern=r"^backup-evidence-[0-9a-f]{20}$")
    backup_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    environment_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    started_at: datetime
    completed_at: datetime
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    restored_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    production_data_overwritten: Literal[False] = False
    checks: tuple[RestoreCheck, ...] = Field(min_length=5, max_length=5)
    passed: bool
    execution_enabled: Literal[False] = False
    caveats: tuple[str, ...] = DRILL_CAVEATS

    @model_validator(mode="after")
    def validate_restore(self) -> RestoreDrillEvidence:
        observed_at = _require_utc(self.observed_at, "observed_at")
        started = _require_utc(self.started_at, "started_at")
        completed = _require_utc(self.completed_at, "completed_at")
        if completed < started or observed_at != completed:
            raise ValueError("restore times must be chronological and end at observed_at")
        if abs(self.duration_seconds - (completed - started).total_seconds()) > 1e-6:
            raise ValueError("restore duration must match the observed times")
        if tuple(check.name for check in self.checks) != RESTORE_CHECKS:
            raise ValueError("restore checks must use the complete canonical set")
        if self.passed != all(check.passed for check in self.checks):
            raise ValueError("restore pass status must match every required check")
        if self.execution_enabled is not False or self.caveats != DRILL_CAVEATS:
            raise ValueError("restore evidence must retain the closed execution boundary")
        _validate_identity(
            self,
            prefix="restore-drill",
            identifier=self.evidence_id,
            digest=self.evidence_sha256,
            identity_fields={"evidence_id", "evidence_sha256"},
        )
        return self


def build_restore_drill(
    target: DeploymentTarget,
    backup: BackupEvidence,
    submission: RestoreDrillSubmission,
) -> RestoreDrillEvidence:
    """Bind operator-observed restore checks to one exact deployment backup."""
    if backup.target_id != target.target_id or backup.target_sha256 != target.target_sha256:
        raise ValueError("restore backup must match the exact deployment target")
    if submission.restored_commit_sha != target.commit_sha:
        raise ValueError("restore submission must match the reviewed deployment commit")
    if submission.started_at < backup.observed_at:
        raise ValueError("restore cannot start before the bound backup observation")
    draft = RestoreDrillEvidence.model_construct(
        evidence_id="restore-drill-" + "0" * 20,
        evidence_sha256="0" * 64,
        target_id=target.target_id,
        target_sha256=target.target_sha256,
        backup_evidence_id=backup.evidence_id,
        backup_file_sha256=backup.file_sha256,
        observed_at=submission.completed_at,
        environment_id=submission.environment_id,
        started_at=submission.started_at,
        completed_at=submission.completed_at,
        duration_seconds=(submission.completed_at - submission.started_at).total_seconds(),
        restored_commit_sha=submission.restored_commit_sha,
        production_data_overwritten=False,
        checks=submission.checks,
        passed=all(check.passed for check in submission.checks),
    )
    digest = _model_sha256(draft, identity_fields={"evidence_id", "evidence_sha256"})
    return RestoreDrillEvidence(
        **draft.model_dump(mode="python", exclude={"evidence_id", "evidence_sha256"}),
        evidence_id=f"restore-drill-{digest[:20]}",
        evidence_sha256=digest,
    )


OperationalEvidence = Annotated[
    DeploymentProbeEvidence
    | ResourceSnapshotEvidence
    | RestartRecoveryEvidence
    | BackupEvidence
    | RestoreDrillEvidence,
    Field(discriminator="kind"),
]

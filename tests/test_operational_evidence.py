"""Operational evidence identities, capture boundaries, campaigns, and CLI tests."""

from __future__ import annotations

import json
import ssl
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict, Unpack

import httpx
import pytest
from pydantic import ValidationError

from atlas_pulse.operational_evidence import (
    BackupEvidence,
    CertificateObservation,
    DeploymentProbeEvidence,
    DeploymentTarget,
    EndpointObservation,
    ExecutionBoundaryObservation,
    HostResourceObservation,
    OperationalEvidence,
    OperationalEvidenceReport,
    ResourceSnapshotSubmission,
    RestoreCheck,
    RestoreDrillSubmission,
    ServiceResourceObservation,
    build_backup_evidence,
    build_deployment_target,
    build_resource_snapshot,
    build_restart_recovery,
    build_restore_drill,
    capture_deployment_probe,
    capture_resource_snapshot,
    evaluate_operational_campaign,
    observe_certificate,
    render_operational_report,
)
from atlas_pulse.operational_evidence.base import PROBE_PATHS, RESTORE_CHECKS
from atlas_pulse.operational_evidence.cli import run_cli
from atlas_pulse.source_polling import SourceFreshnessItem, SourceFreshnessResponse

_COMMIT = "a" * 40
_START = datetime(2026, 9, 14, 12, tzinfo=UTC)


class _HandlerOptions(TypedDict, total=False):
    commit_sha: str | None
    readiness_status: str
    boundary_overrides: dict[str, object] | None
    invalid_events: bool
    invalid_freshness: bool


def _target(*, commit_sha: str = _COMMIT, deployed_at: datetime = _START) -> DeploymentTarget:
    return build_deployment_target(
        origin="https://atlas.example/",
        commit_sha=commit_sha,
        application_version="0.12.0",
        environment="free-tier-public",
        deployed_at=deployed_at,
    )


def _certificate(
    _target_value: DeploymentTarget,
    observed_at: datetime,
    _timeout: float,
) -> CertificateObservation:
    return CertificateObservation(
        checked_at=observed_at,
        leaf_sha256="b" * 64,
        not_before=observed_at - timedelta(days=1),
        not_after=observed_at + timedelta(days=89),
        remaining_seconds=89 * 86_400,
        hostname_verified=True,
        tls_version="TLSv1.3",
        passed=True,
    )


def _handler(
    target: DeploymentTarget,
    observed_at: datetime,
    *,
    commit_sha: str | None = None,
    readiness_status: str = "ready",
    boundary_overrides: dict[str, object] | None = None,
    invalid_events: bool = False,
    invalid_freshness: bool = False,
) -> httpx.MockTransport:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/healthz":
            payload: object = {
                "status": "ok",
                "version": target.application_version,
                "commit_sha": commit_sha or target.commit_sha,
            }
        elif request.url.path == "/api/readyz":
            payload = {
                "status": readiness_status,
                "version": target.application_version,
                "commit_sha": commit_sha or target.commit_sha,
            }
        elif request.url.path == "/api/v1/source-freshness":
            payload = (
                {"passed": True}
                if invalid_freshness
                else SourceFreshnessResponse(
                    generated_at=observed_at,
                    items=tuple(
                        SourceFreshnessItem(
                            source=source,
                            interval_seconds=900 if source == "gdelt" else 60,
                            poll_stale_after_seconds=2700 if source == "gdelt" else 180,
                            source_stale_after_seconds=3600 if source == "gdelt" else 600,
                            poll_status="healthy",
                            source_data_status="current",
                            last_outcome="succeeded",
                            last_stage="complete",
                            last_attempt_at=observed_at - timedelta(seconds=30),
                            last_success_at=observed_at - timedelta(seconds=20),
                            last_source_generated_at=(
                                observed_at - timedelta(seconds=120 if source == "gdelt" else 60)
                            ),
                            last_success_age_seconds=20,
                            source_age_seconds=120 if source == "gdelt" else 60,
                            consecutive_failures=0,
                            transport_attempts=1,
                            timestamp_basis="source_metadata",
                            passed=True,
                        )
                        for source in ("gdelt", "usgs")
                    ),
                    passed=True,
                ).model_dump(mode="json")
            )
        elif request.url.path == "/api/v1/events":
            payload = (
                {"count": 1, "items": [{"event": {"source": "unknown"}}]}
                if invalid_events
                else {
                    "count": 2,
                    "items": [
                        {
                            "event": {
                                "source": "gdelt",
                                "ingested_at": (observed_at - timedelta(seconds=120)).isoformat(),
                            }
                        },
                        {
                            "event": {
                                "source": "usgs",
                                "ingested_at": (observed_at - timedelta(seconds=60)).isoformat(),
                            }
                        },
                    ],
                }
            )
        else:
            execution: dict[str, object] = {
                "status": "not_started",
                "agent_model_invoked": False,
                "agent_network_accessed": False,
                "agent_tools_invoked": False,
                "answer_generated": False,
                "agent_side_effects_performed": False,
            }
            if boundary_overrides:
                execution.update(boundary_overrides)
            payload = {
                "manifest": {
                    "status": "blocked",
                    "release": {"status": "not_supplied"},
                    "authorization": {"checks": [{} for _ in range(11)]},
                    "execution": execution,
                }
            }
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(respond)


def _probe(
    target: DeploymentTarget,
    observed_at: datetime,
    **handler_options: Unpack[_HandlerOptions],
) -> DeploymentProbeEvidence:
    return capture_deployment_probe(
        target,
        observed_at=observed_at,
        transport=_handler(target, observed_at, **handler_options),
        certificate_loader=_certificate,
    )


def _resource_submission(
    target: DeploymentTarget,
    observed_at: datetime,
    *,
    unhealthy: str | None = None,
) -> ResourceSnapshotSubmission:
    services = tuple(
        ServiceResourceObservation(
            service=service,
            container_id=f"{index + 1:012x}",
            state="running",
            health="unhealthy" if service == unhealthy else "healthy",
            cpu_percent=2.5,
            memory_used_bytes=128 * 1024 * 1024,
            memory_limit_bytes=512 * 1024 * 1024,
            pids=12,
            restart_count=0,
        )
        for index, service in enumerate(target.required_services)
    )
    return ResourceSnapshotSubmission(
        observed_at=observed_at,
        host=HostResourceObservation(
            load_1m=0.4,
            memory_total_bytes=12 * 1024**3,
            memory_available_bytes=8 * 1024**3,
            disk_total_bytes=100 * 1024**3,
            disk_available_bytes=70 * 1024**3,
        ),
        services=services,
    )


def _restore_submission(
    *,
    started_at: datetime,
    completed_at: datetime,
    failed: str | None = None,
) -> RestoreDrillSubmission:
    return RestoreDrillSubmission(
        environment_id="disposable-restore-01",
        started_at=started_at,
        completed_at=completed_at,
        restored_commit_sha=_COMMIT,
        checks=tuple(
            RestoreCheck(name=name, passed=name != failed, observed=f"observed {name}")
            for name in RESTORE_CHECKS
        ),
    )


def test_target_is_canonical_content_addressed_and_execution_closed() -> None:
    first = _target()
    repeated = _target()

    assert first == repeated
    assert first.origin == "https://atlas.example"
    assert first.target_id == f"deployment-target-{first.target_sha256[:20]}"
    assert first.required_sources == ("gdelt", "usgs")
    assert "edge" in first.required_services
    assert "caddy" not in first.required_services
    assert first.execution_enabled is False
    assert any("execution disabled" in caveat for caveat in first.caveats)
    assert DeploymentTarget.model_validate_json(first.model_dump_json()) == first


@pytest.mark.parametrize(
    "origin",
    [
        "http://atlas.example",
        "https://user:pass@atlas.example",
        "https://atlas.example/private",
        "https://atlas.example?secret=yes",
        "https://localhost",
        "https://service.internal",
        "https://127.0.0.1",
        "https://10.1.2.3",
        "https://atlas.example:8443",
    ],
)
def test_target_rejects_non_public_or_noncanonical_origins(origin: str) -> None:
    with pytest.raises(ValueError, match=r"origin|local|globally|port"):
        build_deployment_target(
            origin=origin,
            commit_sha=_COMMIT,
            application_version="0.12.0",
            environment="free-tier-public",
            deployed_at=_START,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("required_sources", ("usgs", "usgs"), "sources"),
        ("required_services", ("web", "api"), "services"),
        ("required_services", ("api", "not_valid"), "Compose service"),
        ("compose_files", ("../compose.yaml",), "repository-relative"),
        ("compose_files", ("deploy//compose.yaml",), "repository-relative"),
        ("deployed_at", datetime(2026, 9, 14, 12), "timezone-aware"),
    ],
)
def test_target_rejects_invalid_scope(field: str, value: object, message: str) -> None:
    data = _target().model_dump(mode="python")
    data[field] = value
    with pytest.raises(ValidationError, match=message):
        DeploymentTarget.model_validate(data)


def test_target_rejects_tampering_even_when_fields_are_structurally_valid() -> None:
    data = _target().model_dump(mode="python")
    data["environment"] = "different-public-host"
    with pytest.raises(ValidationError, match="SHA-256"):
        DeploymentTarget.model_validate(data)


def test_successful_probe_binds_commit_tls_sources_and_default_deny() -> None:
    target = _target()
    probe = _probe(target, _START + timedelta(minutes=5))

    assert probe.passed is True
    assert probe.observed_commit_sha == _COMMIT
    assert tuple(item.name for item in probe.endpoints) == tuple(PROBE_PATHS)
    assert all(item.passed for item in probe.endpoints)
    assert probe.execution_boundary.passed is True
    assert probe.execution_boundary.agent_model_invoked is False
    assert tuple(item.source for item in probe.source_visibility) == ("gdelt", "usgs")
    assert tuple(item.age_seconds for item in probe.source_visibility) == (120.0, 60.0)
    assert tuple(item.source for item in probe.source_freshness) == ("gdelt", "usgs")
    assert all(item.passed for item in probe.source_freshness)
    assert probe.evidence_id == f"deployment-probe-{probe.evidence_sha256[:20]}"
    serialized = probe.model_dump_json()
    assert "operational boundary" not in serialized
    assert DeploymentProbeEvidence.model_validate_json(serialized) == probe


@pytest.mark.parametrize(
    ("options", "failed_endpoint"),
    [
        ({"commit_sha": "c" * 40}, "health"),
        ({"readiness_status": "starting"}, "readiness"),
        ({"boundary_overrides": {"agent_tools_invoked": True}}, "agent_preflight"),
        ({"invalid_events": True}, "events"),
        ({"invalid_freshness": True}, "source_freshness"),
    ],
)
def test_probe_records_semantic_failures_without_fabricating_passes(
    options: _HandlerOptions, failed_endpoint: str
) -> None:
    probe = _probe(_target(), _START + timedelta(minutes=5), **options)

    failed = next(item for item in probe.endpoints if item.name == failed_endpoint)
    assert probe.passed is False
    assert failed.passed is False
    assert failed.failure_code in {"invalid_payload", "unexpected_value"}


def test_probe_records_http_connection_and_oversized_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/healthz":
            raise httpx.ConnectError("offline", request=request)
        if request.url.path == "/api/readyz":
            return httpx.Response(503, content=b"unready")
        return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))

    probe = capture_deployment_probe(
        target,
        observed_at=_START + timedelta(minutes=1),
        transport=httpx.MockTransport(handler),
        certificate_loader=lambda _target, observed, _timeout: CertificateObservation(
            checked_at=observed,
            hostname_verified=False,
            passed=False,
            failure_code="tls_error",
        ),
    )

    assert probe.passed is False
    assert probe.endpoints[0].failure_code == "dns_or_connect_error"
    assert probe.endpoints[1].failure_code == "http_status"
    assert probe.endpoints[2].failure_code == "response_too_large"
    assert probe.endpoints[2].body_bytes == 1024 * 1024
    assert probe.certificate.failure_code == "tls_error"

    monkeypatch.setattr(
        "atlas_pulse.operational_evidence.capture._public_resolution", lambda _origin: False
    )
    unreachable = capture_deployment_probe(target, observed_at=_START + timedelta(minutes=2))
    assert all(item.failure_code == "dns_or_connect_error" for item in unreachable.endpoints)


def test_probe_models_reject_internally_inconsistent_observations() -> None:
    with pytest.raises(ValidationError, match="response truncation"):
        EndpointObservation(
            name="health",
            path=PROBE_PATHS["health"],
            status_code=200,
            latency_ms=1,
            body_bytes=2,
            response_sha256="a" * 64,
            body_truncated=True,
            passed=False,
            failure_code="invalid_payload",
        )
    with pytest.raises(ValidationError, match="execution-boundary"):
        ExecutionBoundaryObservation(passed=True)
    with pytest.raises(ValidationError, match="remaining seconds"):
        CertificateObservation(
            checked_at=_START,
            leaf_sha256="a" * 64,
            not_before=_START - timedelta(days=1),
            not_after=_START + timedelta(days=1),
            remaining_seconds=1,
            hostname_verified=True,
            tls_version="TLSv1.3",
            passed=True,
        )


def test_resource_snapshot_is_bound_and_fails_closed_on_unhealthy_service() -> None:
    target = _target()
    healthy = build_resource_snapshot(target, _resource_submission(target, _START))
    unhealthy = build_resource_snapshot(
        target,
        _resource_submission(target, _START + timedelta(minutes=1), unhealthy="api"),
    )

    assert healthy.passed is True
    assert unhealthy.passed is False
    assert healthy.evidence_id.startswith("resource-snapshot-")
    with pytest.raises(ValidationError, match="memory use"):
        ServiceResourceObservation(
            service="api",
            container_id="a" * 12,
            state="running",
            health="healthy",
            cpu_percent=1,
            memory_used_bytes=2,
            memory_limit_bytes=1,
            pids=1,
            restart_count=0,
        )


def test_resource_collector_uses_bounded_compose_and_stats_fields(tmp_path: Path) -> None:
    target = _target()
    (tmp_path / "deploy/free-tier").mkdir(parents=True)
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    (tmp_path / "deploy/free-tier/compose.yaml").write_text("services: {}\n")
    ps = [
        {
            "ID": f"{index + 1:012x}",
            "Service": service,
            "State": "running",
            "Health": "healthy" if service in {"api", "postgres", "valkey", "web"} else "",
        }
        for index, service in enumerate(target.required_services)
    ]
    stats = "\n".join(
        json.dumps(
            {
                "ID": row["ID"],
                "CPUPerc": "1.25%",
                "MemUsage": "128MiB / 512MiB",
                "PIDs": "9",
            }
        )
        for row in ps
    )
    commands: list[tuple[str, ...]] = []

    def runner(arguments: Sequence[str], _cwd: Path) -> str:
        command = tuple(arguments)
        commands.append(command)
        return json.dumps(ps) if command[1] == "compose" else stats

    evidence = capture_resource_snapshot(
        target,
        tmp_path,
        observed_at=_START + timedelta(minutes=1),
        command_runner=runner,
        host_loader=lambda _root: _resource_submission(target, _START).host,
    )

    assert evidence.passed is True
    assert len(evidence.services) == 8
    assert evidence.services[0].memory_used_bytes == 128 * 1024**2
    assert evidence.services[0].memory_limit_bytes == 512 * 1024**2
    assert all("inspect" not in command for command in commands)
    assert all("logs" not in command for command in commands)


@pytest.mark.parametrize(
    "bad_output",
    ["not-json", "[1]", json.dumps({"ID": "a" * 12, "State": "running"})],
)
def test_resource_collector_rejects_invalid_docker_metadata(
    tmp_path: Path,
    bad_output: str,
) -> None:
    target = _target()
    (tmp_path / "deploy/free-tier").mkdir(parents=True)
    (tmp_path / "compose.yaml").touch()
    (tmp_path / "deploy/free-tier/compose.yaml").touch()
    with pytest.raises(ValueError, match=r"JSON|Service"):
        capture_resource_snapshot(
            target,
            tmp_path,
            command_runner=lambda _arguments, _cwd: bad_output,
            host_loader=lambda _root: _resource_submission(target, _START).host,
        )


def test_resource_collector_rejects_missing_project_or_compose_file(tmp_path: Path) -> None:
    target = _target()
    with pytest.raises(ValueError, match="project directory"):
        capture_resource_snapshot(target, tmp_path / "missing")
    with pytest.raises(ValueError, match="Compose files"):
        capture_resource_snapshot(target, tmp_path)


class _FakeSocket:
    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeTlsSocket(_FakeSocket):
    def getpeercert(self, *, binary_form: bool = False) -> bytes | dict[str, str]:
        if binary_form:
            return b"leaf-certificate"
        return {
            "notBefore": "Sep 13 12:00:00 2026 GMT",
            "notAfter": "Dec 12 12:00:00 2026 GMT",
        }

    def version(self) -> str:
        return "TLSv1.3"


class _FakeTlsContext:
    def wrap_socket(self, _raw: object, *, server_hostname: str) -> _FakeTlsSocket:
        assert server_hostname == "atlas.example"
        return _FakeTlsSocket()


def test_certificate_observer_hashes_verified_leaf_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssl, "create_default_context", _FakeTlsContext)
    monkeypatch.setattr(
        "atlas_pulse.operational_evidence.capture.socket.create_connection",
        lambda *_args, **_kwargs: _FakeSocket(),
    )

    observed = observe_certificate(_target(), _START, 5)

    assert observed.passed is True
    assert observed.hostname_verified is True
    assert observed.tls_version == "TLSv1.3"
    assert observed.remaining_seconds == 89 * 86_400


@pytest.mark.parametrize(
    ("error", "failure_code"),
    [
        (TimeoutError(), "timeout"),
        (ssl.SSLError("certificate rejected"), "tls_error"),
        (OSError("network unavailable"), "dns_or_connect_error"),
    ],
)
def test_certificate_observer_records_bounded_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    failure_code: str,
) -> None:
    def fail() -> object:
        raise error

    monkeypatch.setattr(ssl, "create_default_context", fail)
    observed = observe_certificate(_target(), _START, 5)
    assert observed.passed is False
    assert observed.failure_code == failure_code
    assert observed.leaf_sha256 is None


def test_restart_recovery_cross_validates_target_time_and_service() -> None:
    target = _target()
    before = _probe(target, _START + timedelta(hours=1))
    after = _probe(target, _START + timedelta(hours=1, minutes=2))
    recovery = build_restart_recovery(
        target,
        before,
        after,
        trigger="planned_stack_restart",
        services=("api", "web"),
        restart_started_at=_START + timedelta(hours=1, minutes=1),
    )

    assert recovery.passed is True
    assert recovery.recovery_seconds == 60
    assert recovery.services == ("api", "web")
    with pytest.raises(ValueError, match="between"):
        build_restart_recovery(
            target,
            before,
            after,
            trigger="planned_stack_restart",
            services=("api",),
            restart_started_at=_START,
        )
    with pytest.raises(ValueError, match="belong"):
        build_restart_recovery(
            target,
            before,
            after,
            trigger="planned_stack_restart",
            services=("unknown",),
            restart_started_at=_START + timedelta(hours=1, minutes=1),
        )
    with pytest.raises(ValueError, match="exact deployment"):
        build_restart_recovery(
            _target(commit_sha="c" * 40),
            before,
            after,
            trigger="planned_stack_restart",
            services=("api",),
            restart_started_at=_START + timedelta(hours=1, minutes=1),
        )


def test_backup_hashing_rejects_wrong_format_symlink_and_empty_file(tmp_path: Path) -> None:
    target = _target()
    backup_path = tmp_path / "atlas.dump"
    backup_path.write_bytes(b"PGDMP" + b"database-bytes")
    evidence = build_backup_evidence(
        target,
        backup_path,
        storage_scope="off_host_verified",
        encrypted_at_rest=True,
        observed_at=_START + timedelta(hours=2),
    )

    assert evidence.file_name == "atlas.dump"
    assert evidence.file_size_bytes == backup_path.stat().st_size
    assert evidence.storage_scope == "off_host_verified"
    assert BackupEvidence.model_validate_json(evidence.model_dump_json()) == evidence

    invalid = tmp_path / "invalid.dump"
    invalid.write_bytes(b"not-a-postgres-dump")
    with pytest.raises(ValueError, match="custom-format"):
        build_backup_evidence(target, invalid, storage_scope="local_only", encrypted_at_rest=False)
    empty = tmp_path / "empty.dump"
    empty.touch()
    with pytest.raises(ValueError, match="non-empty"):
        build_backup_evidence(target, empty, storage_scope="local_only", encrypted_at_rest=False)
    linked = tmp_path / "linked.dump"
    linked.symlink_to(backup_path)
    with pytest.raises(ValueError, match="cannot open"):
        build_backup_evidence(target, linked, storage_scope="local_only", encrypted_at_rest=False)


def test_restore_drill_binds_exact_backup_commit_and_all_checks(tmp_path: Path) -> None:
    target = _target()
    backup_path = tmp_path / "atlas.dump"
    backup_path.write_bytes(b"PGDMPcontent")
    backup = build_backup_evidence(
        target,
        backup_path,
        storage_scope="off_host_verified",
        encrypted_at_rest=True,
        observed_at=_START + timedelta(hours=1),
    )
    submission = _restore_submission(
        started_at=_START + timedelta(hours=2),
        completed_at=_START + timedelta(hours=3),
    )
    restore = build_restore_drill(target, backup, submission)

    assert restore.passed is True
    assert restore.duration_seconds == 3600
    assert restore.backup_file_sha256 == backup.file_sha256
    failed = build_restore_drill(
        target,
        backup,
        _restore_submission(
            started_at=_START + timedelta(hours=4),
            completed_at=_START + timedelta(hours=5),
            failed="database_readable",
        ),
    )
    assert failed.passed is False
    wrong_commit = submission.model_copy(update={"restored_commit_sha": "c" * 40})
    with pytest.raises(ValueError, match="reviewed deployment commit"):
        build_restore_drill(target, backup, wrong_commit)
    wrong = restore.model_copy(update={"restored_commit_sha": "c" * 40})
    with pytest.raises(ValueError, match="deployment commit"):
        evaluate_operational_campaign(
            target,
            (backup, wrong),
            generated_at=_START + timedelta(hours=4),
        )


def _complete_campaign(
    target: DeploymentTarget,
    tmp_path: Path,
) -> tuple[tuple[OperationalEvidence, ...], OperationalEvidenceReport]:
    evidence: list[OperationalEvidence] = []
    probes: list[DeploymentProbeEvidence] = []
    for day in range(30):
        observed = _START + timedelta(days=day, minutes=5)
        probe = _probe(target, observed)
        probes.append(probe)
        evidence.extend(
            (
                probe,
                build_resource_snapshot(target, _resource_submission(target, observed)),
            )
        )
    backup_path = tmp_path / "atlas.dump"
    backup_path.write_bytes(b"PGDMP" + b"complete-campaign")
    backup = build_backup_evidence(
        target,
        backup_path,
        storage_scope="off_host_verified",
        encrypted_at_rest=True,
        observed_at=_START + timedelta(days=1, hours=1),
    )
    restore = build_restore_drill(
        target,
        backup,
        _restore_submission(
            started_at=_START + timedelta(days=1, hours=2),
            completed_at=_START + timedelta(days=1, hours=3),
        ),
    )
    restart = build_restart_recovery(
        target,
        probes[5],
        probes[6],
        trigger="planned_host_reboot",
        services=target.required_services,
        restart_started_at=_START + timedelta(days=5, hours=12),
    )
    evidence.extend((backup, restore, restart))
    values = tuple(evidence)
    report = evaluate_operational_campaign(
        target,
        values,
        generated_at=_START + timedelta(days=30),
    )
    return values, report


def test_complete_campaign_means_minimum_samples_not_sla(tmp_path: Path) -> None:
    target = _target()
    evidence, report = _complete_campaign(target, tmp_path)

    assert len(evidence) == 63
    assert report.status == "minimum_observation_set_complete"
    assert report.passed_requirement_count == 8
    assert report.blocked_requirement_count == 0
    assert report.calendar_span_days == 30
    assert report.passing_probe_days == 30
    assert report.resource_snapshot_days == 30
    assert report.longest_passing_probe_streak_days == 30
    assert report.longest_certificate_streak_days == 30
    assert report.aligned_probe_resource_days == 30
    assert report.longest_aligned_sample_streak_days == 30
    assert report.sampled_probe_success_rate == 1
    assert tuple(item.source for item in report.source_freshness) == ("gdelt", "usgs")
    assert all(item.longest_passing_streak_days == 30 for item in report.source_freshness)
    assert all(item.passing_observation_count == 30 for item in report.source_freshness)
    assert report.claim_scope == "sampled_observations_not_an_sla"
    assert report.execution_enabled is False
    assert all(item.p95_passed_latency_ms is not None for item in report.endpoint_latency)
    assert OperationalEvidenceReport.model_validate_json(report.model_dump_json()) == report
    markdown = render_operational_report(target, report)
    assert "MINIMUM OBSERVATION SET COMPLETE" in markdown
    assert "not an SLA" in markdown
    assert "Source poll and upstream freshness" in markdown
    assert _COMMIT in markdown


def test_one_missing_freshness_day_breaks_the_required_source_streak(tmp_path: Path) -> None:
    target = _target()
    evidence, _ = _complete_campaign(target, tmp_path)
    victim = next(
        item
        for item in evidence
        if isinstance(item, DeploymentProbeEvidence)
        and item.observed_at.date() == (_START + timedelta(days=15)).date()
    )
    replacement = _probe(target, victim.observed_at, invalid_freshness=True)
    report = evaluate_operational_campaign(
        target,
        tuple(replacement if item is victim else item for item in evidence),
        generated_at=_START + timedelta(days=30),
    )

    freshness_gate = next(
        item
        for item in report.requirements
        if item.requirement_id == "required_sources_fresh_30_days"
    )
    assert freshness_gate.passed is False
    assert all(item.longest_passing_streak_days == 15 for item in report.source_freshness)
    assert report.status == "insufficient_evidence"


def test_incomplete_campaign_reports_every_missing_requirement() -> None:
    target = _target()
    report = evaluate_operational_campaign(target, (), generated_at=_START)

    assert report.status == "insufficient_evidence"
    assert report.evidence_count == 0
    assert report.sampled_probe_success_rate == 0
    assert report.passed_requirement_count == 0
    assert report.blocked_requirement_count == 8
    assert all(item.blocking_reason for item in report.requirements)
    assert render_operational_report(target, report).count("blocked:") == 8


def test_scattered_samples_do_not_form_a_consecutive_campaign() -> None:
    target = _target()
    evidence: list[OperationalEvidence] = []
    for day in range(30):
        observed = _START + timedelta(days=day * 2, minutes=5)
        evidence.extend(
            (
                _probe(target, observed),
                build_resource_snapshot(target, _resource_submission(target, observed)),
            )
        )

    report = evaluate_operational_campaign(
        target,
        tuple(evidence),
        generated_at=_START + timedelta(days=60),
    )

    probe_gate = next(
        item for item in report.requirements if item.requirement_id == "passing_probe_30_days"
    )
    resource_gate = next(
        item for item in report.requirements if item.requirement_id == "resource_snapshot_30_days"
    )
    assert report.passing_probe_days == 30
    assert report.longest_passing_probe_streak_days == 1
    assert report.longest_aligned_sample_streak_days == 1
    assert probe_gate.passed is False
    assert resource_gate.passed is False
    assert report.status == "insufficient_evidence"


def test_passing_restore_of_local_backup_does_not_satisfy_durability_gate(
    tmp_path: Path,
) -> None:
    target = _target()
    backup_path = tmp_path / "local.dump"
    backup_path.write_bytes(b"PGDMP" + b"local-only")
    backup = build_backup_evidence(
        target,
        backup_path,
        storage_scope="local_only",
        encrypted_at_rest=True,
        observed_at=_START + timedelta(minutes=1),
    )
    restore = build_restore_drill(
        target,
        backup,
        _restore_submission(
            started_at=_START + timedelta(minutes=2),
            completed_at=_START + timedelta(minutes=3),
        ),
    )

    report = evaluate_operational_campaign(
        target,
        (backup, restore),
        generated_at=_START + timedelta(minutes=4),
    )

    restore_gate = next(
        item for item in report.requirements if item.requirement_id == "restore_drill_passed"
    )
    assert restore.passed is True
    assert report.restore_drill_count == 1
    assert report.passing_restore_drill_count == 0
    assert restore_gate.passed is False
    assert restore_gate.blocking_reason is not None
    assert "encrypted off-host" in restore_gate.blocking_reason


def test_campaign_rejects_cross_target_duplicate_missing_and_tampered_links(
    tmp_path: Path,
) -> None:
    target = _target()
    other = _target(commit_sha="c" * 40)
    probe = _probe(target, _START + timedelta(minutes=1))
    with pytest.raises(ValueError, match="exact deployment"):
        evaluate_operational_campaign(
            other,
            (probe,),
            generated_at=_START + timedelta(minutes=2),
        )
    with pytest.raises(ValueError, match="unique"):
        evaluate_operational_campaign(
            target,
            (probe, probe),
            generated_at=_START + timedelta(minutes=2),
        )
    with pytest.raises(ValueError, match="target metadata"):
        evaluate_operational_campaign(
            target,
            (probe.model_copy(update={"expected_commit_sha": "d" * 40}),),
            generated_at=_START + timedelta(minutes=2),
        )
    resource = build_resource_snapshot(
        target,
        _resource_submission(target, _START + timedelta(minutes=1)),
    )
    with pytest.raises(ValueError, match="required services"):
        evaluate_operational_campaign(
            target,
            (resource.model_copy(update={"required_services": ("api",)}),),
            generated_at=_START + timedelta(minutes=2),
        )

    backup_path = tmp_path / "atlas.dump"
    backup_path.write_bytes(b"PGDMPdata")
    backup = build_backup_evidence(
        target,
        backup_path,
        storage_scope="off_host_verified",
        encrypted_at_rest=True,
        observed_at=_START + timedelta(minutes=2),
    )
    restore = build_restore_drill(
        target,
        backup,
        _restore_submission(
            started_at=_START + timedelta(minutes=3),
            completed_at=_START + timedelta(minutes=4),
        ),
    )
    with pytest.raises(ValueError, match="outside the campaign"):
        evaluate_operational_campaign(
            target,
            (restore,),
            generated_at=_START + timedelta(minutes=5),
        )

    report = evaluate_operational_campaign(
        target,
        (probe,),
        generated_at=_START + timedelta(minutes=2),
    )
    data = report.model_dump(mode="python")
    data["probe_count"] = 2
    with pytest.raises(ValidationError, match=r"reference|success rate|identity"):
        OperationalEvidenceReport.model_validate(data)


def test_cli_target_resource_backup_restore_restart_and_report(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "target.json"
    assert (
        run_cli(
            [
                "target",
                "--origin",
                "https://atlas.example",
                "--commit-sha",
                _COMMIT,
                "--application-version",
                "0.12.0",
                "--deployed-at",
                "2026-09-14T12:00:00Z",
                "--output",
                str(target_path),
            ]
        )
        == 0
    )
    target = DeploymentTarget.model_validate_json(target_path.read_text())
    before = _probe(target, _START + timedelta(minutes=1))
    after = _probe(target, _START + timedelta(minutes=3))
    before_path = tmp_path / "before.evidence.json"
    after_path = tmp_path / "after.evidence.json"
    before_path.write_text(before.model_dump_json())
    after_path.write_text(after.model_dump_json())

    resource_submission = tmp_path / "resource-submission.json"
    resource_submission.write_text(_resource_submission(target, _START).model_dump_json())
    resource_path = tmp_path / "resource.evidence.json"
    assert (
        run_cli(
            [
                "record-resource",
                "--target",
                str(target_path),
                "--submission",
                str(resource_submission),
                "--output",
                str(resource_path),
            ]
        )
        == 0
    )
    failing_resource_submission = tmp_path / "failing-resource-submission.json"
    failing_resource_submission.write_text(
        _resource_submission(target, _START, unhealthy="api").model_dump_json()
    )
    failing_resource_path = tmp_path / "failing-resource.evidence.json"
    assert (
        run_cli(
            [
                "record-resource",
                "--target",
                str(target_path),
                "--submission",
                str(failing_resource_submission),
                "--output",
                str(failing_resource_path),
            ]
        )
        == 1
    )
    assert json.loads(failing_resource_path.read_text())["passed"] is False

    restart_path = tmp_path / "restart.evidence.json"
    assert (
        run_cli(
            [
                "record-restart",
                "--target",
                str(target_path),
                "--before-probe",
                str(before_path),
                "--after-probe",
                str(after_path),
                "--restart-started-at",
                "2026-09-14T12:02:00Z",
                "--trigger",
                "planned_stack_restart",
                "--service",
                "api",
                "--output",
                str(restart_path),
            ]
        )
        == 0
    )

    backup_file = tmp_path / "atlas.dump"
    backup_file.write_bytes(b"PGDMPcli")
    backup_path = tmp_path / "backup.evidence.json"
    assert (
        run_cli(
            [
                "record-backup",
                "--target",
                str(target_path),
                "--backup-file",
                str(backup_file),
                "--storage-scope",
                "off_host_verified",
                "--encrypted-at-rest",
                "--observed-at",
                "2026-09-14T12:04:00Z",
                "--output",
                str(backup_path),
            ]
        )
        == 0
    )
    backup = BackupEvidence.model_validate_json(backup_path.read_text())
    restore_submission_path = tmp_path / "restore-submission.json"
    restore_submission_path.write_text(
        _restore_submission(
            started_at=_START + timedelta(minutes=5),
            completed_at=_START + timedelta(minutes=6),
        ).model_dump_json()
    )
    restore_path = tmp_path / "restore.evidence.json"
    assert (
        run_cli(
            [
                "record-restore",
                "--target",
                str(target_path),
                "--backup-evidence",
                str(backup_path),
                "--submission",
                str(restore_submission_path),
                "--output",
                str(restore_path),
            ]
        )
        == 0
    )
    assert backup.file_sha256 in restore_path.read_text()

    report_json = tmp_path / "report.json"
    report_markdown = tmp_path / "report.md"
    assert (
        run_cli(
            [
                "report",
                "--target",
                str(target_path),
                "--evidence-dir",
                str(tmp_path),
                "--generated-at",
                "2026-09-14T12:10:00Z",
                "--output-json",
                str(report_json),
                "--output-markdown",
                str(report_markdown),
            ]
        )
        == 0
    )
    assert "insufficient_evidence" in report_json.read_text()
    assert "not an SLA" in report_markdown.read_text()
    assert (
        run_cli(
            [
                "target",
                "--origin",
                "https://atlas.example",
                "--commit-sha",
                _COMMIT,
                "--application-version",
                "0.12.0",
                "--deployed-at",
                "2026-09-14T12:00:00Z",
                "--output",
                str(target_path),
            ]
        )
        == 2
    )


def test_cli_safe_loader_rejects_symlink_and_oversized_json(tmp_path: Path) -> None:
    target_path = tmp_path / "target.json"
    target_path.write_text(_target().model_dump_json())
    linked = tmp_path / "linked.json"
    linked.symlink_to(target_path)
    assert (
        run_cli(
            [
                "report",
                "--target",
                str(linked),
                "--generated-at",
                "2026-09-14T12:00:00Z",
                "--output-json",
                str(tmp_path / "report.json"),
                "--output-markdown",
                str(tmp_path / "report.md"),
            ]
        )
        == 2
    )
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (5 * 1024 * 1024 + 1))
    assert (
        run_cli(
            [
                "report",
                "--target",
                str(oversized),
                "--generated-at",
                "2026-09-14T12:00:00Z",
                "--output-json",
                str(tmp_path / "other.json"),
                "--output-markdown",
                str(tmp_path / "other.md"),
            ]
        )
        == 2
    )


def test_evidence_union_round_trips_by_discriminator() -> None:
    probe = _probe(_target(), _START + timedelta(minutes=1))
    payload = json.loads(probe.model_dump_json())
    assert payload["kind"] == "deployment_probe"
    assert probe.execution_enabled is False

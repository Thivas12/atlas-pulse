"""Operator CLI for public deployment observations and 30-day evidence reports."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import BaseModel, TypeAdapter, ValidationError

from atlas_pulse.operational_evidence.base import (
    BackupEvidence,
    DeploymentProbeEvidence,
    DeploymentTarget,
    OperationalEvidence,
    ResourceSnapshotSubmission,
    RestartTrigger,
    RestoreDrillSubmission,
    build_deployment_target,
    build_resource_snapshot,
    build_restart_recovery,
    build_restore_drill,
)
from atlas_pulse.operational_evidence.campaign import evaluate_operational_campaign
from atlas_pulse.operational_evidence.capture import (
    build_backup_evidence,
    capture_deployment_probe,
    capture_resource_snapshot,
)
from atlas_pulse.operational_evidence.report import render_operational_report

_MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
_EVIDENCE_ADAPTER: TypeAdapter[OperationalEvidence] = TypeAdapter(OperationalEvidence)
_DEFAULT_REQUIRED_SERVICES = (
    "api",
    "edge",
    "ingestor",
    "postgres",
    "projector",
    "retrieval-indexer",
    "valkey",
    "web",
)
_DEFAULT_COMPOSE_FILES = ("compose.yaml", "deploy/free-tier/compose.yaml")


def _read_regular(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError(f"cannot open JSON artifact {path}: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"JSON artifact {path} must be a regular file")
        if before.st_size > _MAX_ARTIFACT_BYTES:
            raise ValueError(f"JSON artifact {path} exceeds the 5 MiB limit")
        chunks: list[bytes] = []
        remaining = _MAX_ARTIFACT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining == 0:
            raise ValueError(f"JSON artifact {path} exceeds the 5 MiB limit")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError(f"JSON artifact {path} changed while it was read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    return model.model_validate_json(_read_regular(path))


def _evidence(path: Path) -> OperationalEvidence:
    return _EVIDENCE_ADAPTER.validate_json(_read_regular(path))


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("timestamp must be an ISO-8601 UTC value") from error
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise argparse.ArgumentTypeError("timestamp must be timezone-aware UTC")
    return parsed.astimezone(UTC)


def _positive_timeout(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be a number from 0 to 60") from error
    if not 0 < parsed <= 60:
        raise argparse.ArgumentTypeError("timeout must be greater than zero and at most 60")
    return parsed


def _ensure_writable(paths: Sequence[Path], *, inputs: Sequence[Path], force: bool) -> None:
    resolved = {path.resolve() for path in paths}
    if len(resolved) != len(paths):
        raise ValueError("output paths must be distinct")
    if resolved & {path.resolve() for path in inputs}:
        raise ValueError("outputs cannot replace input evidence")
    existing = [path for path in paths if path.exists()]
    if existing and not force:
        raise FileExistsError(
            "refusing to overwrite " + ", ".join(str(path) for path in existing) + "; pass --force"
        )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_model(path: Path, value: BaseModel) -> None:
    _write(path, value.model_dump_json(indent=2) + "\n")


def _target(args: argparse.Namespace) -> int:
    _ensure_writable((args.output,), inputs=(), force=args.force)
    target = build_deployment_target(
        origin=args.origin,
        commit_sha=args.commit_sha,
        application_version=args.application_version,
        environment=args.environment,
        deployed_at=args.deployed_at,
        required_sources=tuple(sorted(args.required_source or ("gdelt", "usgs"))),
        required_services=tuple(sorted(args.required_service or _DEFAULT_REQUIRED_SERVICES)),
        compose_files=tuple(sorted(args.compose_file or _DEFAULT_COMPOSE_FILES)),
    )
    _write_model(args.output, target)
    print(
        f"Wrote deployment target {target.target_id} for {target.commit_sha}; "
        "agent execution remains disabled"
    )
    return 0


def _probe(args: argparse.Namespace) -> int:
    _ensure_writable((args.output,), inputs=(args.target,), force=args.force)
    target = _model(args.target, DeploymentTarget)
    probe = capture_deployment_probe(
        target,
        observed_at=args.observed_at,
        timeout_seconds=args.timeout,
    )
    _write_model(args.output, probe)
    print(
        f"Wrote {'passing' if probe.passed else 'failing'} public probe {probe.evidence_id}; "
        "no model or agent was invoked"
    )
    return 0 if probe.passed else 1


def _resource(args: argparse.Namespace) -> int:
    inputs = (args.target, args.submission)
    _ensure_writable((args.output,), inputs=inputs, force=args.force)
    target = _model(args.target, DeploymentTarget)
    submission = _model(args.submission, ResourceSnapshotSubmission)
    evidence = build_resource_snapshot(target, submission)
    _write_model(args.output, evidence)
    print(f"Wrote resource snapshot {evidence.evidence_id}; capacity remains unclaimed")
    return 0 if evidence.passed else 1


def _capture_resource(args: argparse.Namespace) -> int:
    _ensure_writable((args.output,), inputs=(args.target,), force=args.force)
    target = _model(args.target, DeploymentTarget)
    evidence = capture_resource_snapshot(
        target,
        args.project_dir,
        observed_at=args.observed_at,
    )
    _write_model(args.output, evidence)
    print(
        f"Wrote {'passing' if evidence.passed else 'failing'} collected resource snapshot "
        f"{evidence.evidence_id}; logs and environment were not read"
    )
    return 0 if evidence.passed else 1


def _restart(args: argparse.Namespace) -> int:
    inputs = (args.target, args.before_probe, args.after_probe)
    _ensure_writable((args.output,), inputs=inputs, force=args.force)
    target = _model(args.target, DeploymentTarget)
    before = _model(args.before_probe, DeploymentProbeEvidence)
    after = _model(args.after_probe, DeploymentProbeEvidence)
    evidence = build_restart_recovery(
        target,
        before,
        after,
        trigger=cast(RestartTrigger, args.trigger),
        services=tuple(sorted(args.service)),
        restart_started_at=args.restart_started_at,
    )
    _write_model(args.output, evidence)
    print(
        f"Wrote {'passing' if evidence.passed else 'failing'} restart evidence "
        f"{evidence.evidence_id}; this command did not restart a service"
    )
    return 0 if evidence.passed else 1


def _backup(args: argparse.Namespace) -> int:
    inputs = (args.target, args.backup_file)
    _ensure_writable((args.output,), inputs=inputs, force=args.force)
    target = _model(args.target, DeploymentTarget)
    evidence = build_backup_evidence(
        target,
        args.backup_file,
        storage_scope=args.storage_scope,
        encrypted_at_rest=args.encrypted_at_rest,
        observed_at=args.observed_at,
    )
    _write_model(args.output, evidence)
    print(f"Wrote backup digest evidence {evidence.evidence_id}; the backup file was not modified")
    return 0


def _restore(args: argparse.Namespace) -> int:
    inputs = (args.target, args.backup_evidence, args.submission)
    _ensure_writable((args.output,), inputs=inputs, force=args.force)
    target = _model(args.target, DeploymentTarget)
    backup = _model(args.backup_evidence, BackupEvidence)
    submission = _model(args.submission, RestoreDrillSubmission)
    evidence = build_restore_drill(target, backup, submission)
    _write_model(args.output, evidence)
    print(
        f"Wrote {'passing' if evidence.passed else 'failing'} isolated restore evidence "
        f"{evidence.evidence_id}"
    )
    return 0 if evidence.passed else 1


def _evidence_paths(args: argparse.Namespace) -> tuple[Path, ...]:
    paths = list(args.evidence or ())
    if args.evidence_dir is not None:
        if not args.evidence_dir.is_dir():
            raise ValueError("evidence directory does not exist or is not a directory")
        paths.extend(sorted(args.evidence_dir.glob("*.evidence.json")))
    unique = {path.resolve(): path for path in paths}
    if len(unique) != len(paths):
        raise ValueError("evidence inputs must be unique")
    return tuple(unique[key] for key in sorted(unique, key=str))


def _report(args: argparse.Namespace) -> int:
    evidence_paths = _evidence_paths(args)
    inputs = (args.target, *evidence_paths)
    outputs = (args.output_json, args.output_markdown)
    _ensure_writable(outputs, inputs=inputs, force=args.force)
    target = _model(args.target, DeploymentTarget)
    evidence = tuple(_evidence(path) for path in evidence_paths)
    report = evaluate_operational_campaign(
        target,
        evidence,
        generated_at=args.generated_at,
    )
    _write_model(args.output_json, report)
    _write(args.output_markdown, render_operational_report(target, report))
    print(f"Wrote {report.status} report {report.report_id}; sampled evidence is not an SLA")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-pulse-operations",
        description=(
            "Capture content-addressed public deployment observations and evaluate a conservative "
            "30-day minimum evidence set without enabling agent execution."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    target = commands.add_parser("target", help="create an exact public deployment target")
    target.add_argument("--origin", required=True)
    target.add_argument("--commit-sha", required=True)
    target.add_argument("--application-version", required=True)
    target.add_argument("--environment", default="free-tier-public")
    target.add_argument("--deployed-at", type=_utc, required=True)
    target.add_argument(
        "--required-source",
        action="append",
        choices=("usgs", "nws", "firms", "gdelt"),
    )
    target.add_argument("--required-service", action="append")
    target.add_argument("--compose-file", action="append")
    target.add_argument("--output", type=Path, required=True)
    target.add_argument("--force", action="store_true")

    probe = commands.add_parser("probe", help="capture the fixed HTTPS and default-deny checks")
    probe.add_argument("--target", type=Path, required=True)
    probe.add_argument("--observed-at", type=_utc)
    probe.add_argument("--timeout", type=_positive_timeout, default=10.0)
    probe.add_argument("--output", type=Path, required=True)
    probe.add_argument("--force", action="store_true")

    resource = commands.add_parser(
        "record-resource", help="bind a strict host/container snapshot to the target"
    )
    resource.add_argument("--target", type=Path, required=True)
    resource.add_argument("--submission", type=Path, required=True)
    resource.add_argument("--output", type=Path, required=True)
    resource.add_argument("--force", action="store_true")

    capture_resource = commands.add_parser(
        "capture-resource", help="collect bounded Docker/host resource values for the target"
    )
    capture_resource.add_argument("--target", type=Path, required=True)
    capture_resource.add_argument("--project-dir", type=Path, required=True)
    capture_resource.add_argument("--observed-at", type=_utc)
    capture_resource.add_argument("--output", type=Path, required=True)
    capture_resource.add_argument("--force", action="store_true")

    restart = commands.add_parser(
        "record-restart", help="bind exact before/after probes to an operator-run restart"
    )
    restart.add_argument("--target", type=Path, required=True)
    restart.add_argument("--before-probe", type=Path, required=True)
    restart.add_argument("--after-probe", type=Path, required=True)
    restart.add_argument("--restart-started-at", type=_utc, required=True)
    restart.add_argument(
        "--trigger",
        choices=("planned_host_reboot", "planned_stack_restart", "failure_recovery"),
        required=True,
    )
    restart.add_argument("--service", action="append", required=True)
    restart.add_argument("--output", type=Path, required=True)
    restart.add_argument("--force", action="store_true")

    backup = commands.add_parser(
        "record-backup", help="hash one stable PostgreSQL custom-format backup"
    )
    backup.add_argument("--target", type=Path, required=True)
    backup.add_argument("--backup-file", type=Path, required=True)
    backup.add_argument(
        "--storage-scope", choices=("local_only", "off_host_verified"), required=True
    )
    backup.add_argument("--encrypted-at-rest", action="store_true")
    backup.add_argument("--observed-at", type=_utc)
    backup.add_argument("--output", type=Path, required=True)
    backup.add_argument("--force", action="store_true")

    restore = commands.add_parser(
        "record-restore", help="bind isolated restore checks to one exact backup digest"
    )
    restore.add_argument("--target", type=Path, required=True)
    restore.add_argument("--backup-evidence", type=Path, required=True)
    restore.add_argument("--submission", type=Path, required=True)
    restore.add_argument("--output", type=Path, required=True)
    restore.add_argument("--force", action="store_true")

    report = commands.add_parser(
        "report", help="cross-check observations and render the sampled 30-day evidence report"
    )
    report.add_argument("--target", type=Path, required=True)
    report.add_argument("--evidence", type=Path, action="append")
    report.add_argument("--evidence-dir", type=Path)
    report.add_argument("--generated-at", type=_utc)
    report.add_argument("--output-json", type=Path, required=True)
    report.add_argument("--output-markdown", type=Path, required=True)
    report.add_argument("--force", action="store_true")
    return parser


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Run one operations command and preserve failure probes as evidence."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "target":
            return _target(args)
        if args.command == "probe":
            return _probe(args)
        if args.command == "record-resource":
            return _resource(args)
        if args.command == "capture-resource":
            return _capture_resource(args)
        if args.command == "record-restart":
            return _restart(args)
        if args.command == "record-backup":
            return _backup(args)
        if args.command == "record-restore":
            return _restore(args)
        return _report(args)
    except (FileExistsError, OSError, RuntimeError, ValueError, ValidationError) as error:
        print(f"operational evidence command failed: {error}", file=sys.stderr)
        return 2


def main() -> None:
    """Installed operational-evidence entry point."""
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()

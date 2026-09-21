"""Capture one logged, exact-target operational-evidence sample."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
Runner = Callable[[tuple[str, ...], Path], subprocess.CompletedProcess[str]]


def _run(arguments: tuple[str, ...], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=20 * 60,
    )


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("observed time must be ISO-8601 UTC") from error
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise argparse.ArgumentTypeError("observed time must use UTC")
    return parsed.astimezone(UTC)


def _path(project_dir: Path, value: Path) -> Path:
    return value if value.is_absolute() else project_dir / value


def _target_commit(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read deployment target {path}: {error}") from error
    commit = value.get("commit_sha") if isinstance(value, dict) else None
    if not isinstance(commit, str) or COMMIT_SHA.fullmatch(commit) is None:
        raise ValueError("deployment target does not contain one full lowercase commit SHA")
    return commit


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def _emit(log_path: Path, message: str) -> None:
    print(message, flush=True)
    _append_log(log_path, message)


def _invoke(
    arguments: tuple[str, ...],
    *,
    project_dir: Path,
    log_path: Path,
    runner: Runner,
) -> tuple[int, subprocess.CompletedProcess[str] | None]:
    _emit(log_path, f"$ {shlex.join(arguments)}")
    try:
        result = runner(arguments, project_dir)
    except (OSError, subprocess.SubprocessError) as error:
        _emit(log_path, f"command execution failed: {error}")
        return 2, None
    for output in (result.stdout, result.stderr):
        if output.strip():
            _emit(log_path, output.rstrip())
    _emit(log_path, f"command exit code: {result.returncode}")
    if result.returncode == 0:
        return 0, result
    return (1 if result.returncode == 1 else 2), result


def collect_daily_evidence(
    *,
    project_dir: Path,
    target_path: Path,
    evidence_dir: Path,
    report_json: Path,
    report_markdown: Path,
    log_path: Path,
    observed_at: datetime,
    uv_executable: str,
    runner: Runner = _run,
) -> int:
    """Capture aligned probe/resource evidence and refresh the campaign report."""

    project_dir = project_dir.resolve()
    target_path = _path(project_dir, target_path)
    evidence_dir = _path(project_dir, evidence_dir)
    report_json = _path(project_dir, report_json)
    report_markdown = _path(project_dir, report_markdown)
    log_path = _path(project_dir, log_path)
    timestamp = observed_at.astimezone(UTC).replace(microsecond=0)
    observed = timestamp.isoformat().replace("+00:00", "Z")
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")

    _emit(log_path, f"=== AtlasPulse daily evidence started {observed} ===")
    try:
        expected_commit = _target_commit(target_path)
    except ValueError as error:
        _emit(log_path, f"configuration error: {error}")
        return 2

    revision_status, revision_result = _invoke(
        ("git", "rev-parse", "--verify", "HEAD"),
        project_dir=project_dir,
        log_path=log_path,
        runner=runner,
    )
    if revision_status != 0 or revision_result is None:
        return 2
    actual_commit = revision_result.stdout.strip()
    if COMMIT_SHA.fullmatch(actual_commit) is None:
        _emit(log_path, "configuration error: Git HEAD could not be resolved to a full commit")
        return 2
    if actual_commit != expected_commit:
        _emit(
            log_path,
            "configuration error: deployment target commit "
            f"{expected_commit} does not match checkout {actual_commit}",
        )
        return 2

    status_code, status_result = _invoke(
        ("git", "status", "--porcelain", "--untracked-files=no"),
        project_dir=project_dir,
        log_path=log_path,
        runner=runner,
    )
    if status_code != 0 or status_result is None:
        return 2
    if status_result.stdout.strip():
        _emit(log_path, "configuration error: tracked checkout files differ from the target commit")
        return 2

    evidence_dir.mkdir(parents=True, exist_ok=True)
    base = (uv_executable, "run", "--frozen", "--offline", "atlas-pulse-operations")
    probe_path = evidence_dir / f"{stamp}-probe.evidence.json"
    resource_path = evidence_dir / f"{stamp}-resource.evidence.json"
    probe_status, _ = _invoke(
        (
            *base,
            "probe",
            "--target",
            str(target_path),
            "--observed-at",
            observed,
            "--output",
            str(probe_path),
        ),
        project_dir=project_dir,
        log_path=log_path,
        runner=runner,
    )
    resource_status, _ = _invoke(
        (
            *base,
            "capture-resource",
            "--target",
            str(target_path),
            "--project-dir",
            str(project_dir),
            "--observed-at",
            observed,
            "--output",
            str(resource_path),
        ),
        project_dir=project_dir,
        log_path=log_path,
        runner=runner,
    )
    report_status, _ = _invoke(
        (
            *base,
            "report",
            "--target",
            str(target_path),
            "--evidence-dir",
            str(evidence_dir),
            "--generated-at",
            observed,
            "--output-json",
            str(report_json),
            "--output-markdown",
            str(report_markdown),
            "--force",
        ),
        project_dir=project_dir,
        log_path=log_path,
        runner=runner,
    )
    statuses = (probe_status, resource_status, report_status)
    result = 2 if 2 in statuses else 1 if 1 in statuses else 0
    _emit(log_path, f"=== AtlasPulse daily evidence finished with exit code {result} ===")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture one aligned, logged operational-evidence sample for the exact deployment "
            "target. Exit 0 means both observations passed; 1 means failing evidence was saved; "
            "2 means configuration or execution failed."
        )
    )
    parser.add_argument("--project-dir", type=Path, default=ROOT)
    parser.add_argument("--target", type=Path, default=Path("artifacts/operations/target.json"))
    parser.add_argument("--evidence-dir", type=Path, default=Path("artifacts/operations/evidence"))
    parser.add_argument(
        "--report-json", type=Path, default=Path("artifacts/operations/report.json")
    )
    parser.add_argument(
        "--report-markdown", type=Path, default=Path("artifacts/operations/report.md")
    )
    parser.add_argument("--log", type=Path, default=Path("artifacts/operations/daily-evidence.log"))
    parser.add_argument("--observed-at", type=_utc)
    parser.add_argument("--uv", default=str(Path.home() / ".local" / "bin" / "uv"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    observed_at = args.observed_at or datetime.now(UTC)
    try:
        return collect_daily_evidence(
            project_dir=args.project_dir,
            target_path=args.target,
            evidence_dir=args.evidence_dir,
            report_json=args.report_json,
            report_markdown=args.report_markdown,
            log_path=args.log,
            observed_at=observed_at,
            uv_executable=args.uv,
        )
    except (OSError, ValueError) as error:
        print(f"daily evidence collector failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

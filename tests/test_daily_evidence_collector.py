"""Tests for the exact-target daily operational-evidence collector."""

from __future__ import annotations

import json
import runpy
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

MODULE = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "collect_daily_evidence.py")
)
collect_daily_evidence = cast(Callable[..., int], MODULE["collect_daily_evidence"])
target_commit = cast(Callable[[Path], str], MODULE["_target_commit"])

COMMIT = "a" * 40
OBSERVED = datetime(2026, 9, 21, 4, 30, tzinfo=UTC)


def _target(path: Path, *, commit: str = COMMIT) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"commit_sha": commit}), encoding="utf-8")


def _collect(tmp_path: Path, runner: Callable[..., subprocess.CompletedProcess[str]]) -> int:
    return collect_daily_evidence(
        project_dir=tmp_path,
        target_path=Path("artifacts/operations/target.json"),
        evidence_dir=Path("artifacts/operations/evidence"),
        report_json=Path("artifacts/operations/report.json"),
        report_markdown=Path("artifacts/operations/report.md"),
        log_path=Path("artifacts/operations/daily-evidence.log"),
        observed_at=OBSERVED,
        uv_executable="/test/uv",
        runner=runner,
    )


def test_daily_collector_runs_aligned_probe_resource_and_report(tmp_path: Path) -> None:
    target = tmp_path / "artifacts/operations/target.json"
    _target(target)
    calls: list[tuple[str, ...]] = []

    def runner(arguments: tuple[str, ...], _cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if arguments[:2] == ("git", "rev-parse"):
            return subprocess.CompletedProcess(arguments, 0, stdout=f"{COMMIT}\n", stderr="")
        if arguments[:2] == ("git", "status"):
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(arguments, 0, stdout="completed\n", stderr="")

    assert _collect(tmp_path, runner) == 0

    operation_calls = [call for call in calls if "atlas-pulse-operations" in call]
    assert [call[5] for call in operation_calls] == ["probe", "capture-resource", "report"]
    for call in operation_calls[:2]:
        assert call[call.index("--observed-at") + 1] == "2026-09-21T04:30:00Z"
    assert "--force" in operation_calls[-1]
    log = tmp_path / "artifacts/operations/daily-evidence.log"
    assert "finished with exit code 0" in log.read_text(encoding="utf-8")
    assert log.stat().st_mode & 0o777 == 0o600


def test_daily_collector_rejects_a_stale_target_before_capture(tmp_path: Path) -> None:
    _target(tmp_path / "artifacts/operations/target.json", commit="b" * 40)
    calls: list[tuple[str, ...]] = []

    def runner(arguments: tuple[str, ...], _cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout=f"{COMMIT}\n", stderr="")

    assert _collect(tmp_path, runner) == 2
    assert not any("atlas-pulse-operations" in call for call in calls)
    log = (tmp_path / "artifacts/operations/daily-evidence.log").read_text(encoding="utf-8")
    assert "does not match checkout" in log


def test_daily_collector_preserves_both_samples_when_probe_fails(tmp_path: Path) -> None:
    _target(tmp_path / "artifacts/operations/target.json")
    operations: list[str] = []

    def runner(arguments: tuple[str, ...], _cwd: Path) -> subprocess.CompletedProcess[str]:
        if arguments[:2] == ("git", "rev-parse"):
            return subprocess.CompletedProcess(arguments, 0, stdout=f"{COMMIT}\n", stderr="")
        if "atlas-pulse-operations" not in arguments:
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")
        command = arguments[5]
        operations.append(command)
        return subprocess.CompletedProcess(
            arguments,
            1 if command == "probe" else 0,
            stdout=f"{command} completed\n",
            stderr="",
        )

    assert _collect(tmp_path, runner) == 1
    assert operations == ["probe", "capture-resource", "report"]


@pytest.mark.parametrize("content", ["not-json", "{}", '{"commit_sha":"ABC"}'])
def test_target_commit_rejects_invalid_json_or_identity(tmp_path: Path, content: str) -> None:
    target = tmp_path / "target.json"
    target.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        target_commit(target)

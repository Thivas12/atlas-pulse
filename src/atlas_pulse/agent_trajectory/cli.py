"""CLI for offline agent trajectory scoring, drift, and release assessment."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ValidationError

from atlas_pulse.agent_trajectory.base import (
    AgentTrajectoryBatch,
    AgentTrajectorySubmission,
    apply_agent_trajectory_submission,
    build_agent_trajectory_submission,
)
from atlas_pulse.agent_trajectory.drift import (
    AgentTrajectoryDriftReport,
    compare_agent_trajectory_reports,
)
from atlas_pulse.agent_trajectory.release import (
    AgentReleaseThresholdPolicy,
    build_default_agent_release_policy,
    evaluate_agent_release,
)
from atlas_pulse.agent_trajectory.report import (
    render_agent_release_markdown,
    render_agent_trajectory_drift_markdown,
    render_agent_trajectory_markdown,
)
from atlas_pulse.agent_trajectory.scoring import (
    AgentTrajectoryScoreReport,
    score_agent_trajectory,
)
from atlas_pulse.grounded_answer_evaluation import (
    GroundedAnswerCandidateBatch,
    GroundedAnswerEvaluationReport,
    GroundedAnswerTask,
)
from atlas_pulse.relationship_evaluation import RelationshipCandidateComparisonReport


def _json_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _ensure_writable(paths: Sequence[Path], *, inputs: Sequence[Path], force: bool) -> None:
    resolved_outputs = {path.resolve() for path in paths}
    if len(resolved_outputs) != len(paths):
        raise ValueError("output paths must be distinct")
    if resolved_outputs & {path.resolve() for path in inputs}:
        raise ValueError("output paths must not replace input artifacts")
    existing = [path for path in paths if path.exists()]
    if existing and not force:
        targets = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"refusing to overwrite {targets}; pass --force intentionally")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _policy(args: argparse.Namespace) -> int:
    _ensure_writable((args.output,), inputs=(), force=args.force)
    policy = build_default_agent_release_policy()
    _write(args.output, policy.model_dump_json(indent=2) + "\n")
    print(f"Wrote default-deny release policy {policy.policy_id}; execution remains disabled")
    return 0


def _template(args: argparse.Namespace) -> int:
    _ensure_writable((args.output,), inputs=(args.task, args.candidate_batch), force=args.force)
    task = _json_model(args.task, GroundedAnswerTask)
    batch = _json_model(args.candidate_batch, GroundedAnswerCandidateBatch)
    submission = build_agent_trajectory_submission(task, batch)
    _write(args.output, submission.model_dump_json(indent=2) + "\n")
    print(
        f"Wrote protected trajectory template for {len(submission.cases)} case(s); "
        "record only observable steps and capability use"
    )
    return 0


def _import(args: argparse.Namespace) -> int:
    inputs = (args.task, args.candidate_batch, args.submission)
    _ensure_writable((args.output,), inputs=inputs, force=args.force)
    task = _json_model(args.task, GroundedAnswerTask)
    candidate = _json_model(args.candidate_batch, GroundedAnswerCandidateBatch)
    submission = _json_model(args.submission, AgentTrajectorySubmission)
    batch = apply_agent_trajectory_submission(task, candidate, submission)
    _write(args.output, batch.model_dump_json(indent=2) + "\n")
    print(
        f"Imported blocked {batch.trajectory_batch_id}; no model, tool, or side effect was "
        "invoked by this command"
    )
    return 0


def _score(args: argparse.Namespace) -> int:
    inputs = (
        args.task,
        args.candidate_batch,
        args.grounded_report,
        args.trajectory_batch,
    )
    outputs = (args.output_json, args.output_markdown)
    _ensure_writable(outputs, inputs=inputs, force=args.force)
    task = _json_model(args.task, GroundedAnswerTask)
    candidate = _json_model(args.candidate_batch, GroundedAnswerCandidateBatch)
    grounded = _json_model(args.grounded_report, GroundedAnswerEvaluationReport)
    trajectory = _json_model(args.trajectory_batch, AgentTrajectoryBatch)
    report = score_agent_trajectory(task, candidate, grounded, trajectory)
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_agent_trajectory_markdown(report))
    print(f"Wrote blocked observable trajectory report {report.report_id}")
    return 0


def _drift(args: argparse.Namespace) -> int:
    inputs = (args.baseline, args.current, args.policy)
    outputs = (args.output_json, args.output_markdown)
    _ensure_writable(outputs, inputs=inputs, force=args.force)
    baseline = _json_model(args.baseline, AgentTrajectoryScoreReport)
    current = _json_model(args.current, AgentTrajectoryScoreReport)
    policy = _json_model(args.policy, AgentReleaseThresholdPolicy)
    report = compare_agent_trajectory_reports(
        baseline,
        current,
        thresholds=policy.drift_thresholds,
    )
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_agent_trajectory_drift_markdown(report))
    print(f"Wrote {report.status} trajectory drift report {report.drift_report_id}")
    return 0


def _release(args: argparse.Namespace) -> int:
    trajectory_paths = tuple(args.trajectory_report)
    grounded_paths = tuple(args.grounded_report)
    drift_paths = tuple(args.drift_report)
    inputs = (
        args.policy,
        args.relationship_report,
        *grounded_paths,
        *trajectory_paths,
        *drift_paths,
    )
    outputs = (args.output_json, args.output_markdown)
    _ensure_writable(outputs, inputs=inputs, force=args.force)
    policy = _json_model(args.policy, AgentReleaseThresholdPolicy)
    relationship = _json_model(args.relationship_report, RelationshipCandidateComparisonReport)
    grounded = tuple(_json_model(path, GroundedAnswerEvaluationReport) for path in grounded_paths)
    trajectories = tuple(_json_model(path, AgentTrajectoryScoreReport) for path in trajectory_paths)
    drifts = tuple(_json_model(path, AgentTrajectoryDriftReport) for path in drift_paths)
    assessment = evaluate_agent_release(
        policy,
        relationship,
        grounded,
        trajectories,
        drifts,
    )
    _write(args.output_json, assessment.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_agent_release_markdown(assessment))
    print(
        f"Wrote {assessment.status} release assessment {assessment.assessment_id}; "
        "execution remains hard-disabled"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-pulse-evaluate-agent-trajectories",
        description=(
            "Score observable offline agent traces, detect drift, and evaluate conservative "
            "human-review thresholds without enabling execution."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    policy = commands.add_parser("write-policy", help="write the versioned default thresholds")
    policy.add_argument("--output", type=Path, required=True)
    policy.add_argument("--force", action="store_true")

    template = commands.add_parser(
        "template", help="create a trace-empty template bound to one grounded candidate batch"
    )
    template.add_argument("--task", type=Path, required=True)
    template.add_argument("--candidate-batch", type=Path, required=True)
    template.add_argument("--output", type=Path, required=True)
    template.add_argument("--force", action="store_true")

    imported = commands.add_parser(
        "import", help="validate completed observable traces and content-address them"
    )
    imported.add_argument("--task", type=Path, required=True)
    imported.add_argument("--candidate-batch", type=Path, required=True)
    imported.add_argument("--submission", type=Path, required=True)
    imported.add_argument("--output", type=Path, required=True)
    imported.add_argument("--force", action="store_true")

    score = commands.add_parser(
        "score", help="join trajectories to an adjudicated grounded-answer report"
    )
    score.add_argument("--task", type=Path, required=True)
    score.add_argument("--candidate-batch", type=Path, required=True)
    score.add_argument("--grounded-report", type=Path, required=True)
    score.add_argument("--trajectory-batch", type=Path, required=True)
    score.add_argument("--output-json", type=Path, required=True)
    score.add_argument("--output-markdown", type=Path, required=True)
    score.add_argument("--force", action="store_true")

    drift = commands.add_parser(
        "drift", help="compare two chronological score reports under policy drift limits"
    )
    drift.add_argument("--baseline", type=Path, required=True)
    drift.add_argument("--current", type=Path, required=True)
    drift.add_argument("--policy", type=Path, required=True)
    drift.add_argument("--output-json", type=Path, required=True)
    drift.add_argument("--output-markdown", type=Path, required=True)
    drift.add_argument("--force", action="store_true")

    release = commands.add_parser(
        "release", help="evaluate exact relationship, grounded, trajectory, and drift evidence"
    )
    release.add_argument("--policy", type=Path, required=True)
    release.add_argument("--relationship-report", type=Path, required=True)
    release.add_argument("--grounded-report", type=Path, action="append", required=True)
    release.add_argument("--trajectory-report", type=Path, action="append", required=True)
    release.add_argument("--drift-report", type=Path, action="append", default=[])
    release.add_argument("--output-json", type=Path, required=True)
    release.add_argument("--output-markdown", type=Path, required=True)
    release.add_argument("--force", action="store_true")
    return parser


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Run one trajectory command with a clear non-zero failure status."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "write-policy":
            return _policy(args)
        if args.command == "template":
            return _template(args)
        if args.command == "import":
            return _import(args)
        if args.command == "score":
            return _score(args)
        if args.command == "drift":
            return _drift(args)
        return _release(args)
    except (FileExistsError, OSError, RuntimeError, ValueError, ValidationError) as error:
        print(f"agent trajectory evaluation failed: {error}", file=sys.stderr)
        return 2


def main() -> None:
    """Installed agent-trajectory evaluation entry point."""
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()

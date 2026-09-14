"""Provision and run the separate local relationship NLI candidate."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from huggingface_hub import snapshot_download
from pydantic import BaseModel, ValidationError

from atlas_pulse.relationship_evaluation.candidates import (
    CandidateEvaluationTask,
    build_candidate_prediction_sheet,
)
from atlas_pulse.relationship_nli import (
    NliCandidateConfig,
    NliRuntime,
    OnnxNliRuntime,
    render_candidate_prediction_csv,
    run_nli_candidate,
    validate_nli_model_artifacts,
)

RuntimeFactory = Callable[[NliCandidateConfig, Path], NliRuntime]
SnapshotDownloader = Callable[..., str]


def _json_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _ensure_writable(paths: Sequence[Path], *, inputs: Sequence[Path], force: bool) -> None:
    resolved_outputs = {path.resolve() for path in paths}
    if len(resolved_outputs) != len(paths):
        raise ValueError("output paths must be distinct")
    protected_inputs = {path.resolve() for path in inputs}
    if resolved_outputs & protected_inputs:
        raise ValueError("output paths must not replace input artifacts")
    existing = [path for path in paths if path.exists()]
    if existing and not force:
        targets = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"refusing to overwrite {targets}; pass --force intentionally")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-pulse-run-relationship-nli",
        description="Run a pinned local NLI model over a gold-blind relationship candidate task.",
    )
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--predictions-template", type=Path, required=True)
    parser.add_argument("--candidate-config", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-predictions", type=Path, required=True)
    parser.add_argument("--output-definition", type=Path, required=True)
    parser.add_argument("--output-run", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    return parser


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: RuntimeFactory = OnnxNliRuntime,
) -> int:
    """Execute a candidate and convert contract/runtime failures to exit code 2."""
    parser = _run_parser()
    args = parser.parse_args(argv)
    inputs = (args.task, args.predictions_template, args.candidate_config)
    outputs = (args.output_predictions, args.output_definition, args.output_run)
    try:
        _ensure_writable(outputs, inputs=inputs, force=args.force)
        task = _json_model(args.task, CandidateEvaluationTask)
        config = _json_model(args.candidate_config, NliCandidateConfig)
        expected_template = build_candidate_prediction_sheet(task).content
        supplied_template = args.predictions_template.read_text(encoding="utf-8")
        if supplied_template != expected_template:
            raise ValueError(
                "predictions template must be the exact blank sheet emitted for this task"
            )
        runtime = runtime_factory(config, args.model_dir)
        run = run_nli_candidate(task, config, runtime)
        predictions = render_candidate_prediction_csv(task, run)
        _write(args.output_predictions, predictions)
        _write(args.output_definition, run.system.model_dump_json(indent=2) + "\n")
        _write(args.output_run, run.model_dump_json(indent=2) + "\n")
        print(
            f"Wrote blocked {run.run_id}: {run.case_count} case(s), "
            f"{run.decision_counts['insufficient_evidence']} abstention(s)"
        )
        return 0
    except (
        FileExistsError,
        ImportError,
        OSError,
        RuntimeError,
        ValueError,
        ValidationError,
    ) as error:
        print(f"relationship NLI candidate failed: {error}", file=sys.stderr)
        return 2


def _cache_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-pulse-cache-relationship-nli",
        description="Download only the pinned files required by a relationship NLI candidate.",
    )
    parser.add_argument("--candidate-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def cache_cli(
    argv: Sequence[str] | None = None,
    *,
    downloader: SnapshotDownloader = snapshot_download,
) -> int:
    """Provision a revision-pinned snapshot and verify its exact inference bytes."""
    parser = _cache_parser()
    args = parser.parse_args(argv)
    try:
        config = _json_model(args.candidate_config, NliCandidateConfig)
        args.output.mkdir(parents=True, exist_ok=True)
        downloader(
            repo_id=config.model_id,
            revision=config.model_revision,
            allow_patterns=list(config.artifact_files),
            local_dir=str(args.output),
        )
        artifact_hash = validate_nli_model_artifacts(config, args.output)
        print(
            f"Cached {config.model_id}@{config.model_revision} as {artifact_hash} in {args.output}"
        )
        return 0
    except (OSError, RuntimeError, ValueError, ValidationError) as error:
        print(f"relationship NLI cache failed: {error}", file=sys.stderr)
        return 2


def main() -> None:
    """Installed offline candidate runner entry point."""
    raise SystemExit(run_cli())


def cache_main() -> None:
    """Installed pinned snapshot provisioning entry point."""
    raise SystemExit(cache_cli())


if __name__ == "__main__":
    main()

"""Provision and execute the separate pinned local grounded-answer candidate."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path

import httpx
from huggingface_hub import hf_hub_download
from pydantic import BaseModel, ValidationError

from atlas_pulse.grounded_answer_evaluation import GroundedAnswerTask
from atlas_pulse.grounded_answer_runner import (
    GroundedAnswerRunnerConfig,
    GroundedAnswerRuntime,
    LlamaServerRuntime,
    run_grounded_answer_candidate,
    validate_grounded_answer_model,
)

RuntimeFactory = Callable[
    [GroundedAnswerRunnerConfig, Path, Path],
    AbstractContextManager[GroundedAnswerRuntime],
]
ModelDownloader = Callable[..., str]


def _json_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _ensure_writable(paths: Sequence[Path], *, inputs: Sequence[Path], force: bool) -> None:
    resolved = {path.resolve() for path in paths}
    if len(resolved) != len(paths):
        raise ValueError("output paths must be distinct")
    if resolved & {path.resolve() for path in inputs}:
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
        prog="atlas-pulse-run-grounded-answer",
        description=(
            "Run one pinned local llama.cpp candidate over a gold-free grounded-answer task."
        ),
    )
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--candidate-config", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--llama-server", type=Path, required=True)
    parser.add_argument("--output-submission", type=Path, required=True)
    parser.add_argument("--output-definition", type=Path, required=True)
    parser.add_argument("--output-run", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    return parser


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: RuntimeFactory = LlamaServerRuntime,
) -> int:
    """Execute a gold-free candidate and convert all contract failures to exit code 2."""
    args = _run_parser().parse_args(argv)
    inputs = (args.task, args.candidate_config, args.model_dir, args.llama_server)
    outputs = (args.output_submission, args.output_definition, args.output_run)
    try:
        _ensure_writable(outputs, inputs=inputs, force=args.force)
        task = _json_model(args.task, GroundedAnswerTask)
        config = _json_model(args.candidate_config, GroundedAnswerRunnerConfig)
        with runtime_factory(config, args.model_dir, args.llama_server) as runtime:
            submission, run, _batch = run_grounded_answer_candidate(task, config, runtime)
        _write(args.output_submission, submission.model_dump_json(indent=2) + "\n")
        _write(args.output_definition, run.system.model_dump_json(indent=2) + "\n")
        _write(args.output_run, run.model_dump_json(indent=2) + "\n")
        print(
            f"Wrote blocked {run.run_id}: {run.case_count} case(s), "
            f"{run.response_counts['abstained']} abstention(s)"
        )
        return 0
    except (
        FileExistsError,
        httpx.HTTPError,
        OSError,
        RuntimeError,
        TimeoutError,
        ValueError,
        ValidationError,
    ) as error:
        print(f"grounded-answer candidate failed: {error}", file=sys.stderr)
        return 2


def _cache_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-pulse-cache-grounded-answer-model",
        description="Download only the exact revision-pinned GGUF required by a candidate.",
    )
    parser.add_argument("--candidate-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def cache_cli(
    argv: Sequence[str] | None = None,
    *,
    downloader: ModelDownloader = hf_hub_download,
) -> int:
    """Provision one exact model file and verify its declared upstream SHA-256."""
    args = _cache_parser().parse_args(argv)
    try:
        config = _json_model(args.candidate_config, GroundedAnswerRunnerConfig)
        args.output.mkdir(parents=True, exist_ok=True)
        downloaded = Path(
            downloader(
                repo_id=config.model_id,
                filename=config.model_file,
                revision=config.model_revision,
                local_dir=str(args.output),
            )
        ).resolve(strict=True)
        expected = (args.output / config.model_file).resolve(strict=True)
        if downloaded != expected:
            raise ValueError("model downloader returned a path outside the exact output artifact")
        model_path = validate_grounded_answer_model(config, args.output)
        print(
            f"Cached {config.model_id}@{config.model_revision} as "
            f"{config.model_file_sha256} in {model_path}"
        )
        return 0
    except (OSError, RuntimeError, ValueError, ValidationError) as error:
        print(f"grounded-answer model cache failed: {error}", file=sys.stderr)
        return 2


def main() -> None:
    """Installed offline grounded-answer runner entry point."""
    raise SystemExit(run_cli())


def cache_main() -> None:
    """Installed online provisioning entry point for the exact pinned GGUF."""
    raise SystemExit(cache_cli())


if __name__ == "__main__":
    main()

"""Provision and execute the separate pinned local grounded-answer candidate."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Literal

import httpx
from huggingface_hub import hf_hub_download
from pydantic import BaseModel, Field, ValidationError

from atlas_pulse.evaluation.base import StrictModel
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.grounded_answer_evaluation import GroundedAnswerTask, GroundedAnswerTaskCase
from atlas_pulse.grounded_answer_runner import (
    GroundedAnswerGeneration,
    GroundedAnswerRunnerConfig,
    GroundedAnswerRuntime,
    LlamaServerRuntime,
    grounded_answer_prompt_sha256,
    run_grounded_answer_candidate,
    validate_grounded_answer_model,
)
from atlas_pulse.relationship_evaluation.candidates import CandidateSystemDefinition

RuntimeFactory = Callable[
    [GroundedAnswerRunnerConfig, Path, Path],
    AbstractContextManager[GroundedAnswerRuntime],
]
ModelDownloader = Callable[..., str]


class _CheckpointCase(StrictModel):
    """One completed generation retained outside final evidence artifacts."""

    case_id: str
    query_id: str
    generation: GroundedAnswerGeneration


class _RunCheckpoint(StrictModel):
    """Exact resumable prefix for one task, configuration, and runtime identity."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    task_id: str
    task_sha256: str
    candidate_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    system: CandidateSystemDefinition
    cases: tuple[_CheckpointCase, ...] = Field(default=(), max_length=100)


class _CheckpointingRuntime:
    """Persist each valid case and reuse only an exact canonical task prefix."""

    def __init__(
        self,
        runtime: GroundedAnswerRuntime,
        task: GroundedAnswerTask,
        config: GroundedAnswerRunnerConfig,
        checkpoint_path: Path,
        notify: Callable[[str], None],
    ) -> None:
        self._runtime = runtime
        self._task = task
        self._config_sha256 = canonical_sha256(config)
        self._checkpoint_path = checkpoint_path
        self._notify = notify
        self._cases: list[_CheckpointCase]
        if checkpoint_path.exists():
            checkpoint = _json_model(checkpoint_path, _RunCheckpoint)
            self._validate_checkpoint(checkpoint)
            self._cases = list(checkpoint.cases)
            notify(
                f"Resuming from checkpoint: {len(self._cases)}/{task.case_count} "
                f"case(s) at {checkpoint_path}"
            )
        else:
            self._cases = []
            notify(f"Checkpointing completed cases to {checkpoint_path}")

    @property
    def system(self) -> CandidateSystemDefinition:
        return self._runtime.system

    def _validate_checkpoint(self, checkpoint: _RunCheckpoint) -> None:
        if (
            checkpoint.task_id != self._task.task_id
            or checkpoint.task_sha256 != self._task.task_sha256
        ):
            raise ValueError("grounded-answer checkpoint does not match the exact task")
        if checkpoint.candidate_config_sha256 != self._config_sha256:
            raise ValueError(
                "grounded-answer checkpoint does not match the candidate configuration"
            )
        if checkpoint.system != self.system:
            raise ValueError("grounded-answer checkpoint does not match the runtime identity")
        expected_cases = self._task.cases[: len(checkpoint.cases)]
        if len(expected_cases) != len(checkpoint.cases):
            raise ValueError("grounded-answer checkpoint contains too many cases")
        for saved, expected in zip(checkpoint.cases, expected_cases, strict=True):
            if saved.case_id != expected.case_id or saved.query_id != expected.query_id:
                raise ValueError("grounded-answer checkpoint is not an exact canonical task prefix")
            if saved.generation.prompt_sha256 != grounded_answer_prompt_sha256(expected):
                raise ValueError(
                    "grounded-answer checkpoint prompt identity does not match the task"
                )

    def _persist(self) -> None:
        checkpoint = _RunCheckpoint(
            task_id=self._task.task_id,
            task_sha256=self._task.task_sha256,
            candidate_config_sha256=self._config_sha256,
            system=self.system,
            cases=tuple(self._cases),
        )
        _write_atomic(
            self._checkpoint_path,
            checkpoint.model_dump_json(indent=2) + "\n",
        )

    def generate(self, case: GroundedAnswerTaskCase) -> GroundedAnswerGeneration:
        expected_index = len(self._cases)
        for index, expected in enumerate(self._task.cases):
            if expected.case_id == getattr(case, "case_id", None):
                if index < expected_index:
                    saved = self._cases[index]
                    self._notify(f"Reusing checkpointed case: {saved.query_id}")
                    return saved.generation
                if index != expected_index:
                    raise ValueError("grounded-answer cases were requested outside canonical order")
                generation = self._runtime.generate(expected)
                if generation.prompt_sha256 != grounded_answer_prompt_sha256(expected):
                    raise ValueError(
                        "grounded-answer runtime prompt identity changed before checkpoint"
                    )
                self._cases.append(
                    _CheckpointCase(
                        case_id=expected.case_id,
                        query_id=expected.query_id,
                        generation=generation,
                    )
                )
                self._persist()
                self._notify(
                    f"Checkpointed case {len(self._cases)}/{self._task.case_count}: "
                    f"{expected.query_id}"
                )
                return generation
        raise ValueError("grounded-answer runtime requested a case outside the exact task")


def _json_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _ensure_writable(
    paths: Sequence[Path],
    *,
    inputs: Sequence[Path],
    checkpoint: Path,
    force: bool,
) -> None:
    target_paths = (*paths, checkpoint)
    resolved = {path.resolve() for path in target_paths}
    if len(resolved) != len(target_paths):
        raise ValueError("output paths must be distinct")
    if resolved & {path.resolve() for path in inputs}:
        raise ValueError("output paths must not replace input artifacts")
    existing = [path for path in paths if path.exists()]
    if existing and not force:
        existing_targets = ", ".join(str(path) for path in existing)
        raise FileExistsError(
            f"refusing to overwrite {existing_targets}; pass --force intentionally"
        )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    _write(temporary, content)
    temporary.replace(path)


def _checkpoint_path(output_run: Path) -> Path:
    return output_run.with_name(f"{output_run.stem}.checkpoint.json")


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
    checkpoint_path = _checkpoint_path(args.output_run)
    try:
        _ensure_writable(
            outputs,
            inputs=inputs,
            checkpoint=checkpoint_path,
            force=args.force,
        )
        task = _json_model(args.task, GroundedAnswerTask)
        config = _json_model(args.candidate_config, GroundedAnswerRunnerConfig)
        with runtime_factory(config, args.model_dir, args.llama_server) as runtime:
            checkpointing_runtime = _CheckpointingRuntime(
                runtime,
                task,
                config,
                checkpoint_path,
                lambda message: print(message, flush=True),
            )
            submission, run, _batch = run_grounded_answer_candidate(
                task,
                config,
                checkpointing_runtime,
                progress=lambda message: print(message, flush=True),
            )
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

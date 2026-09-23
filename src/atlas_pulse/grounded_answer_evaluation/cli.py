"""CLI for grounded-answer capture, candidate import, human review, and scoring."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

from atlas_pulse.grounded_answer_evaluation.adjudication import (
    apply_grounded_answer_adjudication,
    build_grounded_answer_adjudication_sheet,
)
from atlas_pulse.grounded_answer_evaluation.base import (
    GroundedAnswerBenchmark,
    GroundedAnswerCandidateBatch,
    GroundedAnswerSubmission,
    GroundedAnswerTask,
    ReviewedGroundedAnswerBatch,
)
from atlas_pulse.grounded_answer_evaluation.candidates import (
    apply_grounded_answer_submission,
    build_grounded_answer_submission,
)
from atlas_pulse.grounded_answer_evaluation.capture import capture_grounded_answer_task
from atlas_pulse.grounded_answer_evaluation.judgments import (
    apply_grounded_answer_development_review,
    apply_grounded_answer_review,
    build_grounded_answer_review_sheet,
)
from atlas_pulse.grounded_answer_evaluation.metrics import score_grounded_answer_review
from atlas_pulse.grounded_answer_evaluation.report import (
    render_grounded_answer_adjudication_markdown,
    render_grounded_answer_agreement_markdown,
    render_grounded_answer_markdown,
)
from atlas_pulse.relationship_evaluation.candidates import CandidateSystemDefinition


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


async def _capture(args: argparse.Namespace) -> int:
    outputs = (args.output_task, args.output_submission)
    _ensure_writable(outputs, inputs=(args.benchmark,), force=args.force)
    benchmark = _json_model(args.benchmark, GroundedAnswerBenchmark)
    task = await capture_grounded_answer_task(benchmark, base_url=args.base_url)
    submission = build_grounded_answer_submission(task)
    _write(args.output_task, task.model_dump_json(indent=2) + "\n")
    _write(args.output_submission, submission.model_dump_json(indent=2) + "\n")
    empty = sum(case.pack_status == "no_traceable_evidence" for case in task.cases)
    print(
        f"Captured gold-free {task.task_id}: {task.case_count} case(s), "
        f"{empty} without traceable evidence"
    )
    print(f"Complete candidate responses and token/latency fields in {args.output_submission}")
    return 0


def _candidate_import(args: argparse.Namespace) -> int:
    outputs = (args.output_batch, args.output_review_sheet)
    inputs = (args.task, args.submission, args.candidate_definition)
    _ensure_writable(outputs, inputs=inputs, force=args.force)
    task = _json_model(args.task, GroundedAnswerTask)
    submission = _json_model(args.submission, GroundedAnswerSubmission)
    system = _json_model(args.candidate_definition, CandidateSystemDefinition)
    batch = apply_grounded_answer_submission(task, submission, system=system)
    review_sheet = build_grounded_answer_review_sheet(task, batch)
    _write(args.output_batch, batch.model_dump_json(indent=2) + "\n")
    _write(args.output_review_sheet, review_sheet.content)
    print(
        f"Imported blocked {batch.batch_id}; "
        f"{review_sheet.pending_count} model-blind review row(s) require human judgment"
    )
    return 0


def _review(args: argparse.Namespace) -> int:
    _ensure_writable(
        (args.output_review,),
        inputs=(args.task, args.batch, args.judgments),
        force=args.force,
    )
    task = _json_model(args.task, GroundedAnswerTask)
    batch = _json_model(args.batch, GroundedAnswerCandidateBatch)
    review = apply_grounded_answer_review(
        task,
        batch,
        args.judgments.read_text(encoding="utf-8"),
        reviewer=args.reviewer,
    )
    _write(args.output_review, review.model_dump_json(indent=2) + "\n")
    print(
        f"Imported first-pass {review.review_id} from {review.reviewer}; promotion remains blocked"
    )
    return 0


def _development_review(args: argparse.Namespace) -> int:
    _ensure_writable(
        (args.output_review,),
        inputs=(args.task, args.batch, args.judgments),
        force=args.force,
    )
    task = _json_model(args.task, GroundedAnswerTask)
    batch = _json_model(args.batch, GroundedAnswerCandidateBatch)
    review = apply_grounded_answer_development_review(
        task,
        batch,
        args.judgments.read_text(encoding="utf-8"),
        reviewer=args.reviewer,
        review_assistance=args.review_assistance,
    )
    _write(args.output_review, review.model_dump_json(indent=2) + "\n")
    print(
        f"Imported development-only {review.review_id} from {review.reviewer}; "
        "promotion remains blocked"
    )
    return 0


def _compare_reviews(args: argparse.Namespace) -> int:
    outputs = (args.output_json, args.output_markdown, args.output_adjudication_sheet)
    inputs = (args.task, args.batch, args.first_review, args.second_review)
    _ensure_writable(outputs, inputs=inputs, force=args.force)
    task = _json_model(args.task, GroundedAnswerTask)
    batch = _json_model(args.batch, GroundedAnswerCandidateBatch)
    first = _json_model(args.first_review, ReviewedGroundedAnswerBatch)
    second = _json_model(args.second_review, ReviewedGroundedAnswerBatch)
    sheet = build_grounded_answer_adjudication_sheet(task, batch, first, second)
    report = sheet.agreement_report
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_grounded_answer_agreement_markdown(report))
    _write(args.output_adjudication_sheet, sheet.content)
    print(
        f"Compared independent reviews in {report.report_id}: "
        f"{sheet.pending_judgment_count} disputed row(s), "
        f"{sheet.pending_field_count} field(s); promotion remains blocked"
    )
    return 0


def _adjudicate(args: argparse.Namespace) -> int:
    outputs = (args.output_review, args.output_json, args.output_markdown)
    inputs = (
        args.task,
        args.batch,
        args.first_review,
        args.second_review,
        args.judgments,
    )
    _ensure_writable(outputs, inputs=inputs, force=args.force)
    task = _json_model(args.task, GroundedAnswerTask)
    batch = _json_model(args.batch, GroundedAnswerCandidateBatch)
    first = _json_model(args.first_review, ReviewedGroundedAnswerBatch)
    second = _json_model(args.second_review, ReviewedGroundedAnswerBatch)
    review, report = apply_grounded_answer_adjudication(
        task,
        batch,
        first,
        second,
        args.judgments.read_text(encoding="utf-8"),
        adjudicator=args.adjudicator,
    )
    _write(args.output_review, review.model_dump_json(indent=2) + "\n")
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_grounded_answer_adjudication_markdown(report))
    print(
        f"Finalized blocked {review.review_id}: "
        f"{report.adjudication_decision_count} disputed row(s) adjudicated"
    )
    return 0


def _score(args: argparse.Namespace) -> int:
    outputs = (args.output_json, args.output_markdown)
    inputs = (args.task, args.batch, args.review)
    _ensure_writable(outputs, inputs=inputs, force=args.force)
    task = _json_model(args.task, GroundedAnswerTask)
    batch = _json_model(args.batch, GroundedAnswerCandidateBatch)
    review = _json_model(args.review, ReviewedGroundedAnswerBatch)
    if review.development_review is not None:
        raise ValueError("single-review development artifacts require development-score")
    report = score_grounded_answer_review(task, batch, review)
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_grounded_answer_markdown(report))
    print(f"Wrote blocked descriptive report {report.report_id}")
    return 0


def _development_score(args: argparse.Namespace) -> int:
    outputs = (args.output_json, args.output_markdown)
    inputs = (args.task, args.batch, args.review)
    _ensure_writable(outputs, inputs=inputs, force=args.force)
    task = _json_model(args.task, GroundedAnswerTask)
    batch = _json_model(args.batch, GroundedAnswerCandidateBatch)
    review = _json_model(args.review, ReviewedGroundedAnswerBatch)
    if review.development_review is None:
        raise ValueError("development-score requires a single-review development artifact")
    report = score_grounded_answer_review(task, batch, review)
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_grounded_answer_markdown(report))
    print(f"Wrote development-only blocked report {report.report_id}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-pulse-evaluate-grounded-answers",
        description=(
            "Capture gold-free evidence tasks and score externally generated cited claims "
            "through human review."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    capture = commands.add_parser("capture", help="capture one evidence pack per live question")
    capture.add_argument("--benchmark", type=Path, required=True)
    capture.add_argument("--base-url", default="http://localhost:8000")
    capture.add_argument("--output-task", type=Path, required=True)
    capture.add_argument("--output-submission", type=Path, required=True)
    capture.add_argument("--force", action="store_true")

    candidate = commands.add_parser(
        "candidate-import",
        help="validate complete cited answers and emit a model-blind review sheet",
    )
    candidate.add_argument("--task", type=Path, required=True)
    candidate.add_argument("--submission", type=Path, required=True)
    candidate.add_argument("--candidate-definition", type=Path, required=True)
    candidate.add_argument("--output-batch", type=Path, required=True)
    candidate.add_argument("--output-review-sheet", type=Path, required=True)
    candidate.add_argument("--force", action="store_true")

    review = commands.add_parser("review", help="import one complete model-blind human review")
    review.add_argument("--task", type=Path, required=True)
    review.add_argument("--batch", type=Path, required=True)
    review.add_argument("--judgments", type=Path, required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--output-review", type=Path, required=True)
    review.add_argument("--force", action="store_true")

    development_review = commands.add_parser(
        "development-review",
        help="import one declared review for permanently non-promoting development evidence",
    )
    development_review.add_argument("--task", type=Path, required=True)
    development_review.add_argument("--batch", type=Path, required=True)
    development_review.add_argument("--judgments", type=Path, required=True)
    development_review.add_argument("--reviewer", required=True)
    development_review.add_argument(
        "--review-assistance",
        choices=("unassisted", "ai_assisted"),
        required=True,
        help="declare whether AI assistance contributed to the single review",
    )
    development_review.add_argument("--output-review", type=Path, required=True)
    development_review.add_argument("--force", action="store_true")

    compare = commands.add_parser(
        "compare-reviews",
        help="compare two independent reviews and emit a blind adjudication sheet",
    )
    compare.add_argument("--task", type=Path, required=True)
    compare.add_argument("--batch", type=Path, required=True)
    compare.add_argument("--first-review", type=Path, required=True)
    compare.add_argument("--second-review", type=Path, required=True)
    compare.add_argument("--output-json", type=Path, required=True)
    compare.add_argument("--output-markdown", type=Path, required=True)
    compare.add_argument("--output-adjudication-sheet", type=Path, required=True)
    compare.add_argument("--force", action="store_true")

    adjudicate = commands.add_parser(
        "adjudicate",
        help="finalize disputed fields through a separate human adjudicator",
    )
    adjudicate.add_argument("--task", type=Path, required=True)
    adjudicate.add_argument("--batch", type=Path, required=True)
    adjudicate.add_argument("--first-review", type=Path, required=True)
    adjudicate.add_argument("--second-review", type=Path, required=True)
    adjudicate.add_argument("--judgments", type=Path, required=True)
    adjudicate.add_argument("--adjudicator", required=True)
    adjudicate.add_argument("--output-review", type=Path, required=True)
    adjudicate.add_argument("--output-json", type=Path, required=True)
    adjudicate.add_argument("--output-markdown", type=Path, required=True)
    adjudicate.add_argument("--force", action="store_true")

    score = commands.add_parser(
        "score", help="score one complete first-pass or adjudicated review without promotion"
    )
    score.add_argument("--task", type=Path, required=True)
    score.add_argument("--batch", type=Path, required=True)
    score.add_argument("--review", type=Path, required=True)
    score.add_argument("--output-json", type=Path, required=True)
    score.add_argument("--output-markdown", type=Path, required=True)
    score.add_argument("--force", action="store_true")

    development_score = commands.add_parser(
        "development-score",
        help="score one declared single review while permanently blocking promotion",
    )
    development_score.add_argument("--task", type=Path, required=True)
    development_score.add_argument("--batch", type=Path, required=True)
    development_score.add_argument("--review", type=Path, required=True)
    development_score.add_argument("--output-json", type=Path, required=True)
    development_score.add_argument("--output-markdown", type=Path, required=True)
    development_score.add_argument("--force", action="store_true")
    return parser


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Run one grounded-answer evaluation command with clear failure status."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            return asyncio.run(_capture(args))
        if args.command == "candidate-import":
            return _candidate_import(args)
        if args.command == "review":
            return _review(args)
        if args.command == "development-review":
            return _development_review(args)
        if args.command == "compare-reviews":
            return _compare_reviews(args)
        if args.command == "adjudicate":
            return _adjudicate(args)
        if args.command == "development-score":
            return _development_score(args)
        return _score(args)
    except (
        FileExistsError,
        httpx.HTTPError,
        OSError,
        RuntimeError,
        ValueError,
        ValidationError,
    ) as error:
        print(f"grounded-answer evaluation failed: {error}", file=sys.stderr)
        return 2


def main() -> None:
    """Installed grounded-answer evaluation entry point."""
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()

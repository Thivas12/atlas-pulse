"""Command-line workflow for capture, blind review, and claim-pair scoring."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

from atlas_pulse.relationship_evaluation.adjudication import (
    apply_relationship_adjudication,
    build_relationship_adjudication_sheet,
)
from atlas_pulse.relationship_evaluation.base import (
    RelationshipBenchmarkDefinition,
    RelationshipPool,
)
from atlas_pulse.relationship_evaluation.candidates import (
    CandidateEvaluationTask,
    CandidateSystemDefinition,
    apply_candidate_predictions,
    build_candidate_evaluation_task,
    build_candidate_prediction_sheet,
    build_development_candidate_evaluation_task,
    score_relationship_candidate,
    score_relationship_development_candidate,
)
from atlas_pulse.relationship_evaluation.capture import capture_relationship_pool
from atlas_pulse.relationship_evaluation.judgments import (
    apply_relationship_judgments,
    build_relationship_judgment_sheet,
)
from atlas_pulse.relationship_evaluation.metrics import score_relationship_pool
from atlas_pulse.relationship_evaluation.report import (
    render_adjudication_markdown,
    render_candidate_comparison_markdown,
    render_relationship_markdown,
    render_review_agreement_markdown,
)


def _json_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _ensure_writable(
    paths: Sequence[Path],
    *,
    force: bool,
    inputs: Sequence[Path] = (),
) -> None:
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


async def _capture(args: argparse.Namespace) -> int:
    inputs = (args.definition,) + (
        (args.seed_reviewed_pool,) if args.seed_reviewed_pool is not None else ()
    )
    _ensure_writable(
        (args.output, args.judgments_output),
        force=args.force,
        inputs=inputs,
    )
    definition = _json_model(args.definition, RelationshipBenchmarkDefinition)
    seed_pool = (
        _json_model(args.seed_reviewed_pool, RelationshipPool)
        if args.seed_reviewed_pool is not None
        else None
    )
    pool = await capture_relationship_pool(definition, base_url=args.base_url)
    sheet = build_relationship_judgment_sheet(pool, seed_pool=seed_pool)
    _write(args.output, pool.model_dump_json(indent=2) + "\n")
    _write(args.judgments_output, sheet.content)
    print(
        f"Captured {pool.sampled_edge_count}/{pool.available_edge_count} edges as "
        f"{len(pool.cases)} blinded predicate cases"
    )
    if seed_pool is not None:
        print(
            f"Reused {sheet.reused_count} exact prior judgment(s); "
            f"{sheet.pending_count} case(s) remain for review"
        )
    if pool.incidents_truncated or pool.candidate_edges_truncated:
        print(
            "Capture warning: the production incident response reported truncation; "
            "the pool is a bounded sample, not a complete live census",
            file=sys.stderr,
        )
    if pool.sampled_edge_count < pool.available_edge_count:
        print(
            "Sampling notice: source-pair caps selected a deterministic subset of available edges",
            file=sys.stderr,
        )
    if sheet.pending_count:
        print(f"Complete the blank gold_label cells in {args.judgments_output}")
    else:
        print(f"No blank relationship judgments remain in {args.judgments_output}")
    return 0


def _review(args: argparse.Namespace) -> int:
    _ensure_writable(
        (args.output,),
        force=args.force,
        inputs=(args.pool, args.judgments),
    )
    pool = _json_model(args.pool, RelationshipPool)
    reviewed = apply_relationship_judgments(
        pool,
        args.judgments.read_text(encoding="utf-8"),
        reviewer=args.reviewer,
    )
    _write(args.output, reviewed.model_dump_json(indent=2) + "\n")
    print(f"Imported {len(reviewed.cases)} complete claim-pair judgments from {reviewed.reviewer}")
    return 0


def _score(args: argparse.Namespace) -> int:
    _ensure_writable(
        (args.output_json, args.output_markdown),
        force=args.force,
        inputs=(args.pool,),
    )
    pool = _json_model(args.pool, RelationshipPool)
    report = score_relationship_pool(pool)
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_relationship_markdown(report))
    print(f"Wrote {report.report_id}")
    return 0


def _agreement(args: argparse.Namespace) -> int:
    _ensure_writable(
        (args.output_json, args.output_markdown, args.adjudication_output),
        force=args.force,
        inputs=(args.first_pool, args.second_pool),
    )
    first = _json_model(args.first_pool, RelationshipPool)
    second = _json_model(args.second_pool, RelationshipPool)
    sheet = build_relationship_adjudication_sheet(first, second)
    report = sheet.agreement_report
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_review_agreement_markdown(report))
    _write(args.adjudication_output, sheet.content)
    print(
        f"Compared {report.overall.case_count} cases from two independent reviewers: "
        f"{report.overall.agreement_count} agreement(s), "
        f"{report.overall.disagreement_count} disagreement(s)"
    )
    if sheet.pending_count:
        print(f"Complete the adjudicated_label cells in {args.adjudication_output}")
    else:
        print("The reviewers agreed on every case; the adjudication sheet contains only its header")
    return 0


def _adjudicate(args: argparse.Namespace) -> int:
    _ensure_writable(
        (args.output_pool, args.output_json, args.output_markdown),
        force=args.force,
        inputs=(args.first_pool, args.second_pool, args.adjudication_sheet),
    )
    first = _json_model(args.first_pool, RelationshipPool)
    second = _json_model(args.second_pool, RelationshipPool)
    pool, report = apply_relationship_adjudication(
        first,
        second,
        args.adjudication_sheet.read_text(encoding="utf-8"),
        adjudicator=args.adjudicator,
    )
    _write(args.output_pool, pool.model_dump_json(indent=2) + "\n")
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_adjudication_markdown(report))
    print(
        f"Finalized {len(pool.cases)} gold labels as {report.report_id}; "
        f"adjudicated {report.adjudication_decision_count} disagreement(s)"
    )
    return 0


def _candidate_task(args: argparse.Namespace) -> int:
    _ensure_writable(
        (args.output, args.predictions_output),
        force=args.force,
        inputs=(args.pool,),
    )
    pool = _json_model(args.pool, RelationshipPool)
    task = build_candidate_evaluation_task(pool)
    sheet = build_candidate_prediction_sheet(task)
    _write(args.output, task.model_dump_json(indent=2) + "\n")
    _write(args.predictions_output, sheet.content)
    print(
        f"Wrote gold-blind {task.task_id} with {task.case_count} case(s); "
        f"complete predicted_label and latency_ms in {args.predictions_output}"
    )
    return 0


def _development_candidate_task(args: argparse.Namespace) -> int:
    _ensure_writable(
        (args.output, args.predictions_output),
        force=args.force,
        inputs=(args.pool,),
    )
    pool = _json_model(args.pool, RelationshipPool)
    task = build_development_candidate_evaluation_task(pool)
    sheet = build_candidate_prediction_sheet(task)
    _write(args.output, task.model_dump_json(indent=2) + "\n")
    _write(args.predictions_output, sheet.content)
    print(
        f"Wrote development-only label-blind {task.task_id} with {task.case_count} case(s); "
        f"complete predicted_label and latency_ms in {args.predictions_output}"
    )
    return 0


def _candidate_score(args: argparse.Namespace) -> int:
    _ensure_writable(
        (args.output_batch, args.output_json, args.output_markdown),
        force=args.force,
        inputs=(args.pool, args.task, args.predictions, args.candidate_definition),
    )
    pool = _json_model(args.pool, RelationshipPool)
    task = _json_model(args.task, CandidateEvaluationTask)
    system = _json_model(args.candidate_definition, CandidateSystemDefinition)
    batch = apply_candidate_predictions(
        task,
        args.predictions.read_text(encoding="utf-8"),
        system=system,
    )
    report = score_relationship_candidate(pool, task, batch)
    _write(args.output_batch, batch.model_dump_json(indent=2) + "\n")
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_candidate_comparison_markdown(report))
    print(
        f"Wrote blocked comparison {report.report_id}: "
        f"{report.paired_outcomes.improvements} improvement(s), "
        f"{report.paired_outcomes.regressions} regression(s)"
    )
    return 0


def _development_candidate_score(args: argparse.Namespace) -> int:
    _ensure_writable(
        (args.output_batch, args.output_json, args.output_markdown),
        force=args.force,
        inputs=(args.pool, args.task, args.predictions, args.candidate_definition),
    )
    pool = _json_model(args.pool, RelationshipPool)
    task = _json_model(args.task, CandidateEvaluationTask)
    system = _json_model(args.candidate_definition, CandidateSystemDefinition)
    batch = apply_candidate_predictions(
        task,
        args.predictions.read_text(encoding="utf-8"),
        system=system,
    )
    report = score_relationship_development_candidate(
        pool,
        task,
        batch,
        review_assistance=args.review_assistance,
    )
    _write(args.output_batch, batch.model_dump_json(indent=2) + "\n")
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_candidate_comparison_markdown(report))
    print(
        f"Wrote development-only blocked comparison {report.report_id}: "
        f"{report.paired_outcomes.improvements} improvement(s), "
        f"{report.paired_outcomes.regressions} regression(s)"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-pulse-evaluate-relationships",
        description=(
            "Capture, review, and score live AtlasPulse claim pairs with explicit evidence scope."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser(
        "capture", help="sample live measured edges and create a prediction-blind review sheet"
    )
    capture.add_argument("--definition", type=Path, required=True)
    capture.add_argument("--base-url", default="http://localhost:8000")
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--judgments-output", type=Path, required=True)
    capture.add_argument(
        "--seed-reviewed-pool",
        type=Path,
        help="prefill labels only for exact reviewer-visible evidence from this reviewed pool",
    )
    capture.add_argument("--force", action="store_true")

    review = subparsers.add_parser("review", help="import a completed prediction-blind sheet")
    review.add_argument("--pool", type=Path, required=True)
    review.add_argument("--judgments", type=Path, required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--force", action="store_true")

    agreement = subparsers.add_parser(
        "agreement",
        help="measure two exact independent reviews and export only their disagreements",
    )
    agreement.add_argument("--first-pool", type=Path, required=True)
    agreement.add_argument("--second-pool", type=Path, required=True)
    agreement.add_argument("--output-json", type=Path, required=True)
    agreement.add_argument("--output-markdown", type=Path, required=True)
    agreement.add_argument("--adjudication-output", type=Path, required=True)
    agreement.add_argument("--force", action="store_true")

    adjudicate = subparsers.add_parser(
        "adjudicate",
        help="import protected disagreement decisions and finalize one gold pool",
    )
    adjudicate.add_argument("--first-pool", type=Path, required=True)
    adjudicate.add_argument("--second-pool", type=Path, required=True)
    adjudicate.add_argument("--adjudication-sheet", type=Path, required=True)
    adjudicate.add_argument("--adjudicator", required=True)
    adjudicate.add_argument("--output-pool", type=Path, required=True)
    adjudicate.add_argument("--output-json", type=Path, required=True)
    adjudicate.add_argument("--output-markdown", type=Path, required=True)
    adjudicate.add_argument("--force", action="store_true")

    score = subparsers.add_parser("score", help="score a fully reviewed claim-pair pool")
    score.add_argument("--pool", type=Path, required=True)
    score.add_argument("--output-json", type=Path, required=True)
    score.add_argument("--output-markdown", type=Path, required=True)
    score.add_argument("--force", action="store_true")

    candidate_task = subparsers.add_parser(
        "candidate-task",
        help="export adjudicated evidence without gold labels or deployed predictions",
    )
    candidate_task.add_argument("--pool", type=Path, required=True)
    candidate_task.add_argument("--output", type=Path, required=True)
    candidate_task.add_argument("--predictions-output", type=Path, required=True)
    candidate_task.add_argument("--force", action="store_true")

    development_candidate_task = subparsers.add_parser(
        "development-candidate-task",
        help="export one-reviewed evidence for a permanently non-promoting comparison",
    )
    development_candidate_task.add_argument("--pool", type=Path, required=True)
    development_candidate_task.add_argument("--output", type=Path, required=True)
    development_candidate_task.add_argument("--predictions-output", type=Path, required=True)
    development_candidate_task.add_argument("--force", action="store_true")

    candidate_score = subparsers.add_parser(
        "candidate-score",
        help="import external predictions and compare them with the captured deployed rule",
    )
    candidate_score.add_argument("--pool", type=Path, required=True)
    candidate_score.add_argument("--task", type=Path, required=True)
    candidate_score.add_argument("--predictions", type=Path, required=True)
    candidate_score.add_argument("--candidate-definition", type=Path, required=True)
    candidate_score.add_argument("--output-batch", type=Path, required=True)
    candidate_score.add_argument("--output-json", type=Path, required=True)
    candidate_score.add_argument("--output-markdown", type=Path, required=True)
    candidate_score.add_argument("--force", action="store_true")

    development_candidate_score = subparsers.add_parser(
        "development-candidate-score",
        help="compare external predictions with one review and keep promotion blocked",
    )
    development_candidate_score.add_argument("--pool", type=Path, required=True)
    development_candidate_score.add_argument("--task", type=Path, required=True)
    development_candidate_score.add_argument("--predictions", type=Path, required=True)
    development_candidate_score.add_argument("--candidate-definition", type=Path, required=True)
    development_candidate_score.add_argument(
        "--review-assistance",
        choices=("unassisted", "ai_assisted"),
        required=True,
        help="declare whether AI assistance contributed to the single review",
    )
    development_candidate_score.add_argument("--output-batch", type=Path, required=True)
    development_candidate_score.add_argument("--output-json", type=Path, required=True)
    development_candidate_score.add_argument("--output-markdown", type=Path, required=True)
    development_candidate_score.add_argument("--force", action="store_true")
    return parser


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Run one command and convert contract, HTTP, and filesystem failures to exit code 2."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            return asyncio.run(_capture(args))
        if args.command == "review":
            return _review(args)
        if args.command == "score":
            return _score(args)
        if args.command == "agreement":
            return _agreement(args)
        if args.command == "adjudicate":
            return _adjudicate(args)
        if args.command == "candidate-task":
            return _candidate_task(args)
        if args.command == "development-candidate-task":
            return _development_candidate_task(args)
        if args.command == "candidate-score":
            return _candidate_score(args)
        return _development_candidate_score(args)
    except (
        FileExistsError,
        OSError,
        RuntimeError,
        ValueError,
        ValidationError,
        httpx.HTTPError,
    ) as error:
        print(f"relationship evaluation failed: {error}", file=sys.stderr)
        return 2


def main() -> None:
    """Installed console-script entry point."""
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()

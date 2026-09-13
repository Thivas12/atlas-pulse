"""Command-line workflow for capture, blind review, and claim-pair scoring."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

from atlas_pulse.relationship_evaluation.base import (
    RelationshipBenchmarkDefinition,
    RelationshipPool,
)
from atlas_pulse.relationship_evaluation.capture import capture_relationship_pool
from atlas_pulse.relationship_evaluation.judgments import (
    apply_relationship_judgments,
    build_relationship_judgment_sheet,
)
from atlas_pulse.relationship_evaluation.metrics import score_relationship_pool
from atlas_pulse.relationship_evaluation.report import render_relationship_markdown


def _json_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _ensure_writable(paths: Sequence[Path], *, force: bool) -> None:
    if len(set(paths)) != len(paths):
        raise ValueError("output paths must be distinct")
    existing = [path for path in paths if path.exists()]
    if existing and not force:
        targets = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"refusing to overwrite {targets}; pass --force intentionally")


async def _capture(args: argparse.Namespace) -> int:
    _ensure_writable((args.output, args.judgments_output), force=args.force)
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
    _ensure_writable((args.output,), force=args.force)
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
    _ensure_writable((args.output_json, args.output_markdown), force=args.force)
    pool = _json_model(args.pool, RelationshipPool)
    report = score_relationship_pool(pool)
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_relationship_markdown(report))
    print(f"Wrote {report.report_id}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-pulse-evaluate-relationships",
        description="Capture, blindly review, and score live AtlasPulse claim pairs.",
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

    score = subparsers.add_parser("score", help="score a fully reviewed claim-pair pool")
    score.add_argument("--pool", type=Path, required=True)
    score.add_argument("--output-json", type=Path, required=True)
    score.add_argument("--output-markdown", type=Path, required=True)
    score.add_argument("--force", action="store_true")
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
        return _score(args)
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

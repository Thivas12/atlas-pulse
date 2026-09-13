"""Command-line workflow for capture, human review, scoring, and gates."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ValidationError

from atlas_pulse.evaluation.base import (
    CandidatePool,
    EvaluationQuerySet,
    GateOutcome,
    GatePolicy,
)
from atlas_pulse.evaluation.capture import capture_pool
from atlas_pulse.evaluation.judgments import apply_judgments, export_judgments
from atlas_pulse.evaluation.metrics import evaluate_gates, score_pool
from atlas_pulse.evaluation.report import render_markdown


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
    query_set = _json_model(args.queries, EvaluationQuerySet)
    pool = await capture_pool(query_set, base_url=args.base_url)
    _write(
        args.output,
        pool.model_dump_json(indent=2) + "\n",
    )
    _write(args.judgments_output, export_judgments(pool))
    print(f"Captured {sum(len(query.candidates) for query in pool.queries)} pooled candidates")
    empty_queries = tuple(
        pooled_query.query.query_id for pooled_query in pool.queries if not pooled_query.candidates
    )
    if empty_queries:
        print(
            "Candidate coverage warning; no mode returned results for: " + ", ".join(empty_queries),
            file=sys.stderr,
        )
    print(f"Review every relevance_0_to_3 cell in {args.judgments_output}")
    return 0


def _review(args: argparse.Namespace) -> int:
    _ensure_writable((args.output,), force=args.force)
    pool = _json_model(args.pool, CandidatePool)
    reviewed = apply_judgments(
        pool,
        args.judgments.read_text(encoding="utf-8"),
        reviewer=args.reviewer,
    )
    _write(args.output, reviewed.model_dump_json(indent=2) + "\n")
    print(f"Imported complete judgments from {reviewed.reviewer}")
    return 0


def _score(args: argparse.Namespace) -> int:
    _ensure_writable((args.output_json, args.output_markdown), force=args.force)
    pool = _json_model(args.pool, CandidatePool)
    report = score_pool(pool, cutoffs=args.cutoff)
    gates: tuple[GateOutcome, ...] = ()
    if args.policy is not None:
        policy = _json_model(args.policy, GatePolicy)
        gates = evaluate_gates(report, policy)
        report = report.model_copy(
            update={"gate_policy_id": policy.policy_id, "gate_outcomes": gates}
        )
    _write(
        args.output_json,
        report.model_dump_json(indent=2) + "\n",
    )
    _write(
        args.output_markdown,
        render_markdown(report, gates=gates),
    )
    failed = tuple(gate.rule_id for gate in gates if not gate.passed)
    if failed:
        print(f"Retrieval gates failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"Wrote {report.report_id}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-pulse-evaluate",
        description="Capture and score human-reviewed AtlasPulse retrieval pools.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="pool and blind live retrieval candidates")
    capture.add_argument("--queries", type=Path, required=True)
    capture.add_argument("--base-url", default="http://localhost:8000")
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--judgments-output", type=Path, required=True)
    capture.add_argument("--force", action="store_true")

    review = subparsers.add_parser("review", help="import a completed rank-blind judgment sheet")
    review.add_argument("--pool", type=Path, required=True)
    review.add_argument("--judgments", type=Path, required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--force", action="store_true")

    score = subparsers.add_parser("score", help="score a completed human-reviewed pool")
    score.add_argument("--pool", type=Path, required=True)
    score.add_argument("--policy", type=Path)
    score.add_argument("--cutoff", type=int, action="append", default=None)
    score.add_argument("--output-json", type=Path, required=True)
    score.add_argument("--output-markdown", type=Path, required=True)
    score.add_argument("--force", action="store_true")
    return parser


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Run one CLI command and map validation/IO failures to a clear exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            return asyncio.run(_capture(args))
        if args.command == "review":
            return _review(args)
        if args.cutoff is None:
            args.cutoff = [1, 3, 5, 10]
        return _score(args)
    except (FileExistsError, OSError, RuntimeError, ValueError, ValidationError) as error:
        print(f"evaluation failed: {error}", file=sys.stderr)
        return 2


def main() -> None:
    """Installed console-script entry point."""
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()

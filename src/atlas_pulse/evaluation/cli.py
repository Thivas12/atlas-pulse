"""Command-line workflow for capture, review, scoring, comparison, campaigns, and gates."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ValidationError

from atlas_pulse.evaluation.adjudication import (
    apply_retrieval_adjudication,
    build_retrieval_adjudication_sheet,
)
from atlas_pulse.evaluation.base import (
    CandidatePool,
    EvaluationQuerySet,
    GateOutcome,
    GatePolicy,
)
from atlas_pulse.evaluation.campaign import build_campaign, render_campaign_markdown
from atlas_pulse.evaluation.capture import capture_pool
from atlas_pulse.evaluation.comparison import compare_pools, render_comparison_markdown
from atlas_pulse.evaluation.diagnostics import (
    diagnose_retrieval_readiness,
    render_retrieval_readiness_markdown,
)
from atlas_pulse.evaluation.judging import (
    run_judgment_session,
    run_retrieval_adjudication_session,
)
from atlas_pulse.evaluation.judgments import apply_judgments, build_judgment_sheet
from atlas_pulse.evaluation.metrics import evaluate_gates, score_pool
from atlas_pulse.evaluation.report import (
    render_markdown,
    render_retrieval_adjudication_markdown,
    render_retrieval_review_agreement_markdown,
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
    if resolved_outputs & {path.resolve() for path in inputs}:
        raise ValueError("output paths must not replace input artifacts")
    existing = [path for path in paths if path.exists()]
    if existing and not force:
        targets = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"refusing to overwrite {targets}; pass --force intentionally")


def _report_rate_limit_wait(seconds: int, reason: str) -> None:
    print(f"Rate-limit pacing: waiting {seconds}s for {reason}", file=sys.stderr)


async def _capture(args: argparse.Namespace) -> int:
    _ensure_writable((args.output, args.judgments_output), force=args.force)
    query_set = _json_model(args.queries, EvaluationQuerySet)
    seed_pool = (
        _json_model(args.seed_reviewed_pool, CandidatePool)
        if args.seed_reviewed_pool is not None
        else None
    )
    pool = await capture_pool(
        query_set,
        base_url=args.base_url,
        on_rate_limit_wait=_report_rate_limit_wait,
    )
    sheet = build_judgment_sheet(pool, seed_pool=seed_pool)
    _write(
        args.output,
        pool.model_dump_json(indent=2) + "\n",
    )
    _write(args.judgments_output, sheet.content)
    candidate_count = sum(len(query.candidates) for query in pool.queries)
    print(f"Captured {candidate_count} pooled candidates")
    if seed_pool is not None:
        print(
            f"Reused {sheet.reused_count} exact prior judgment(s); "
            f"{sheet.pending_count} candidate(s) remain for review"
        )
    empty_queries = tuple(
        pooled_query.query.query_id for pooled_query in pool.queries if not pooled_query.candidates
    )
    if empty_queries:
        print(
            "Candidate coverage warning; no mode returned results for: " + ", ".join(empty_queries),
            file=sys.stderr,
        )
    if sheet.pending_count:
        print(f"Complete the blank relevance_0_to_3 cells in {args.judgments_output}")
    else:
        print(f"No blank relevance judgments remain in {args.judgments_output}")
    return 0


async def _diagnose(args: argparse.Namespace) -> int:
    _ensure_writable((args.output_json, args.output_markdown), force=args.force)
    query_set = _json_model(args.queries, EvaluationQuerySet)
    report = await diagnose_retrieval_readiness(
        query_set,
        base_url=args.base_url,
        expected_commit_sha=args.expected_commit,
        sleeper=asyncio.sleep,
        on_rate_limit_wait=_report_rate_limit_wait,
    )
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_retrieval_readiness_markdown(report))
    print(f"Wrote {report.report_id}")
    if report.empty_query_ids:
        print(
            "Eligibility warning; no dense candidate for: " + ", ".join(report.empty_query_ids),
            file=sys.stderr,
        )
    if not report.ready_for_capture:
        if report.blocked_sources:
            print(
                "Source pipeline blocked for: " + ", ".join(report.blocked_sources),
                file=sys.stderr,
            )
        return 1
    print("PASS: deployment and required source pipelines are ready for a fresh capture")
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


def _judge(args: argparse.Namespace) -> int:
    pool = _json_model(args.pool, CandidatePool)
    run_judgment_session(pool, args.judgments)
    return 0


def _review_sheet(args: argparse.Namespace) -> int:
    _ensure_writable((args.output,), force=args.force, inputs=(args.pool,))
    pool = _json_model(args.pool, CandidatePool)
    if pool.judgment_status != "unjudged":
        raise ValueError("an independent review sheet requires the original unjudged pool")
    sheet = build_judgment_sheet(pool)
    _write(args.output, sheet.content)
    print(f"Wrote {sheet.pending_count} rank-blind candidate(s) to {args.output}")
    return 0


def _agreement(args: argparse.Namespace) -> int:
    _ensure_writable(
        (args.output_json, args.output_markdown, args.adjudication_output),
        force=args.force,
        inputs=(args.first_pool, args.second_pool),
    )
    first = _json_model(args.first_pool, CandidatePool)
    second = _json_model(args.second_pool, CandidatePool)
    sheet = build_retrieval_adjudication_sheet(first, second)
    report = sheet.agreement_report
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_retrieval_review_agreement_markdown(report))
    _write(args.adjudication_output, sheet.content)
    print(
        f"Compared {report.overall.judgment_count} judgments from two independent reviewers: "
        f"{report.overall.agreement_count} agreement(s), "
        f"{report.overall.disagreement_count} disagreement(s)"
    )
    if sheet.pending_count:
        print(
            "Resolve the blinded disagreements with the judge-adjudication command: "
            f"{args.adjudication_output}"
        )
    else:
        print("The reviewers agreed on every candidate; the adjudication sheet has only a header")
    return 0


def _judge_adjudication(args: argparse.Namespace) -> int:
    first = _json_model(args.first_pool, CandidatePool)
    second = _json_model(args.second_pool, CandidatePool)
    run_retrieval_adjudication_session(
        first,
        second,
        args.adjudication_sheet,
    )
    return 0


def _adjudicate(args: argparse.Namespace) -> int:
    _ensure_writable(
        (args.output_pool, args.output_json, args.output_markdown),
        force=args.force,
        inputs=(args.first_pool, args.second_pool, args.adjudication_sheet),
    )
    first = _json_model(args.first_pool, CandidatePool)
    second = _json_model(args.second_pool, CandidatePool)
    pool, report = apply_retrieval_adjudication(
        first,
        second,
        args.adjudication_sheet.read_text(encoding="utf-8"),
        adjudicator=args.adjudicator,
    )
    _write(args.output_pool, pool.model_dump_json(indent=2) + "\n")
    _write(args.output_json, report.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_retrieval_adjudication_markdown(report))
    print(
        f"Finalized {report.judgment_count} relevance judgments as {report.report_id}; "
        f"adjudicated {report.adjudication_decision_count} disagreement(s)"
    )
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


def _compare(args: argparse.Namespace) -> int:
    _ensure_writable((args.output_json, args.output_markdown), force=args.force)
    baseline = _json_model(args.baseline_pool, CandidatePool)
    candidate = _json_model(args.candidate_pool, CandidatePool)
    comparison = compare_pools(baseline, candidate, cutoffs=tuple(args.cutoff))
    _write(args.output_json, comparison.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_comparison_markdown(comparison))
    print(f"Wrote {comparison.comparison_id}")
    return 0


def _campaign(args: argparse.Namespace) -> int:
    _ensure_writable((args.output_json, args.output_markdown), force=args.force)
    pools = tuple(_json_model(path, CandidatePool) for path in args.pool)
    campaign = build_campaign(pools, cutoffs=tuple(args.cutoff))
    _write(args.output_json, campaign.model_dump_json(indent=2) + "\n")
    _write(args.output_markdown, render_campaign_markdown(campaign))
    print(f"Wrote {campaign.campaign_id} from {campaign.capture_count} reviewed captures")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-pulse-evaluate",
        description=(
            "Capture, independently review, adjudicate, score, compare, and trend "
            "AtlasPulse retrieval pools."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    diagnose = subparsers.add_parser(
        "diagnose",
        help="trace a frozen query set through source freshness, projection, and retrieval",
    )
    diagnose.add_argument("--queries", type=Path, required=True)
    diagnose.add_argument("--base-url", default="http://localhost:8000")
    diagnose.add_argument("--expected-commit", required=True)
    diagnose.add_argument("--output-json", type=Path, required=True)
    diagnose.add_argument("--output-markdown", type=Path, required=True)
    diagnose.add_argument("--force", action="store_true")

    capture = subparsers.add_parser("capture", help="pool and blind live retrieval candidates")
    capture.add_argument("--queries", type=Path, required=True)
    capture.add_argument("--base-url", default="http://localhost:8000")
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--judgments-output", type=Path, required=True)
    capture.add_argument(
        "--seed-reviewed-pool",
        type=Path,
        help="prefill judgments only for byte-identical evidence from this reviewed pool",
    )
    capture.add_argument("--force", action="store_true")

    judge = subparsers.add_parser(
        "judge",
        help="resume rank-blind human grading in the terminal",
    )
    judge.add_argument("--pool", type=Path, required=True)
    judge.add_argument("--judgments", type=Path, required=True)

    review_sheet = subparsers.add_parser(
        "review-sheet",
        help="export a fresh rank-blind sheet from the original unjudged pool",
    )
    review_sheet.add_argument("--pool", type=Path, required=True)
    review_sheet.add_argument("--output", type=Path, required=True)
    review_sheet.add_argument("--force", action="store_true")

    review = subparsers.add_parser("review", help="import a completed rank-blind judgment sheet")
    review.add_argument("--pool", type=Path, required=True)
    review.add_argument("--judgments", type=Path, required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--force", action="store_true")

    agreement = subparsers.add_parser(
        "agreement",
        help="measure two exact independent reviews and export only disagreements",
    )
    agreement.add_argument("--first-pool", type=Path, required=True)
    agreement.add_argument("--second-pool", type=Path, required=True)
    agreement.add_argument("--output-json", type=Path, required=True)
    agreement.add_argument("--output-markdown", type=Path, required=True)
    agreement.add_argument("--adjudication-output", type=Path, required=True)
    agreement.add_argument("--force", action="store_true")

    judge_adjudication = subparsers.add_parser(
        "judge-adjudication",
        help="resume reviewer-blind disagreement adjudication in the terminal",
    )
    judge_adjudication.add_argument("--first-pool", type=Path, required=True)
    judge_adjudication.add_argument("--second-pool", type=Path, required=True)
    judge_adjudication.add_argument("--adjudication-sheet", type=Path, required=True)

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

    score = subparsers.add_parser("score", help="score a completed human-reviewed pool")
    score.add_argument("--pool", type=Path, required=True)
    score.add_argument("--policy", type=Path)
    score.add_argument("--cutoff", type=int, action="append", default=None)
    score.add_argument("--output-json", type=Path, required=True)
    score.add_argument("--output-markdown", type=Path, required=True)
    score.add_argument("--force", action="store_true")

    compare = subparsers.add_parser(
        "compare",
        help="compare two reviewed captures against their shared candidate union",
    )
    compare.add_argument("--baseline-pool", type=Path, required=True)
    compare.add_argument("--candidate-pool", type=Path, required=True)
    compare.add_argument("--cutoff", type=int, action="append", default=None)
    compare.add_argument("--output-json", type=Path, required=True)
    compare.add_argument("--output-markdown", type=Path, required=True)
    compare.add_argument("--force", action="store_true")

    campaign = subparsers.add_parser(
        "campaign",
        help="build a global-union trajectory from chronological reviewed pools",
    )
    campaign.add_argument(
        "--pool",
        type=Path,
        action="append",
        required=True,
        help="reviewed pool in oldest-to-newest order; repeat for every capture",
    )
    campaign.add_argument("--cutoff", type=int, action="append", default=None)
    campaign.add_argument("--output-json", type=Path, required=True)
    campaign.add_argument("--output-markdown", type=Path, required=True)
    campaign.add_argument("--force", action="store_true")
    return parser


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Run one CLI command and map validation/IO failures to a clear exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "diagnose":
            return asyncio.run(_diagnose(args))
        if args.command == "capture":
            return asyncio.run(_capture(args))
        if args.command == "judge":
            return _judge(args)
        if args.command == "review-sheet":
            return _review_sheet(args)
        if args.command == "review":
            return _review(args)
        if args.command == "agreement":
            return _agreement(args)
        if args.command == "judge-adjudication":
            return _judge_adjudication(args)
        if args.command == "adjudicate":
            return _adjudicate(args)
        if args.cutoff is None:
            args.cutoff = [1, 3, 5, 10]
        if args.command == "score":
            return _score(args)
        if args.command == "compare":
            return _compare(args)
        return _campaign(args)
    except (FileExistsError, OSError, RuntimeError, ValueError, ValidationError) as error:
        print(f"evaluation failed: {error}", file=sys.stderr)
        return 2


def main() -> None:
    """Installed console-script entry point."""
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()

"""Resumable rank-blind terminal workflow for human retrieval judgments."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from atlas_pulse.evaluation.adjudication import (
    render_retrieval_adjudication_rows,
    validate_partial_retrieval_adjudication,
)
from atlas_pulse.evaluation.base import CandidatePool, EvaluationQuery, PooledCandidate
from atlas_pulse.evaluation.judgments import (
    render_judgment_rows,
    spreadsheet_safe_text,
    validate_partial_judgments,
)

Prompt = Callable[[str], str]

_RUBRIC = (
    "0 = not relevant | 1 = topically related | "
    "2 = useful but incomplete | 3 = direct and actionable"
)


@dataclass(frozen=True, slots=True)
class JudgmentProgress:
    """Progress recorded after an interactive judgment session."""

    total_count: int
    graded_count: int
    pending_count: int
    stopped_early: bool


def _terminal_safe(value: str) -> str:
    """Remove terminal control characters from untrusted public evidence."""
    return "".join(
        character
        if character in {"\n", "\t"} or character.isprintable()
        else "\N{REPLACEMENT CHARACTER}"
        for character in value
    )


def _wrapped(value: str, *, width: int = 100) -> str:
    normalized = " ".join(_terminal_safe(value).split())
    return textwrap.fill(normalized, width=width, replace_whitespace=False) or "(none)"


def _atomic_write(path: Path, content: str) -> None:
    """Replace a judgment sheet only after its complete new contents reach disk."""
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _candidate_index(
    pool: CandidatePool,
) -> dict[tuple[str, str], tuple[EvaluationQuery, PooledCandidate]]:
    return {
        (pooled_query.query.query_id, candidate.document_id): (
            pooled_query.query,
            candidate,
        )
        for pooled_query in pool.queries
        for candidate in pooled_query.candidates
    }


def _emit_candidate(
    output: TextIO,
    *,
    position: int,
    total: int,
    graded: int,
    query: EvaluationQuery,
    candidate: PooledCandidate,
) -> None:
    print("", file=output)
    print("=" * 100, file=output)
    print(
        f"Candidate {position}/{total} | {graded} already graded | "
        f"query {_terminal_safe(query.query_id)}",
        file=output,
    )
    print(f"QUERY: {_wrapped(query.text)}", file=output)
    print(f"TITLE: {_wrapped(candidate.title)}", file=output)
    print(
        "SOURCE: "
        f"{_terminal_safe(candidate.source)} | "
        f"OCCURRED: {candidate.occurred_at.isoformat()}",
        file=output,
    )
    print("EVIDENCE:", file=output)
    print(_wrapped(candidate.document_text), file=output)
    print(
        "CITATION: "
        f"{_terminal_safe(candidate.citation_status)} | "
        f"{_wrapped(candidate.citation_url or 'not available')}",
        file=output,
    )
    print(_RUBRIC, file=output)


def _progress(rows: list[dict[str, str]], *, stopped_early: bool) -> JudgmentProgress:
    graded = sum(bool(row["relevance_0_to_3"].strip()) for row in rows)
    return JudgmentProgress(
        total_count=len(rows),
        graded_count=graded,
        pending_count=len(rows) - graded,
        stopped_early=stopped_early,
    )


def run_judgment_session(
    pool: CandidatePool,
    judgments_path: Path,
    *,
    prompt: Prompt | None = None,
    output: TextIO | None = None,
) -> JudgmentProgress:
    """Grade blank candidates interactively and save every accepted answer atomically."""
    active_prompt = prompt or input
    active_output = output or sys.stdout
    rows = list(
        validate_partial_judgments(
            pool,
            judgments_path.read_text(encoding="utf-8"),
        )
    )
    candidates = _candidate_index(pool)
    pending_indices = [
        index for index, row in enumerate(rows) if not row["relevance_0_to_3"].strip()
    ]

    initial = _progress(rows, stopped_early=False)
    print(
        f"Validated {initial.total_count} rank-blind candidates; "
        f"{initial.graded_count} graded and {initial.pending_count} pending.",
        file=active_output,
    )
    print(
        "Answers are saved after every accepted grade. Enter q to stop safely.", file=active_output
    )
    if not pending_indices:
        print("All candidates are graded; the sheet is ready to import.", file=active_output)
        return initial

    for row_index in pending_indices:
        row = rows[row_index]
        key = (row["query_id"], row["document_id"])
        query, candidate = candidates[key]
        current = _progress(rows, stopped_early=False)
        _emit_candidate(
            active_output,
            position=row_index + 1,
            total=len(rows),
            graded=current.graded_count,
            query=query,
            candidate=candidate,
        )

        while True:
            try:
                answer = (
                    active_prompt("Grade [0/1/2/3, s=skip, q=save and quit, ?=rubric]: ")
                    .strip()
                    .casefold()
                )
            except (EOFError, KeyboardInterrupt):
                print("\nStopped safely; all earlier grades are already saved.", file=active_output)
                return _progress(rows, stopped_early=True)
            if answer == "q":
                print("Stopped safely; all earlier grades are already saved.", file=active_output)
                return _progress(rows, stopped_early=True)
            if answer == "s":
                print("Skipped; this candidate remains pending.", file=active_output)
                break
            if answer == "?":
                print(_RUBRIC, file=active_output)
                continue
            if answer not in {"0", "1", "2", "3"}:
                print("Invalid choice. Enter 0, 1, 2, 3, s, q, or ?.", file=active_output)
                continue

            while True:
                try:
                    rationale = " ".join(
                        active_prompt("Rationale (optional; press Enter to omit): ").split()
                    )
                except (EOFError, KeyboardInterrupt):
                    print("\nGrade not saved; all earlier grades remain safe.", file=active_output)
                    return _progress(rows, stopped_early=True)
                if len(rationale) <= 1_000:
                    break
                print("Rationale must be 1000 characters or fewer.", file=active_output)

            row["relevance_0_to_3"] = answer
            row["rationale"] = spreadsheet_safe_text(rationale)
            _atomic_write(judgments_path, render_judgment_rows(rows))
            saved = _progress(rows, stopped_early=False)
            print(
                f"Saved: {saved.graded_count}/{saved.total_count} graded; "
                f"{saved.pending_count} pending.",
                file=active_output,
            )
            break

    final = _progress(rows, stopped_early=False)
    if final.pending_count:
        print(
            f"Session reached the end with {final.pending_count} skipped candidate(s); "
            "run the same command again to resume them.",
            file=active_output,
        )
    else:
        print("All candidates are graded; the sheet is ready to import.", file=active_output)
    return final


def _adjudication_progress(
    rows: list[dict[str, str]],
    *,
    stopped_early: bool,
) -> JudgmentProgress:
    graded = sum(bool(row["adjudicated_relevance_0_to_3"].strip()) for row in rows)
    return JudgmentProgress(
        total_count=len(rows),
        graded_count=graded,
        pending_count=len(rows) - graded,
        stopped_early=stopped_early,
    )


def _emit_disagreement(
    output: TextIO,
    *,
    position: int,
    total: int,
    graded: int,
    row: dict[str, str],
) -> None:
    print("", file=output)
    print("=" * 100, file=output)
    print(
        f"Disagreement {position}/{total} | {graded} already adjudicated | "
        f"query {_terminal_safe(row['query_id'])}",
        file=output,
    )
    print(f"QUERY: {_wrapped(row['query_text'])}", file=output)
    print(f"TITLE: {_wrapped(row['title'])}", file=output)
    print(
        f"SOURCE: {_terminal_safe(row['source'])} | OCCURRED: {_terminal_safe(row['occurred_at'])}",
        file=output,
    )
    print("EVIDENCE:", file=output)
    print(_wrapped(row["document_text"]), file=output)
    print(
        "CITATION: "
        f"{_terminal_safe(row['citation_status'])} | "
        f"{_wrapped(row['citation_url'] or 'not available')}",
        file=output,
    )
    print(
        "REVIEW A: "
        f"{_terminal_safe(row['review_a_relevance_0_to_3'])} | "
        f"{_wrapped(row['review_a_rationale'] or 'no rationale')}",
        file=output,
    )
    print(
        "REVIEW B: "
        f"{_terminal_safe(row['review_b_relevance_0_to_3'])} | "
        f"{_wrapped(row['review_b_rationale'] or 'no rationale')}",
        file=output,
    )
    print(_RUBRIC, file=output)


def run_retrieval_adjudication_session(
    first: CandidatePool,
    second: CandidatePool,
    adjudication_path: Path,
    *,
    prompt: Prompt | None = None,
    output: TextIO | None = None,
) -> JudgmentProgress:
    """Resolve blinded review disagreements and atomically save each decision."""
    active_prompt = prompt or input
    active_output = output or sys.stdout
    rows = list(
        validate_partial_retrieval_adjudication(
            first,
            second,
            adjudication_path.read_text(encoding="utf-8"),
        )
    )
    pending_indices = [
        index for index, row in enumerate(rows) if not row["adjudicated_relevance_0_to_3"].strip()
    ]
    initial = _adjudication_progress(rows, stopped_early=False)
    print(
        f"Validated {initial.total_count} blinded disagreement(s); "
        f"{initial.graded_count} adjudicated and {initial.pending_count} pending.",
        file=active_output,
    )
    print(
        "Decisions are saved after every accepted grade. Enter q to stop safely.",
        file=active_output,
    )
    if not pending_indices:
        print("All disagreements are resolved; the sheet is ready to import.", file=active_output)
        return initial

    for row_index in pending_indices:
        row = rows[row_index]
        current = _adjudication_progress(rows, stopped_early=False)
        _emit_disagreement(
            active_output,
            position=row_index + 1,
            total=len(rows),
            graded=current.graded_count,
            row=row,
        )
        while True:
            try:
                answer = (
                    active_prompt("Final grade [0/1/2/3, s=skip, q=save and quit, ?=rubric]: ")
                    .strip()
                    .casefold()
                )
            except (EOFError, KeyboardInterrupt):
                print("\nStopped safely; all earlier decisions are saved.", file=active_output)
                return _adjudication_progress(rows, stopped_early=True)
            if answer == "q":
                print("Stopped safely; all earlier decisions are saved.", file=active_output)
                return _adjudication_progress(rows, stopped_early=True)
            if answer == "s":
                print("Skipped; this disagreement remains pending.", file=active_output)
                break
            if answer == "?":
                print(_RUBRIC, file=active_output)
                continue
            if answer not in {"0", "1", "2", "3"}:
                print("Invalid choice. Enter 0, 1, 2, 3, s, q, or ?.", file=active_output)
                continue

            while True:
                try:
                    rationale = " ".join(
                        active_prompt("Rationale (required, 10-1000 characters): ").split()
                    )
                except (EOFError, KeyboardInterrupt):
                    print(
                        "\nDecision not saved; all earlier decisions remain safe.",
                        file=active_output,
                    )
                    return _adjudication_progress(rows, stopped_early=True)
                if 10 <= len(rationale) <= 1_000:
                    break
                print("Rationale must contain 10 to 1000 characters.", file=active_output)

            row["adjudicated_relevance_0_to_3"] = answer
            row["adjudication_rationale"] = spreadsheet_safe_text(rationale)
            _atomic_write(
                adjudication_path,
                render_retrieval_adjudication_rows(rows),
            )
            saved = _adjudication_progress(rows, stopped_early=False)
            print(
                f"Saved: {saved.graded_count}/{saved.total_count} adjudicated; "
                f"{saved.pending_count} pending.",
                file=active_output,
            )
            break

    final = _adjudication_progress(rows, stopped_early=False)
    if final.pending_count:
        print(
            f"Session reached the end with {final.pending_count} skipped disagreement(s); "
            "run the same command again to resume them.",
            file=active_output,
        )
    else:
        print("All disagreements are resolved; the sheet is ready to import.", file=active_output)
    return final

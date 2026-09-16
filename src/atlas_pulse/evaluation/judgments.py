"""Rank-blind CSV export and strict human-judgment import."""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from atlas_pulse.evaluation.base import CandidatePool, PooledCandidate

JUDGMENT_COLUMNS = (
    "query_id",
    "query_text",
    "document_id",
    "document_hash",
    "source",
    "title",
    "occurred_at",
    "document_text",
    "citation_status",
    "citation_url",
    "relevance_0_to_3",
    "rationale",
)


@dataclass(frozen=True, slots=True)
class JudgmentSheet:
    """Rank-blind CSV plus an auditable exact-reuse count."""

    content: str
    reused_count: int
    pending_count: int


JudgmentKey = tuple[str, str]
ParsedGrade = tuple[int | None, str | None]


def spreadsheet_safe_text(value: str) -> str:
    """Neutralize public text that spreadsheet software could treat as a formula."""
    stripped = value.lstrip()
    return f"'{value}" if stripped.startswith(("=", "+", "-", "@")) else value


def build_judgment_sheet(
    pool: CandidatePool,
    *,
    seed_pool: CandidatePool | None = None,
) -> JudgmentSheet:
    """Create a blind sheet and reuse only identical evidence from a reviewed pool."""
    reusable: dict[tuple[str, str], PooledCandidate] = {}
    if seed_pool is not None:
        if seed_pool.judgment_status != "reviewed":
            raise ValueError("a judgment seed pool must be fully reviewed")
        if (
            seed_pool.query_set_id != pool.query_set_id
            or seed_pool.query_set_sha256 != pool.query_set_sha256
        ):
            raise ValueError("judgment reuse requires the same query-set ID and SHA-256")
        current_queries = {item.query.query_id: item.query for item in pool.queries}
        seed_queries = {item.query.query_id: item.query for item in seed_pool.queries}
        if current_queries != seed_queries:
            raise ValueError("judgment reuse requires identical captured query definitions")
        reusable = {
            (pooled_query.query.query_id, candidate.document_id): candidate
            for pooled_query in seed_pool.queries
            for candidate in pooled_query.candidates
        }

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=JUDGMENT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    reused_count = 0
    pending_count = 0
    for pooled_query in pool.queries:
        for candidate in pooled_query.candidates:
            prior = reusable.get((pooled_query.query.query_id, candidate.document_id))
            if prior is not None and prior.evidence_identity() == candidate.evidence_identity():
                relevance = prior.relevance
                rationale = prior.rationale
                reused_count += 1
            else:
                relevance = candidate.relevance
                rationale = candidate.rationale
                pending_count += int(relevance is None)
            writer.writerow(
                {
                    "query_id": pooled_query.query.query_id,
                    "query_text": spreadsheet_safe_text(pooled_query.query.text),
                    "document_id": candidate.document_id,
                    "document_hash": candidate.document_hash,
                    "source": candidate.source,
                    "title": spreadsheet_safe_text(candidate.title),
                    "occurred_at": candidate.occurred_at.isoformat(),
                    "document_text": spreadsheet_safe_text(candidate.document_text),
                    "citation_status": candidate.citation_status,
                    "citation_url": spreadsheet_safe_text(candidate.citation_url or ""),
                    "relevance_0_to_3": "" if relevance is None else relevance,
                    "rationale": spreadsheet_safe_text(rationale or ""),
                }
            )
    return JudgmentSheet(
        content=output.getvalue(),
        reused_count=reused_count,
        pending_count=pending_count,
    )


def export_judgments(pool: CandidatePool) -> str:
    """Create a reviewer sheet that deliberately excludes modes, scores, and ranks."""
    return build_judgment_sheet(pool).content


def render_judgment_rows(rows: Sequence[Mapping[str, str]]) -> str:
    """Serialize validated judgment rows without changing protected evidence fields."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=JUDGMENT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def judgment_metadata(
    query_id: str,
    query_text: str,
    candidate: PooledCandidate,
) -> dict[str, str]:
    """Render every protected reviewer-visible field for one candidate."""
    return {
        "query_id": query_id,
        "query_text": spreadsheet_safe_text(query_text),
        "document_id": candidate.document_id,
        "document_hash": candidate.document_hash,
        "source": candidate.source,
        "title": spreadsheet_safe_text(candidate.title),
        "occurred_at": candidate.occurred_at.isoformat(),
        "document_text": spreadsheet_safe_text(candidate.document_text),
        "citation_status": candidate.citation_status,
        "citation_url": spreadsheet_safe_text(candidate.citation_url or ""),
    }


def _parse_judgment_rows(
    pool: CandidatePool,
    csv_text: str,
    *,
    require_complete: bool,
) -> tuple[list[dict[str, str]], dict[JudgmentKey, ParsedGrade]]:
    reader = csv.DictReader(io.StringIO(csv_text, newline=""))
    if tuple(reader.fieldnames or ()) != JUDGMENT_COLUMNS:
        raise ValueError(f"judgment sheet columns must exactly equal {JUDGMENT_COLUMNS}")

    expected = {
        (pooled_query.query.query_id, candidate.document_id): (
            pooled_query.query.text,
            candidate,
        )
        for pooled_query in pool.queries
        for candidate in pooled_query.candidates
    }
    rows: list[dict[str, str]] = []
    grades: dict[JudgmentKey, ParsedGrade] = {}
    for line_number, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"judgment row {line_number} contains unexpected or missing columns")
        key = (row["query_id"], row["document_id"])
        if key not in expected:
            raise ValueError(f"judgment row {line_number} references an unknown candidate")
        if key in grades:
            raise ValueError(f"judgment row {line_number} duplicates {key[0]} / {key[1]}")
        query_text, candidate = expected[key]
        metadata = judgment_metadata(key[0], query_text, candidate)
        changed = [field for field, value in metadata.items() if row[field] != value]
        if changed:
            raise ValueError(
                f"judgment row {line_number} changed protected fields: {', '.join(changed)}"
            )

        raw_relevance = row["relevance_0_to_3"].strip()
        if not raw_relevance and not require_complete:
            relevance = None
        else:
            try:
                relevance = int(raw_relevance)
            except ValueError as error:
                raise ValueError(
                    f"judgment row {line_number} requires an integer relevance grade"
                ) from error
            if relevance not in {0, 1, 2, 3}:
                raise ValueError(f"judgment row {line_number} relevance must be within [0, 3]")

        rationale = " ".join(row["rationale"].split()) or None
        if rationale is not None and len(rationale) > 1_000:
            raise ValueError(f"judgment row {line_number} rationale exceeds 1000 characters")
        rows.append(dict(row))
        grades[key] = (relevance, rationale)

    missing = sorted(set(expected) - set(grades))
    if missing:
        raise ValueError(f"judgment sheet is missing {len(missing)} candidate(s)")
    return rows, grades


def validate_partial_judgments(
    pool: CandidatePool,
    csv_text: str,
) -> tuple[dict[str, str], ...]:
    """Validate a resumable sheet while permitting blank relevance grades."""
    if pool.judgment_status != "unjudged":
        raise ValueError("only an unjudged pool can accept a partial judgment sheet")
    rows, _grades = _parse_judgment_rows(pool, csv_text, require_complete=False)
    return tuple(rows)


def apply_judgments(
    pool: CandidatePool,
    csv_text: str,
    *,
    reviewer: str,
    reviewed_at: datetime | None = None,
) -> CandidatePool:
    """Validate a completed blind sheet and return a fully reviewed immutable artifact."""
    if pool.judgment_status != "unjudged":
        raise ValueError("only an unjudged pool can accept a judgment sheet")
    normalized_reviewer = " ".join(reviewer.split())
    if not normalized_reviewer:
        raise ValueError("reviewer must not be empty")

    _rows, grades = _parse_judgment_rows(pool, csv_text, require_complete=True)

    data = pool.model_dump(mode="python")
    for pooled_query in data["queries"]:
        query_id = pooled_query["query"]["query_id"]
        for candidate in pooled_query["candidates"]:
            relevance, rationale = grades[(query_id, candidate["document_id"])]
            if relevance is None:  # pragma: no cover - require_complete guarantees this
                raise AssertionError("complete judgment parsing returned a blank relevance grade")
            candidate["relevance"] = relevance
            candidate["rationale"] = rationale
    data.update(
        judgment_status="reviewed",
        reviewer=normalized_reviewer,
        reviewed_at=reviewed_at or datetime.now(UTC),
    )
    return CandidatePool.model_validate(data)

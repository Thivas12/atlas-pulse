"""Rank-blind CSV export and strict human-judgment import."""

from __future__ import annotations

import csv
import io
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


def _csv_safe(value: str) -> str:
    """Neutralize public text that spreadsheet software could treat as a formula."""
    stripped = value.lstrip()
    return f"'{value}" if stripped.startswith(("=", "+", "-", "@")) else value


def export_judgments(pool: CandidatePool) -> str:
    """Create a reviewer sheet that deliberately excludes modes, scores, and ranks."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=JUDGMENT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for pooled_query in pool.queries:
        for candidate in pooled_query.candidates:
            writer.writerow(
                {
                    "query_id": pooled_query.query.query_id,
                    "query_text": _csv_safe(pooled_query.query.text),
                    "document_id": candidate.document_id,
                    "document_hash": candidate.document_hash,
                    "source": candidate.source,
                    "title": _csv_safe(candidate.title),
                    "occurred_at": candidate.occurred_at.isoformat(),
                    "document_text": _csv_safe(candidate.document_text),
                    "citation_status": candidate.citation_status,
                    "citation_url": _csv_safe(candidate.citation_url or ""),
                    "relevance_0_to_3": "" if candidate.relevance is None else candidate.relevance,
                    "rationale": _csv_safe(candidate.rationale or ""),
                }
            )
    return output.getvalue()


def _candidate_metadata(query_id: str, candidate: PooledCandidate) -> dict[str, str]:
    return {
        "query_id": query_id,
        "document_id": candidate.document_id,
        "document_hash": candidate.document_hash,
        "source": candidate.source,
        "title": _csv_safe(candidate.title),
        "occurred_at": candidate.occurred_at.isoformat(),
        "document_text": _csv_safe(candidate.document_text),
        "citation_status": candidate.citation_status,
        "citation_url": _csv_safe(candidate.citation_url or ""),
    }


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
    grades: dict[tuple[str, str], tuple[int, str | None]] = {}
    for line_number, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(f"judgment row {line_number} contains unexpected columns")
        key = (row["query_id"], row["document_id"])
        if key not in expected:
            raise ValueError(f"judgment row {line_number} references an unknown candidate")
        if key in grades:
            raise ValueError(f"judgment row {line_number} duplicates {key[0]} / {key[1]}")
        query_text, candidate = expected[key]
        metadata = _candidate_metadata(key[0], candidate)
        metadata["query_text"] = _csv_safe(query_text)
        changed = [field for field, value in metadata.items() if row[field] != value]
        if changed:
            raise ValueError(
                f"judgment row {line_number} changed protected fields: {', '.join(changed)}"
            )
        try:
            relevance = int(row["relevance_0_to_3"])
        except ValueError as error:
            raise ValueError(
                f"judgment row {line_number} requires an integer relevance grade"
            ) from error
        if relevance not in {0, 1, 2, 3}:
            raise ValueError(f"judgment row {line_number} relevance must be within [0, 3]")
        rationale = " ".join(row["rationale"].split()) or None
        if rationale is not None and len(rationale) > 1_000:
            raise ValueError(f"judgment row {line_number} rationale exceeds 1000 characters")
        grades[key] = (relevance, rationale)

    missing = sorted(set(expected) - set(grades))
    if missing:
        raise ValueError(f"judgment sheet is missing {len(missing)} candidate(s)")

    data = pool.model_dump(mode="python")
    for pooled_query in data["queries"]:
        query_id = pooled_query["query"]["query_id"]
        for candidate in pooled_query["candidates"]:
            relevance, rationale = grades[(query_id, candidate["document_id"])]
            candidate["relevance"] = relevance
            candidate["rationale"] = rationale
    data.update(
        judgment_status="reviewed",
        reviewer=normalized_reviewer,
        reviewed_at=reviewed_at or datetime.now(UTC),
    )
    return CandidatePool.model_validate(data)

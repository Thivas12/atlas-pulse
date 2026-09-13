"""Rank-blind CSV export and strict human-judgment import."""

from __future__ import annotations

import csv
import io
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


def _csv_safe(value: str) -> str:
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
                    "query_text": _csv_safe(pooled_query.query.text),
                    "document_id": candidate.document_id,
                    "document_hash": candidate.document_hash,
                    "source": candidate.source,
                    "title": _csv_safe(candidate.title),
                    "occurred_at": candidate.occurred_at.isoformat(),
                    "document_text": _csv_safe(candidate.document_text),
                    "citation_status": candidate.citation_status,
                    "citation_url": _csv_safe(candidate.citation_url or ""),
                    "relevance_0_to_3": "" if relevance is None else relevance,
                    "rationale": _csv_safe(rationale or ""),
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

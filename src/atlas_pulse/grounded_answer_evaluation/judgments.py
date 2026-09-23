"""Model-blind human review sheets for grounded-answer candidate outputs."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from atlas_pulse.grounded_answer_evaluation.base import (
    DEVELOPMENT_GROUNDED_ANSWER_REVIEW_CAVEATS,
    GROUNDING_RUBRIC_VERSION,
    GroundedAnswerCandidateBatch,
    GroundedAnswerDevelopmentReviewProvenance,
    GroundedAnswerEvidence,
    GroundedAnswerJudgment,
    GroundedAnswerReviewAssistance,
    GroundedAnswerTask,
    ReviewedGroundedAnswerBatch,
    grounded_answer_review_sha256,
)

GROUNDING_REVIEW_COLUMNS = (
    "task_id",
    "task_sha256",
    "batch_id",
    "batch_sha256",
    "case_id",
    "query_id",
    "question_json",
    "response_status",
    "abstention_reason",
    "answer_json",
    "claim_id",
    "claim_text_json",
    "evidence_ids",
    "evidence_json",
    "support_0_to_3",
    "citation_quality_0_to_2",
    "answer_relevance_0_to_2",
    "abstention_appropriate",
    "rationale",
)

_PROTECTED_COLUMNS = GROUNDING_REVIEW_COLUMNS[:14]


@dataclass(frozen=True, slots=True)
class GroundedAnswerReviewSheet:
    """Portable model-blind CSV plus its number of pending judgments."""

    content: str
    pending_count: int


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _evidence_json(items: Sequence[GroundedAnswerEvidence]) -> str:
    return _compact_json(
        [
            {
                "evidence_id": item.evidence_id,
                "source": item.source,
                "event_id": item.event_id,
                "occurred_at": item.occurred_at.isoformat(),
                "text": item.text,
                "text_sha256": item.text_sha256,
                "citation_url": item.citation_url,
            }
            for item in items
        ]
    )


def _validate_pair(task: GroundedAnswerTask, batch: GroundedAnswerCandidateBatch) -> None:
    if batch.task_id != task.task_id or batch.task_sha256 != task.task_sha256:
        raise ValueError("grounded-answer candidate batch does not match the exact task")
    if [(case.case_id, case.query_id) for case in batch.cases] != [
        (case.case_id, case.query_id) for case in task.cases
    ]:
        raise ValueError("grounded-answer candidate batch does not exactly cover the task")


def _review_rows(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
) -> list[dict[str, str]]:
    _validate_pair(task, batch)
    task_cases = {case.case_id: case for case in task.cases}
    rows: list[dict[str, str]] = []
    for candidate_case in batch.cases:
        task_case = task_cases[candidate_case.case_id]
        response = candidate_case.response
        answer_json = _compact_json(response.model_dump(mode="json", exclude_none=False))
        if response.status == "abstained":
            available_evidence = tuple(task_case.evidence)
            rows.append(
                {
                    "task_id": task.task_id,
                    "task_sha256": task.task_sha256,
                    "batch_id": batch.batch_id,
                    "batch_sha256": batch.batch_sha256,
                    "case_id": candidate_case.case_id,
                    "query_id": candidate_case.query_id,
                    "question_json": _compact_json(task_case.question),
                    "response_status": response.status,
                    "abstention_reason": response.abstention_reason or "",
                    "answer_json": answer_json,
                    "claim_id": "",
                    "claim_text_json": "",
                    "evidence_ids": ";".join(item.evidence_id for item in available_evidence),
                    "evidence_json": _evidence_json(available_evidence),
                    "support_0_to_3": "",
                    "citation_quality_0_to_2": "",
                    "answer_relevance_0_to_2": "",
                    "abstention_appropriate": "",
                    "rationale": "",
                }
            )
            continue
        evidence = {item.evidence_id: item for item in task_case.evidence}
        for claim in response.claims:
            cited = [evidence[evidence_id] for evidence_id in claim.evidence_ids]
            evidence_json = _evidence_json(cited)
            rows.append(
                {
                    "task_id": task.task_id,
                    "task_sha256": task.task_sha256,
                    "batch_id": batch.batch_id,
                    "batch_sha256": batch.batch_sha256,
                    "case_id": candidate_case.case_id,
                    "query_id": candidate_case.query_id,
                    "question_json": _compact_json(task_case.question),
                    "response_status": response.status,
                    "abstention_reason": "",
                    "answer_json": answer_json,
                    "claim_id": claim.claim_id,
                    "claim_text_json": _compact_json(claim.text),
                    "evidence_ids": ";".join(claim.evidence_ids),
                    "evidence_json": evidence_json,
                    "support_0_to_3": "",
                    "citation_quality_0_to_2": "",
                    "answer_relevance_0_to_2": "",
                    "abstention_appropriate": "",
                    "rationale": "",
                }
            )
    return sorted(
        rows,
        key=lambda row: hashlib.sha256(
            f"{task.task_sha256}:{batch.batch_sha256}:{row['case_id']}:{row['claim_id']}".encode()
        ).hexdigest(),
    )


def grounded_answer_review_rows(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
) -> tuple[dict[str, str], ...]:
    """Return canonical reviewer-visible rows for protected downstream workflows."""
    return tuple(_review_rows(task, batch))


def build_grounded_answer_review_sheet(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
) -> GroundedAnswerReviewSheet:
    """Render candidate/evidence rows without exposing model identity or scoring labels."""
    rows = _review_rows(task, batch)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=GROUNDING_REVIEW_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return GroundedAnswerReviewSheet(content=output.getvalue(), pending_count=len(rows))


def _optional_grade(row: dict[str, str], column: str, maximum: int) -> int | None:
    raw = row[column].strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{column} must be a whole number between 0 and {maximum}") from error
    if not 0 <= value <= maximum:
        raise ValueError(f"{column} must be between 0 and {maximum}")
    return value


def _optional_bool(row: dict[str, str], column: str) -> bool | None:
    raw = row[column].strip().casefold()
    if not raw:
        return None
    if raw not in {"yes", "no"}:
        raise ValueError(f"{column} must be yes or no")
    return raw == "yes"


def apply_grounded_answer_review(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
    csv_text: str,
    *,
    reviewer: str,
    reviewed_at: datetime | None = None,
) -> ReviewedGroundedAnswerBatch:
    """Import one complete model-blind review while protecting every evidence/output field."""
    expected_rows = _review_rows(task, batch)
    expected = {(row["case_id"], row["claim_id"]): row for row in expected_rows}
    reader = csv.DictReader(io.StringIO(csv_text, newline=""))
    if tuple(reader.fieldnames or ()) != GROUNDING_REVIEW_COLUMNS:
        raise ValueError("grounded-answer review CSV header does not match the exact template")
    actual_rows = list(reader)
    actual: dict[tuple[str, str], dict[str, str]] = {}
    for row in actual_rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("grounded-answer review CSV contains malformed rows")
        identity = (row["case_id"], row["claim_id"])
        if identity in actual:
            raise ValueError("grounded-answer review CSV contains duplicate rows")
        actual[identity] = row
    missing = set(expected) - set(actual)
    unknown = set(actual) - set(expected)
    if missing or unknown:
        raise ValueError(
            f"grounded-answer review must exactly cover the template; "
            f"missing={len(missing)}, unknown={len(unknown)}"
        )
    judgments: list[GroundedAnswerJudgment] = []
    for identity, expected_row in expected.items():
        row = actual[identity]
        if any(row[column] != expected_row[column] for column in _PROTECTED_COLUMNS):
            raise ValueError(
                "grounded-answer review changed protected task, answer, or evidence fields"
            )
        claim_id = row["claim_id"] or None
        judgments.append(
            GroundedAnswerJudgment(
                case_id=row["case_id"],
                claim_id=claim_id,
                support_grade=_optional_grade(row, "support_0_to_3", 3),
                citation_quality=_optional_grade(row, "citation_quality_0_to_2", 2),
                answer_relevance=_optional_grade(row, "answer_relevance_0_to_2", 2),
                abstention_appropriate=_optional_bool(row, "abstention_appropriate"),
                rationale=row["rationale"],
            )
        )
    timestamp = reviewed_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("grounded-answer review timestamp must be timezone-aware")
    normalized_reviewer = " ".join(reviewer.split())
    if len(normalized_reviewer) < 2:
        raise ValueError("reviewer identity must contain at least two characters")
    ordered = tuple(sorted(judgments, key=lambda item: (item.case_id, item.claim_id or "")))
    draft = ReviewedGroundedAnswerBatch.model_construct(
        schema_version="1.0.0",
        review_id="grounded-review-" + "0" * 20,
        review_sha256="0" * 64,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        batch_id=batch.batch_id,
        batch_sha256=batch.batch_sha256,
        rubric_version=GROUNDING_RUBRIC_VERSION,
        reviewer=normalized_reviewer,
        reviewed_at=timestamp,
        judgment_count=len(ordered),
        judgments=ordered,
        review_status="first_pass_complete",
        promotion_status="blocked",
    )
    digest = grounded_answer_review_sha256(draft)
    return ReviewedGroundedAnswerBatch(
        review_id=f"grounded-review-{digest[:20]}",
        review_sha256=digest,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        batch_id=batch.batch_id,
        batch_sha256=batch.batch_sha256,
        reviewer=normalized_reviewer,
        reviewed_at=timestamp,
        judgment_count=len(ordered),
        judgments=ordered,
    )


def apply_grounded_answer_development_review(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
    csv_text: str,
    *,
    reviewer: str,
    review_assistance: GroundedAnswerReviewAssistance,
    reviewed_at: datetime | None = None,
) -> ReviewedGroundedAnswerBatch:
    """Import one declared single review without creating independent-review evidence."""
    first_pass = apply_grounded_answer_review(
        task,
        batch,
        csv_text,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
    )
    provenance = GroundedAnswerDevelopmentReviewProvenance(
        reviewer=first_pass.reviewer,
        reviewed_at=first_pass.reviewed_at,
        task_sha256=task.task_sha256,
        batch_sha256=batch.batch_sha256,
        review_assistance=review_assistance,
    )
    draft = ReviewedGroundedAnswerBatch.model_construct(
        schema_version="1.2.0",
        review_id="grounded-review-" + "0" * 20,
        review_sha256="0" * 64,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        batch_id=batch.batch_id,
        batch_sha256=batch.batch_sha256,
        rubric_version=GROUNDING_RUBRIC_VERSION,
        reviewer=first_pass.reviewer,
        reviewed_at=first_pass.reviewed_at,
        judgment_count=first_pass.judgment_count,
        judgments=first_pass.judgments,
        review_status="single_review_development_complete",
        adjudication=None,
        development_review=provenance,
        promotion_status="blocked",
        caveats=DEVELOPMENT_GROUNDED_ANSWER_REVIEW_CAVEATS,
    )
    digest = grounded_answer_review_sha256(draft)
    return ReviewedGroundedAnswerBatch(
        schema_version="1.2.0",
        review_id=f"grounded-review-{digest[:20]}",
        review_sha256=digest,
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        batch_id=batch.batch_id,
        batch_sha256=batch.batch_sha256,
        reviewer=first_pass.reviewer,
        reviewed_at=first_pass.reviewed_at,
        judgment_count=first_pass.judgment_count,
        judgments=first_pass.judgments,
        review_status="single_review_development_complete",
        development_review=provenance,
        caveats=DEVELOPMENT_GROUNDED_ANSWER_REVIEW_CAVEATS,
    )

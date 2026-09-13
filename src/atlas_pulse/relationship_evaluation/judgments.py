"""Prediction-blind CSV export and strict relationship-label import."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from atlas_pulse.relationship_evaluation.base import (
    RELATIONSHIP_LABELS,
    RelationshipCase,
    RelationshipPool,
)
from atlas_pulse.relationships import RelationshipLabel

JUDGMENT_COLUMNS = (
    "case_id",
    "predicate",
    "source_pair",
    "edge_id",
    "distance_km",
    "time_delta_minutes",
    "left_geometry_basis",
    "right_geometry_basis",
    "left_node_id",
    "left_source",
    "left_event_id",
    "left_event_type",
    "left_occurred_at",
    "left_document_hash",
    "left_document_text",
    "right_node_id",
    "right_source",
    "right_event_id",
    "right_event_type",
    "right_occurred_at",
    "right_document_hash",
    "right_document_text",
    "gold_label",
    "rationale",
)


@dataclass(frozen=True, slots=True)
class RelationshipJudgmentSheet:
    """Blinded review CSV plus exact prior-label reuse counts."""

    content: str
    reused_count: int
    pending_count: int


def _csv_safe(value: str) -> str:
    """Neutralize public source text that spreadsheets could execute as a formula."""
    stripped = value.lstrip()
    return f"'{value}" if stripped.startswith(("=", "+", "-", "@")) else value


def _protected_metadata(case: RelationshipCase) -> dict[str, str]:
    return {
        "case_id": case.case_id,
        "predicate": case.predicate,
        "source_pair": "+".join(case.source_pair),
        "edge_id": _csv_safe(case.edge_id),
        "distance_km": format(case.distance_km, ".12g"),
        "time_delta_minutes": format(case.time_delta_minutes, ".12g"),
        "left_geometry_basis": case.left_geometry_basis,
        "right_geometry_basis": case.right_geometry_basis,
        "left_node_id": _csv_safe(case.left.node_id),
        "left_source": case.left.source,
        "left_event_id": _csv_safe(case.left.event_id),
        "left_event_type": _csv_safe(case.left.event_type),
        "left_occurred_at": case.left.occurred_at.isoformat(),
        "left_document_hash": case.left.document_hash,
        "left_document_text": _csv_safe(case.left.document_text),
        "right_node_id": _csv_safe(case.right.node_id),
        "right_source": case.right.source,
        "right_event_id": _csv_safe(case.right.event_id),
        "right_event_type": _csv_safe(case.right.event_type),
        "right_occurred_at": case.right.occurred_at.isoformat(),
        "right_document_hash": case.right.document_hash,
        "right_document_text": _csv_safe(case.right.document_text),
    }


def build_relationship_judgment_sheet(
    pool: RelationshipPool,
    *,
    seed_pool: RelationshipPool | None = None,
) -> RelationshipJudgmentSheet:
    """Hide system output and reuse only exact prior human-visible evidence labels."""
    reusable: dict[str, RelationshipCase] = {}
    if seed_pool is not None:
        if seed_pool.judgment_status != "reviewed":
            raise ValueError("a relationship judgment seed must be fully reviewed")
        if (
            seed_pool.benchmark_id != pool.benchmark_id
            or seed_pool.benchmark_sha256 != pool.benchmark_sha256
            or seed_pool.rubric_version != pool.rubric_version
            or seed_pool.predicates != pool.predicates
        ):
            raise ValueError("relationship judgment reuse requires the same benchmark and rubric")
        reusable = {case.case_id: case for case in seed_pool.cases}

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=JUDGMENT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    reused_count = 0
    pending_count = 0
    for case in pool.cases:
        prior = reusable.get(case.case_id)
        if prior is not None and prior.review_identity() == case.review_identity():
            gold_label = prior.gold_label
            rationale = prior.rationale
            reused_count += 1
        else:
            gold_label = case.gold_label
            rationale = case.rationale
            pending_count += int(gold_label is None)
        writer.writerow(
            {
                **_protected_metadata(case),
                "gold_label": gold_label or "",
                "rationale": _csv_safe(rationale or ""),
            }
        )
    return RelationshipJudgmentSheet(
        content=output.getvalue(),
        reused_count=reused_count,
        pending_count=pending_count,
    )


def export_relationship_judgments(pool: RelationshipPool) -> str:
    """Create a reviewer sheet that contains no system label, claim, basis, or rationale."""
    return build_relationship_judgment_sheet(pool).content


def apply_relationship_judgments(
    pool: RelationshipPool,
    csv_text: str,
    *,
    reviewer: str,
    reviewed_at: datetime | None = None,
) -> RelationshipPool:
    """Validate a complete blind sheet and attach its human gold labels to the pool."""
    if pool.judgment_status != "unjudged":
        raise ValueError("only an unjudged relationship pool can accept a review sheet")
    normalized_reviewer = " ".join(reviewer.split())
    if not normalized_reviewer:
        raise ValueError("reviewer must not be empty")

    reader = csv.DictReader(io.StringIO(csv_text, newline=""))
    if tuple(reader.fieldnames or ()) != JUDGMENT_COLUMNS:
        raise ValueError(f"relationship judgment columns must exactly equal {JUDGMENT_COLUMNS}")

    expected = {case.case_id: case for case in pool.cases}
    judgments: dict[str, tuple[RelationshipLabel, str | None]] = {}
    allowed = set(RELATIONSHIP_LABELS)
    for line_number, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(f"relationship judgment row {line_number} has unexpected columns")
        case_id = row["case_id"]
        case = expected.get(case_id)
        if case is None:
            raise ValueError(f"relationship judgment row {line_number} has an unknown case")
        if case_id in judgments:
            raise ValueError(f"relationship judgment row {line_number} duplicates {case_id}")
        metadata = _protected_metadata(case)
        changed = [field for field, value in metadata.items() if row[field] != value]
        if changed:
            raise ValueError(
                f"relationship judgment row {line_number} changed protected fields: "
                + ", ".join(changed)
            )
        label_text = row["gold_label"].strip()
        if label_text not in allowed:
            raise ValueError(
                f"relationship judgment row {line_number} requires one of {sorted(allowed)}"
            )
        rationale = " ".join(row["rationale"].split()) or None
        if rationale is not None and len(rationale) > 1_000:
            raise ValueError(
                f"relationship judgment row {line_number} rationale exceeds 1000 characters"
            )
        judgments[case_id] = (cast(RelationshipLabel, label_text), rationale)

    missing = sorted(set(expected) - set(judgments))
    if missing:
        raise ValueError(f"relationship judgment sheet is missing {len(missing)} case(s)")

    data = pool.model_dump(mode="python")
    for case in data["cases"]:
        label, rationale = judgments[case["case_id"]]
        case["gold_label"] = label
        case["rationale"] = rationale
    data.update(
        judgment_status="reviewed",
        reviewer=normalized_reviewer,
        reviewed_at=reviewed_at or datetime.now(UTC),
    )
    return RelationshipPool.model_validate(data)

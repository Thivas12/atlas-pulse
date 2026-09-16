"""Independent retrieval review agreement and adjudication tests."""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas_pulse.evaluation import (
    ADJUDICATION_COLUMNS,
    CandidatePool,
    CapturedRun,
    EvaluationFilters,
    EvaluationQuery,
    IndependentRetrievalAdjudicationProvenance,
    IndependentRetrievalReviewAgreementReport,
    PooledCandidate,
    PooledQuery,
    RetrievalAdjudicationDecision,
    RetrievalAdjudicationReport,
    RetrievalReviewAgreementMetrics,
    RetrievalReviewDisagreement,
    ReviewedRetrievalPoolIdentity,
    apply_judgments,
    apply_retrieval_adjudication,
    build_retrieval_adjudication_sheet,
    canonical_sha256,
    compare_independent_retrieval_reviews,
    export_judgments,
    render_retrieval_adjudication_markdown,
    render_retrieval_adjudication_rows,
    render_retrieval_review_agreement_markdown,
    retrieval_capture_sha256,
    run_retrieval_adjudication_session,
    score_pool,
    validate_partial_retrieval_adjudication,
)
from atlas_pulse.evaluation.cli import run_cli
from atlas_pulse.evaluation.judgments import JUDGMENT_COLUMNS
from atlas_pulse.evaluation.report import render_markdown
from atlas_pulse.projections.base import SourceName

NOW = datetime(2026, 9, 16, 6, tzinfo=UTC)


def _query(query_id: str, *, source: SourceName, slices: tuple[str, ...]) -> EvaluationQuery:
    return EvaluationQuery(
        query_id=query_id,
        text=f"operational evidence for {query_id}",
        slices=slices,
        filters=EvaluationFilters(source=source, active_only=False),
    )


def _candidate(source: SourceName, event_id: str) -> PooledCandidate:
    text = f"Evidence from {source} for {event_id}"
    return PooledCandidate(
        document_id=f"{source}:{event_id}",
        source=source,
        event_id=event_id,
        title=f"Event {event_id}",
        occurred_at=NOW,
        document_text=text,
        document_hash=hashlib.sha256(text.encode()).hexdigest(),
        citation_status="traceable",
        citation_url=f"https://example.com/{source}/{event_id}",
    )


def _pooled_query(
    query: EvaluationQuery,
    candidates: tuple[PooledCandidate, ...],
) -> PooledQuery:
    return PooledQuery(
        query=query,
        candidates=candidates,
        runs=(
            CapturedRun(
                mode="hybrid",
                ranking_rule="rrf60-evidence-tiebreak-v2",
                embedding_model="test/model",
                latency_ms=12,
                document_ids=tuple(candidate.document_id for candidate in candidates),
            ),
        ),
    )


def _unjudged_pool() -> CandidatePool:
    return CandidatePool(
        pool_id="retrieval-review-20260916t060000z",
        query_set_id="retrieval-review-v1",
        query_set_sha256="a" * 64,
        captured_at=NOW,
        endpoint="https://atlas.example",
        queries=(
            _pooled_query(
                _query("storm-safety", source="nws", slices=("weather", "safety")),
                (_candidate("nws", "alert-a"), _candidate("nws", "alert-b")),
            ),
            _pooled_query(
                _query("quake-impact", source="usgs", slices=("geospatial", "safety")),
                (_candidate("usgs", "quake-a"), _candidate("usgs", "quake-b")),
            ),
        ),
    )


def _reviewed_pool(
    reviewer: str,
    grades: tuple[int, ...],
    *,
    reviewed_at: datetime,
) -> CandidatePool:
    source = _unjudged_pool()
    rows = list(csv.DictReader(io.StringIO(export_judgments(source))))
    assert len(rows) == len(grades)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=JUDGMENT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for index, (row, grade) in enumerate(zip(rows, grades, strict=True)):
        row["relevance_0_to_3"] = str(grade)
        row["rationale"] = f"Reviewer rationale {index} for grade {grade}."
        writer.writerow(row)
    return apply_judgments(
        source,
        output.getvalue(),
        reviewer=reviewer,
        reviewed_at=reviewed_at,
    )


def _independent_reviews() -> tuple[CandidatePool, CandidatePool]:
    return (
        _reviewed_pool("Alice Reviewer", (3, 1, 0, 2), reviewed_at=NOW + timedelta(hours=1)),
        _reviewed_pool("Bob Reviewer", (3, 2, 0, 1), reviewed_at=NOW + timedelta(hours=2)),
    )


def _completed_adjudication_sheet(
    first: CandidatePool,
    second: CandidatePool,
) -> str:
    rows = list(
        csv.DictReader(io.StringIO(build_retrieval_adjudication_sheet(first, second).content))
    )
    for row in rows:
        row["adjudicated_relevance_0_to_3"] = "2"
        row["adjudication_rationale"] = "The evidence supports a useful but incomplete result."
    return render_retrieval_adjudication_rows(rows)


def test_independent_review_agreement_is_canonical_sliced_and_blocked() -> None:
    first, second = _independent_reviews()

    report = compare_independent_retrieval_reviews(first, second, generated_at=NOW)
    reversed_report = compare_independent_retrieval_reviews(second, first, generated_at=NOW)

    assert report == reversed_report
    assert report.first_review.reviewer == "Alice Reviewer"
    assert report.second_review.reviewer == "Bob Reviewer"
    assert report.capture_sha256 == retrieval_capture_sha256(first)
    assert report.first_review.pool_sha256 == canonical_sha256(first)
    assert report.second_review.pool_sha256 == canonical_sha256(second)
    assert report.overall.judgment_count == 4
    assert report.overall.agreement_count == 2
    assert report.overall.disagreement_count == 2
    assert report.overall.observed_agreement == 0.5
    assert report.overall.expected_agreement == 0.25
    assert report.overall.cohen_kappa == pytest.approx(1 / 3)
    assert report.queries["storm-safety"].observed_agreement == 0.5
    assert report.sources["nws"].judgment_count == 2
    assert report.slices["safety"].judgment_count == 4
    assert report.confusion_matrix[1][2] == 1
    assert report.confusion_matrix[2][1] == 1
    assert [(item.query_id, item.document_id) for item in report.disagreements] == [
        ("quake-impact", "usgs:quake-b"),
        ("storm-safety", "nws:alert-b"),
    ]
    assert report.promotion_status == "blocked"

    markdown = render_retrieval_review_agreement_markdown(report)
    assert "Cohen's kappa" in markdown
    assert "`storm-safety`" in markdown
    assert "Promotion status: `blocked`" in markdown


def test_blind_adjudication_session_finalizes_gold_and_scoring(tmp_path: Path) -> None:
    first, second = _independent_reviews()
    sheet = build_retrieval_adjudication_sheet(first, second)
    rows = list(csv.DictReader(io.StringIO(sheet.content)))

    assert sheet.pending_count == 2
    assert tuple(rows[0]) == ADJUDICATION_COLUMNS
    assert "Alice Reviewer" not in sheet.content
    assert "Bob Reviewer" not in sheet.content
    assert "hybrid" not in sheet.content
    assert "ranking" not in sheet.content
    for row in rows:
        assert {
            row["review_a_relevance_0_to_3"],
            row["review_b_relevance_0_to_3"],
        } == {"1", "2"}

    path = tmp_path / "adjudication.csv"
    path.write_text(sheet.content, encoding="utf-8")
    first_answers = iter(
        (
            "?",
            "9",
            "2",
            "short",
            "Evidence is useful but does not directly answer the need.",
            "q",
        )
    )
    output = io.StringIO()
    progress = run_retrieval_adjudication_session(
        first,
        second,
        path,
        prompt=lambda _message: next(first_answers),
        output=output,
    )
    assert progress.graded_count == 1
    assert progress.pending_count == 1
    assert progress.stopped_early is True
    assert "Invalid choice" in output.getvalue()
    assert "10 to 1000" in output.getvalue()
    assert "Alice Reviewer" not in output.getvalue()
    assert not tuple(tmp_path.glob(".adjudication.csv.*.tmp"))

    second_answers = iter(
        (
            "1",
            "Evidence is related but operationally weak for this query.",
        )
    )
    resumed = run_retrieval_adjudication_session(
        first,
        second,
        path,
        prompt=lambda _message: next(second_answers),
        output=io.StringIO(),
    )
    assert resumed.pending_count == 0
    assert len(validate_partial_retrieval_adjudication(first, second, path.read_text())) == 2

    finalized_at = NOW + timedelta(hours=3)
    gold, adjudication = apply_retrieval_adjudication(
        first,
        second,
        path.read_text(encoding="utf-8"),
        adjudicator="Casey Adjudicator",
        adjudicated_at=finalized_at,
    )
    assert gold.schema_version == "1.1.0"
    assert gold.reviewer == "Casey Adjudicator"
    assert gold.reviewed_at == finalized_at
    assert gold.adjudication is not None
    assert gold.adjudication.independent_reviewers == (
        "Alice Reviewer",
        "Bob Reviewer",
    )
    assert gold.adjudication.review_pool_sha256s == (
        canonical_sha256(first),
        canonical_sha256(second),
    )
    assert gold.adjudication.adjudication_decision_count == 2
    assert retrieval_capture_sha256(gold) == retrieval_capture_sha256(first)
    assert CandidatePool.model_validate_json(gold.model_dump_json()) == gold
    assert adjudication.final_pool_sha256 == canonical_sha256(gold)
    assert adjudication.inherited_agreement_count == 2
    assert adjudication.adjudication_decision_count == 2
    assert adjudication.promotion_status == "blocked"

    report = score_pool(gold, cutoffs=(1, 2))
    assert report.schema_version == "1.2.0"
    assert report.adjudication == gold.adjudication
    assert "two independent reviews" in report.caveats[-1]
    scored_markdown = render_markdown(report)
    assert "Review process: `independent-review-adjudication-v1`" in scored_markdown
    assert "Adjudicated disagreements: `2`" in scored_markdown
    adjudication_markdown = render_retrieval_adjudication_markdown(adjudication)
    assert "Final pool SHA-256" in adjudication_markdown
    assert "Casey Adjudicator" in adjudication_markdown


def test_adjudication_rejects_identity_drift_tampering_and_invalid_decisions() -> None:
    first, second = _independent_reviews()

    with pytest.raises(ValueError, match="different reviewer"):
        compare_independent_retrieval_reviews(first, first)

    changed = second.model_copy(update={"endpoint": "https://different.example"})
    with pytest.raises(ValueError, match="exact same captured pool"):
        compare_independent_retrieval_reviews(first, changed)

    completed = _completed_adjudication_sheet(first, second)
    rows = list(csv.DictReader(io.StringIO(completed)))

    tampered = [dict(row) for row in rows]
    tampered[0]["title"] = "Changed title"
    with pytest.raises(ValueError, match="changed protected fields"):
        apply_retrieval_adjudication(
            first,
            second,
            render_retrieval_adjudication_rows(tampered),
            adjudicator="Casey Adjudicator",
        )

    invalid = [dict(row) for row in rows]
    invalid[0]["adjudicated_relevance_0_to_3"] = "4"
    with pytest.raises(ValueError, match=r"within \[0, 3\]"):
        apply_retrieval_adjudication(
            first,
            second,
            render_retrieval_adjudication_rows(invalid),
            adjudicator="Casey Adjudicator",
        )

    short = [dict(row) for row in rows]
    short[0]["adjudication_rationale"] = "too short"
    with pytest.raises(ValueError, match="at least 10"):
        apply_retrieval_adjudication(
            first,
            second,
            render_retrieval_adjudication_rows(short),
            adjudicator="Casey Adjudicator",
        )

    with pytest.raises(ValueError, match="missing 1 disagreement"):
        apply_retrieval_adjudication(
            first,
            second,
            render_retrieval_adjudication_rows(rows[:-1]),
            adjudicator="Casey Adjudicator",
        )
    with pytest.raises(ValueError, match="duplicates"):
        apply_retrieval_adjudication(
            first,
            second,
            render_retrieval_adjudication_rows([*rows, rows[0]]),
            adjudicator="Casey Adjudicator",
        )
    with pytest.raises(ValueError, match="independent from both reviewers"):
        apply_retrieval_adjudication(
            first,
            second,
            completed,
            adjudicator="alice reviewer",
        )

    partial = list(
        csv.DictReader(io.StringIO(build_retrieval_adjudication_sheet(first, second).content))
    )
    partial[0]["adjudication_rationale"] = "A rationale without any final grade."
    with pytest.raises(ValueError, match="without a grade"):
        validate_partial_retrieval_adjudication(
            first,
            second,
            render_retrieval_adjudication_rows(partial),
        )


def test_unanimous_and_degenerate_reviews_support_header_only_adjudication(
    tmp_path: Path,
) -> None:
    first = _reviewed_pool(
        "Alice Reviewer",
        (0, 0, 0, 0),
        reviewed_at=NOW + timedelta(hours=1),
    )
    second = _reviewed_pool(
        "Bob Reviewer",
        (0, 0, 0, 0),
        reviewed_at=NOW + timedelta(hours=2),
    )

    agreement = compare_independent_retrieval_reviews(first, second, generated_at=NOW)
    sheet = build_retrieval_adjudication_sheet(first, second)

    assert agreement.overall.observed_agreement == 1
    assert agreement.overall.expected_agreement == 1
    assert agreement.overall.cohen_kappa is None
    assert agreement.disagreements == ()
    assert sheet.pending_count == 0
    assert tuple(csv.DictReader(io.StringIO(sheet.content))) == ()
    assert "No relevance-grade disagreements" in render_retrieval_review_agreement_markdown(
        agreement
    )

    sheet_path = tmp_path / "header-only.csv"
    sheet_path.write_text(sheet.content, encoding="utf-8")
    progress = run_retrieval_adjudication_session(
        first,
        second,
        sheet_path,
        prompt=lambda _message: pytest.fail("header-only adjudication must not prompt"),
        output=io.StringIO(),
    )
    assert progress.total_count == 0
    assert progress.pending_count == 0

    gold, report = apply_retrieval_adjudication(
        first,
        second,
        sheet.content,
        adjudicator="Casey Adjudicator",
        adjudicated_at=NOW + timedelta(hours=3),
    )
    assert report.adjudication_decision_count == 0
    assert report.inherited_agreement_count == 4
    assert all(
        candidate.relevance == 0
        for pooled_query in gold.queries
        for candidate in pooled_query.candidates
    )
    assert "No disagreements" in render_retrieval_adjudication_markdown(report)


def test_contracts_reject_invalid_review_and_adjudication_provenance() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        ReviewedRetrievalPoolIdentity(
            reviewer=" ",
            reviewed_at=NOW,
            pool_sha256="a" * 64,
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        ReviewedRetrievalPoolIdentity(
            reviewer="Reviewer",
            reviewed_at=datetime(2026, 9, 16),
            pool_sha256="a" * 64,
        )
    with pytest.raises(ValidationError, match="cover every judgment"):
        RetrievalReviewAgreementMetrics(
            judgment_count=2,
            agreement_count=2,
            disagreement_count=1,
            observed_agreement=1,
            expected_agreement=0.5,
            cohen_kappa=1,
        )
    with pytest.raises(ValidationError, match="observed_agreement"):
        RetrievalReviewAgreementMetrics(
            judgment_count=2,
            agreement_count=1,
            disagreement_count=1,
            observed_agreement=1,
            expected_agreement=0.5,
            cohen_kappa=1,
        )
    with pytest.raises(ValidationError, match="must be undefined"):
        RetrievalReviewAgreementMetrics(
            judgment_count=2,
            agreement_count=2,
            disagreement_count=0,
            observed_agreement=1,
            expected_agreement=1,
            cohen_kappa=1,
        )
    with pytest.raises(ValidationError, match="must match observed"):
        RetrievalReviewAgreementMetrics(
            judgment_count=2,
            agreement_count=1,
            disagreement_count=1,
            observed_agreement=0.5,
            expected_agreement=0.25,
            cohen_kappa=0.9,
        )
    with pytest.raises(ValidationError, match="different relevance"):
        RetrievalReviewDisagreement(
            query_id="query",
            document_id="nws:event",
            source="nws",
            first_relevance=2,
            second_relevance=2,
        )
    with pytest.raises(ValidationError, match="originate from a disagreement"):
        RetrievalAdjudicationDecision(
            query_id="query",
            document_id="nws:event",
            source="nws",
            first_relevance=2,
            second_relevance=2,
            adjudicated_relevance=2,
            rationale="This rationale is sufficiently long.",
        )

    invalid = _unjudged_pool().model_dump(mode="python")
    invalid["schema_version"] = "1.1.0"
    with pytest.raises(ValidationError, match="requires adjudication provenance"):
        CandidatePool.model_validate(invalid)

    provenance = IndependentRetrievalAdjudicationProvenance(
        independent_reviewers=("Alice Reviewer", "Bob Reviewer"),
        review_pool_sha256s=("a" * 64, "b" * 64),
        agreement_report_id="agreement-id",
        observed_agreement=0.5,
        cohen_kappa=0.3,
        adjudicator="Casey Adjudicator",
        adjudicated_at=NOW,
        adjudication_decision_count=1,
    )
    invalid = _reviewed_pool(
        "Alice Reviewer",
        (3, 1, 0, 2),
        reviewed_at=NOW,
    ).model_dump(mode="python")
    invalid.update(schema_version="1.1.0", adjudication=provenance.model_dump(mode="python"))
    with pytest.raises(ValidationError, match="reviewer must equal"):
        CandidatePool.model_validate(invalid)


def test_agreement_report_contract_rejects_corrupt_derived_evidence() -> None:
    first, second = _independent_reviews()
    report = compare_independent_retrieval_reviews(first, second, generated_at=NOW)

    def validate(update: dict[str, object]) -> None:
        data = report.model_dump(mode="python")
        data.update(update)
        IndependentRetrievalReviewAgreementReport.model_validate(data)

    with pytest.raises(ValidationError, match="generated_at"):
        validate({"generated_at": datetime(2026, 9, 16)})

    same_reviewers = report.second_review.model_dump(mode="python")
    same_reviewers["reviewer"] = report.first_review.reviewer
    with pytest.raises(ValidationError, match="different reviewers"):
        validate({"second_review": same_reviewers})

    with pytest.raises(ValidationError, match="canonical reviewer order"):
        validate(
            {
                "first_review": report.second_review.model_dump(mode="python"),
                "second_review": report.first_review.model_dump(mode="python"),
            }
        )

    missing_grade = {grade: dict(row) for grade, row in report.confusion_matrix.items()}
    missing_grade.pop(3)
    with pytest.raises(ValidationError, match="every relevance grade"):
        validate({"confusion_matrix": missing_grade})

    negative = {grade: dict(row) for grade, row in report.confusion_matrix.items()}
    negative[0][0] = -1
    with pytest.raises(ValidationError, match="non-negative"):
        validate({"confusion_matrix": negative})

    wrong_count = {grade: dict(row) for grade, row in report.confusion_matrix.items()}
    wrong_count[0][0] += 1
    with pytest.raises(ValidationError, match="cover every judgment"):
        validate({"confusion_matrix": wrong_count})

    wrong_diagonal = {grade: dict(row) for grade, row in report.confusion_matrix.items()}
    wrong_diagonal[0][0] = 0
    wrong_diagonal[0][1] = 1
    with pytest.raises(ValidationError, match="diagonal"):
        validate({"confusion_matrix": wrong_diagonal})

    wrong_expected = report.overall.model_copy(
        update={
            "expected_agreement": 0.3,
            "cohen_kappa": (0.5 - 0.3) / (1 - 0.3),
        }
    )
    with pytest.raises(ValidationError, match="marginals"):
        validate({"overall": wrong_expected.model_dump(mode="python")})

    with pytest.raises(ValidationError, match="canonically ordered"):
        validate({"disagreements": tuple(reversed(report.disagreements))})
    with pytest.raises(ValidationError, match="retain every disagreement"):
        validate({"disagreements": report.disagreements[:1]})

    wrong_disagreement = report.disagreements[0].model_copy(
        update={"first_relevance": 0, "second_relevance": 3}
    )
    with pytest.raises(ValidationError, match="off-diagonal"):
        validate({"disagreements": (wrong_disagreement, report.disagreements[1])})

    with pytest.raises(ValidationError, match="query agreement groups"):
        validate({"queries": {"storm-safety": report.queries["storm-safety"]}})
    with pytest.raises(ValidationError, match="source agreement groups"):
        validate({"sources": {"nws": report.sources["nws"]}})

    oversized_slice = RetrievalReviewAgreementMetrics(
        judgment_count=5,
        agreement_count=5,
        disagreement_count=0,
        observed_agreement=1,
        expected_agreement=0.25,
        cohen_kappa=1,
    )
    with pytest.raises(ValidationError, match="slice agreement count"):
        validate({"slices": {"oversized": oversized_slice.model_dump(mode="python")}})
    with pytest.raises(ValidationError, match="promotion boundary"):
        validate({"caveats": ("changed",)})


def test_adjudication_report_and_import_reject_corrupt_contracts() -> None:
    first, second = _independent_reviews()
    completed = _completed_adjudication_sheet(first, second)
    gold, report = apply_retrieval_adjudication(
        first,
        second,
        completed,
        adjudicator="Casey Adjudicator",
        adjudicated_at=NOW + timedelta(hours=3),
    )

    def validate(update: dict[str, object]) -> None:
        data = report.model_dump(mode="python")
        data.update(update)
        RetrievalAdjudicationReport.model_validate(data)

    with pytest.raises(ValidationError, match="generated_at"):
        validate({"generated_at": datetime(2026, 9, 16)})
    with pytest.raises(ValidationError, match="independent from both reviewers"):
        validate({"adjudicator": "Alice Reviewer"})
    with pytest.raises(ValidationError, match="canonical reviewer order"):
        validate(
            {
                "first_review": report.second_review.model_dump(mode="python"),
                "second_review": report.first_review.model_dump(mode="python"),
            }
        )
    with pytest.raises(ValidationError, match="cover every judgment"):
        validate({"inherited_agreement_count": 1})
    with pytest.raises(ValidationError, match="retain every decision"):
        validate({"decisions": report.decisions[:1]})
    with pytest.raises(ValidationError, match="canonically ordered"):
        validate({"decisions": tuple(reversed(report.decisions))})
    with pytest.raises(ValidationError, match="promotion boundary"):
        validate({"caveats": ("changed",)})

    rows = list(csv.DictReader(io.StringIO(completed)))
    non_integer = [dict(row) for row in rows]
    non_integer[0]["adjudicated_relevance_0_to_3"] = "two"
    with pytest.raises(ValueError, match="integer relevance"):
        apply_retrieval_adjudication(
            first,
            second,
            render_retrieval_adjudication_rows(non_integer),
            adjudicator="Casey Adjudicator",
        )
    too_long = [dict(row) for row in rows]
    too_long[0]["adjudication_rationale"] = "x" * 1_001
    with pytest.raises(ValueError, match="exceeds 1000"):
        apply_retrieval_adjudication(
            first,
            second,
            render_retrieval_adjudication_rows(too_long),
            adjudicator="Casey Adjudicator",
        )
    with pytest.raises(ValueError, match="columns must exactly equal"):
        apply_retrieval_adjudication(
            first,
            second,
            "query_id\n",
            adjudicator="Casey Adjudicator",
        )
    unknown = [dict(row) for row in rows]
    unknown[0]["query_id"] = "unknown-query"
    with pytest.raises(ValueError, match="not a known disagreement"):
        apply_retrieval_adjudication(
            first,
            second,
            render_retrieval_adjudication_rows(unknown),
            adjudicator="Casey Adjudicator",
        )
    with pytest.raises(ValueError, match="adjudicator must not be empty"):
        apply_retrieval_adjudication(first, second, completed, adjudicator=" ")
    with pytest.raises(ValueError, match="must be fully reviewed"):
        compare_independent_retrieval_reviews(_unjudged_pool(), second)
    with pytest.raises(ValueError, match="first-pass review"):
        compare_independent_retrieval_reviews(gold, second)


def test_adjudication_terminal_handles_skips_and_interruptions(tmp_path: Path) -> None:
    first, second = _independent_reviews()
    sheet = build_retrieval_adjudication_sheet(first, second)
    path = tmp_path / "skipped.csv"
    path.write_text(sheet.content, encoding="utf-8")
    answers = iter(("s", "s"))
    output = io.StringIO()

    skipped = run_retrieval_adjudication_session(
        first,
        second,
        path,
        prompt=lambda _message: next(answers),
        output=output,
    )

    assert skipped.pending_count == 2
    assert skipped.stopped_early is False
    assert "reached the end" in output.getvalue()

    def interrupt(_message: str) -> str:
        raise KeyboardInterrupt

    interrupted = run_retrieval_adjudication_session(
        first,
        second,
        path,
        prompt=interrupt,
        output=io.StringIO(),
    )
    assert interrupted.stopped_early is True

    prompts = 0

    def interrupt_rationale(_message: str) -> str:
        nonlocal prompts
        prompts += 1
        if prompts == 1:
            return "2"
        raise EOFError

    rationale_interrupted = run_retrieval_adjudication_session(
        first,
        second,
        path,
        prompt=interrupt_rationale,
        output=io.StringIO(),
    )
    assert rationale_interrupted.stopped_early is True


def test_cli_runs_second_review_agreement_adjudication_and_final_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unjudged = _unjudged_pool()
    first, second = _independent_reviews()
    pool_path = tmp_path / "pool.json"
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    second_sheet = tmp_path / "second.csv"
    pool_path.write_text(unjudged.model_dump_json(indent=2), encoding="utf-8")
    first_path.write_text(first.model_dump_json(indent=2), encoding="utf-8")
    second_path.write_text(second.model_dump_json(indent=2), encoding="utf-8")

    assert (
        run_cli(
            [
                "review-sheet",
                "--pool",
                str(pool_path),
                "--output",
                str(second_sheet),
            ]
        )
        == 0
    )
    assert len(tuple(csv.DictReader(io.StringIO(second_sheet.read_text())))) == 4
    assert run_cli(["review-sheet", "--pool", str(pool_path), "--output", str(pool_path)]) == 2

    agreement_json = tmp_path / "agreement.json"
    agreement_md = tmp_path / "agreement.md"
    adjudication_sheet = tmp_path / "adjudication.csv"
    assert (
        run_cli(
            [
                "agreement",
                "--first-pool",
                str(first_path),
                "--second-pool",
                str(second_path),
                "--output-json",
                str(agreement_json),
                "--output-markdown",
                str(agreement_md),
                "--adjudication-output",
                str(adjudication_sheet),
            ]
        )
        == 0
    )
    assert "| Overall | 4 | 2 | 2 |" in agreement_md.read_text()

    answers = iter(
        (
            "2",
            "Evidence is relevant but incomplete for the requested operation.",
            "2",
            "Evidence is relevant but incomplete for the requested operation.",
        )
    )
    monkeypatch.setattr("builtins.input", lambda _message: next(answers))
    assert (
        run_cli(
            [
                "judge-adjudication",
                "--first-pool",
                str(first_path),
                "--second-pool",
                str(second_path),
                "--adjudication-sheet",
                str(adjudication_sheet),
            ]
        )
        == 0
    )

    gold_path = tmp_path / "gold.json"
    adjudication_json = tmp_path / "adjudication.json"
    adjudication_md = tmp_path / "adjudication.md"
    assert (
        run_cli(
            [
                "adjudicate",
                "--first-pool",
                str(first_path),
                "--second-pool",
                str(second_path),
                "--adjudication-sheet",
                str(adjudication_sheet),
                "--adjudicator",
                "Casey Adjudicator",
                "--output-pool",
                str(gold_path),
                "--output-json",
                str(adjudication_json),
                "--output-markdown",
                str(adjudication_md),
            ]
        )
        == 0
    )
    gold = CandidatePool.model_validate_json(gold_path.read_text())
    assert gold.adjudication is not None

    score_json = tmp_path / "score.json"
    score_md = tmp_path / "score.md"
    assert (
        run_cli(
            [
                "score",
                "--pool",
                str(gold_path),
                "--output-json",
                str(score_json),
                "--output-markdown",
                str(score_md),
            ]
        )
        == 0
    )
    assert '"schema_version": "1.2.0"' in score_json.read_text()
    assert "Independent reviewers" in score_md.read_text()

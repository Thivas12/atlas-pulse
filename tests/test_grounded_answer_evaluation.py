"""Gold-free grounded-answer evaluation and fail-closed review tests."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from agent_rag_core import Event
from pydantic import ValidationError

import atlas_pulse.api as api
import atlas_pulse.grounded_answer_evaluation.cli as grounded_cli
from atlas_pulse.evidence_packs import (
    EvidencePack,
    EvidencePackBudget,
    build_evidence_pack,
    validate_evidence_pack_identity,
)
from atlas_pulse.grounded_answer_evaluation import (
    GROUNDING_ADJUDICATION_COLUMNS,
    GROUNDING_REVIEW_COLUMNS,
    GroundedAnswerAdjudicationReport,
    GroundedAnswerBenchmark,
    GroundedAnswerCandidateBatch,
    GroundedAnswerCaseOutcome,
    GroundedAnswerClaim,
    GroundedAnswerEvaluationReport,
    GroundedAnswerEvidence,
    GroundedAnswerJudgment,
    GroundedAnswerMetricSummary,
    GroundedAnswerQuery,
    GroundedAnswerResponse,
    GroundedAnswerSubmission,
    GroundedAnswerSubmissionCase,
    GroundedAnswerTask,
    GroundedAnswerTaskCase,
    ReviewedGroundedAnswerBatch,
    apply_grounded_answer_adjudication,
    apply_grounded_answer_review,
    apply_grounded_answer_submission,
    build_grounded_answer_adjudication_sheet,
    build_grounded_answer_review_sheet,
    build_grounded_answer_submission,
    capture_grounded_answer_task,
    compare_grounded_answer_reviews,
    grounded_answer_batch_sha256,
    grounded_answer_case_sha256,
    grounded_answer_report_sha256,
    grounded_answer_review_sha256,
    grounded_answer_task_sha256,
    render_grounded_answer_adjudication_markdown,
    render_grounded_answer_agreement_markdown,
    render_grounded_answer_markdown,
    score_grounded_answer_review,
)
from atlas_pulse.relationship_evaluation.candidates import CandidateSystemDefinition
from atlas_pulse.retrieval import (
    CitationValidation,
    RankingExplanation,
    SearchHit,
    SearchQuery,
    SearchResult,
)
from atlas_pulse.retrieval.ranking import RANKING_RULE, RETRIEVAL_CAVEAT
from atlas_pulse.streams import StreamMessage

CAPTURED_AT = datetime(2026, 9, 14, 4, tzinfo=UTC)
GENERATED_AT = datetime(2026, 9, 14, 5, tzinfo=UTC)
REVIEWED_AT = datetime(2026, 9, 14, 6, tzinfo=UTC)
REPORTED_AT = datetime(2026, 9, 14, 7, tzinfo=UTC)
SECOND_REVIEWED_AT = datetime(2026, 9, 14, 6, 30, tzinfo=UTC)
ADJUDICATED_AT = datetime(2026, 9, 14, 8, tzinfo=UTC)


def _benchmark() -> GroundedAnswerBenchmark:
    return GroundedAnswerBenchmark(
        benchmark_id="test-grounded-briefs-v1",
        title="Grounded answer contract test",
        description="Two cases exercise cited claims and a required evidence-empty abstention.",
        retrieval_limit=5,
        candidate_limit=20,
        max_items=3,
        max_characters_per_item=200,
        max_total_characters=500,
        queries=(
            GroundedAnswerQuery(
                query_id="available-evidence",
                question="What protective action is stated?",
                slices=("action", "weather"),
                filters={"source": "nws", "active_only": False},
            ),
            GroundedAnswerQuery(
                query_id="empty-evidence",
                question="What road restriction is stated?",
                slices=("abstention", "weather"),
                filters={"source": "nws", "active_only": False},
            ),
        ),
    )


def _search_result(query: SearchQuery, *, with_hit: bool) -> SearchResult:
    hits: tuple[SearchHit, ...] = ()
    if with_hit:
        event = Event(
            event_id="alert-1",
            event_type="weather.alert",
            source="nws",
            occurred_at=datetime(2026, 9, 14, 3, tzinfo=UTC),
            ingested_at=datetime(2026, 9, 14, 3, 1, tzinfo=UTC),
            payload={"title": "Shelter warning"},
        )
        hit = SearchHit(
            message=StreamMessage(stream_id="100-1", event=event),
            document_text=(
                "Title: Shelter warning\nInstruction: Residents in Example County should "
                "shelter indoors until 18:00 UTC."
            ),
            distance_km=None,
            ranking=RankingExplanation(
                lexical_rank=1,
                lexical_score=0.9,
                dense_rank=1,
                dense_similarity=0.8,
                rrf_score=0.95,
                exact_phrase_match=True,
                token_coverage=1.0,
                rerank_score=0.95,
            ),
            citation=CitationValidation(
                status="traceable",
                url="https://api.weather.gov/alerts/alert-1",
                source_field="source_url",
                reasons=("public_http_url",),
            ),
        )
        hits = (hit,)
    return SearchResult(
        hits=hits,
        candidates_considered=1 if hits else 0,
        embedding_model="test/local-embedding",
        ranking_mode=query.ranking_mode,
        ranking_rule=RANKING_RULE,
        caveat=RETRIEVAL_CAVEAT,
    )


def _pack(benchmark: GroundedAnswerBenchmark, query: GroundedAnswerQuery) -> EvidencePack:
    production = query.filters.search_query(
        text=query.question,
        limit=benchmark.retrieval_limit,
        candidate_limit=benchmark.candidate_limit,
        ranking_mode="hybrid",
    )
    return build_evidence_pack(
        _search_result(production, with_hit=query.query_id == "available-evidence"),
        production,
        budget=EvidencePackBudget(
            max_items=benchmark.max_items,
            max_characters_per_item=benchmark.max_characters_per_item,
            max_total_characters=benchmark.max_total_characters,
        ),
    )


def _capture_client(
    benchmark: GroundedAnswerBenchmark,
    *,
    mutate: Callable[[dict[str, object]], None] | None = None,
) -> httpx.AsyncClient:
    packs = {query.question: _pack(benchmark, query) for query in benchmark.queries}
    queries = {query.question: query for query in benchmark.queries}

    def handler(request: httpx.Request) -> httpx.Response:
        question = request.url.params["q"]
        filters = queries[question].filters
        optional_parameters = {
            "min_magnitude": filters.min_magnitude,
            "max_depth_km": filters.max_depth_km,
            "alert_type": filters.alert_type,
            "min_confidence_rank": filters.min_confidence_rank,
            "observation_period": filters.observation_period,
        }
        for key, value in optional_parameters.items():
            if value is not None:
                assert request.url.params[key] == str(value)
        if filters.tsunami is not None:
            assert request.url.params["tsunami"] == str(filters.tsunami).lower()
        response = api._evidence_pack_response(packs[question]).model_dump(mode="json")
        if mutate is not None:
            mutate(response)
        return httpx.Response(200, json=response)

    return httpx.AsyncClient(
        base_url="http://atlas.test",
        transport=httpx.MockTransport(handler),
    )


def _task() -> GroundedAnswerTask:
    benchmark = _benchmark()

    async def build() -> GroundedAnswerTask:
        async with _capture_client(benchmark) as client:
            return await capture_grounded_answer_task(
                benchmark,
                base_url="http://atlas.test/",
                client=client,
                captured_at=CAPTURED_AT,
            )

    return asyncio.run(build())


def _system(**parameters: str | int | float | bool) -> CandidateSystemDefinition:
    values: dict[str, str | int | float | bool] = {
        "context_length": 4096,
        "max_output_tokens": 256,
        "tokenizer_artifact_sha256": "c" * 64,
        "temperature": 0.7,
        "seed": 42,
    }
    values.update(parameters)
    return CandidateSystemDefinition(
        candidate_id="local-grounded-brief-v1",
        model_id="example/revision-pinned-generator",
        model_revision="a" * 40,
        model_artifact_sha256="b" * 64,
        adapter_version="grounded-brief-adapter-v1",
        input_template_sha256="d" * 64,
        runtime="llama.cpp-cpu",
        runtime_version="build-1234",
        parameters=values,
    )


def _completed_submission(task: GroundedAnswerTask) -> GroundedAnswerSubmission:
    cases: list[GroundedAnswerSubmissionCase] = []
    for case in task.cases:
        if case.pack_status == "no_traceable_evidence":
            response = GroundedAnswerResponse(
                status="abstained",
                abstention_reason="no_traceable_evidence",
            )
            output_tokens = 4
        else:
            evidence_id = case.evidence[0].evidence_id
            response = GroundedAnswerResponse(
                status="answered",
                claims=(
                    GroundedAnswerClaim(
                        claim_id="claim-01",
                        text="Residents in Example County should shelter indoors.",
                        evidence_ids=(evidence_id,),
                    ),
                    GroundedAnswerClaim(
                        claim_id="claim-02",
                        text="The instruction remains in effect until 18:00 UTC.",
                        evidence_ids=(evidence_id,),
                    ),
                ),
            )
            output_tokens = 24
        cases.append(
            GroundedAnswerSubmissionCase(
                case_id=case.case_id,
                query_id=case.query_id,
                response=response,
                input_tokens=120,
                output_tokens=output_tokens,
                latency_ms=20 if response.status == "answered" else 10,
            )
        )
    return GroundedAnswerSubmission(
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        cases=tuple(cases),
    )


def _batch(task: GroundedAnswerTask) -> GroundedAnswerCandidateBatch:
    return apply_grounded_answer_submission(
        task,
        _completed_submission(task),
        system=_system(),
        generated_at=GENERATED_AT,
    )


def _completed_review_csv(task: GroundedAnswerTask, batch: GroundedAnswerCandidateBatch) -> str:
    template = build_grounded_answer_review_sheet(task, batch).content
    rows = list(csv.DictReader(io.StringIO(template)))
    for row in rows:
        if row["claim_id"] == "claim-01":
            row["support_0_to_3"] = "3"
            row["citation_quality_0_to_2"] = "2"
            row["answer_relevance_0_to_2"] = "2"
            row["rationale"] = "The cited alert directly supports this atomic statement."
        elif row["claim_id"] == "claim-02":
            row["support_0_to_3"] = "1"
            row["citation_quality_0_to_2"] = "1"
            row["rationale"] = (
                "The excerpt does not establish that the instruction is still current."
            )
        else:
            row["abstention_appropriate"] = "yes"
            row["rationale"] = "No traceable evidence was available for this question."
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=GROUNDING_REVIEW_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _review(
    task: GroundedAnswerTask, batch: GroundedAnswerCandidateBatch
) -> ReviewedGroundedAnswerBatch:
    return apply_grounded_answer_review(
        task,
        batch,
        _completed_review_csv(task, batch),
        reviewer="Alex Reviewer",
        reviewed_at=REVIEWED_AT,
    )


def _second_review(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
    *,
    disagree: bool = True,
) -> ReviewedGroundedAnswerBatch:
    rows = list(csv.DictReader(io.StringIO(_completed_review_csv(task, batch))))
    for row in rows:
        row["rationale"] = "A second independent reading applied the same bounded rubric."
        if not disagree:
            continue
        if row["claim_id"] == "claim-01":
            row["support_0_to_3"] = "2"
            row["answer_relevance_0_to_2"] = "1"
        elif row["claim_id"] == "claim-02":
            row["citation_quality_0_to_2"] = "2"
        else:
            row["abstention_appropriate"] = "no"
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=GROUNDING_REVIEW_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return apply_grounded_answer_review(
        task,
        batch,
        output.getvalue(),
        reviewer="Blair Reviewer",
        reviewed_at=SECOND_REVIEWED_AT,
    )


def _completed_adjudication_csv(content: str) -> str:
    rows = list(csv.DictReader(io.StringIO(content)))
    final_columns = {
        "support_grade": ("adjudicated_support_0_to_3", "3"),
        "citation_quality": ("adjudicated_citation_quality_0_to_2", "2"),
        "answer_relevance": ("adjudicated_answer_relevance_0_to_2", "2"),
        "abstention_appropriate": ("adjudicated_abstention_appropriate", "yes"),
    }
    for row in rows:
        for field in row["disputed_fields"].split(";"):
            column, value = final_columns[field]
            row[column] = value
        row["adjudication_rationale"] = (
            "The final grade follows the bounded evidence and the stated rubric."
        )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=GROUNDING_ADJUDICATION_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def test_capture_is_gold_free_content_addressed_and_replayable() -> None:
    task = _task()
    repeated = _task()

    assert task == repeated
    assert task.endpoint == "http://atlas.test"
    assert task.task_sha256 == grounded_answer_task_sha256(task)
    assert task.task_id == f"grounded-task-{task.task_sha256[:20]}"
    assert task.case_count == 2
    assert [case.case_id for case in task.cases] == sorted(case.case_id for case in task.cases)
    assert {case.pack_status for case in task.cases} == {
        "traceable_evidence_available",
        "no_traceable_evidence",
    }
    serialized = task.model_dump_json()
    assert '"reference_answer"' not in serialized.casefold()
    assert '"expected_answer"' not in serialized.casefold()
    assert "reviewer" not in serialized.casefold()
    available = next(case for case in task.cases if case.evidence)
    assert available.source_text_characters == sum(len(item.text) for item in available.evidence)
    assert available.evidence[0].citation_url == "https://api.weather.gov/alerts/alert-1"


def test_candidate_import_binds_citations_tokens_and_closed_boundary() -> None:
    task = _task()
    blank = build_grounded_answer_submission(task)
    assert all(case.response is None for case in blank.cases)
    batch = _batch(task)

    assert batch.batch_sha256 == grounded_answer_batch_sha256(batch)
    assert batch.batch_id == f"grounded-batch-{batch.batch_sha256[:20]}"
    assert batch.review_status == "awaiting_human_review"
    assert batch.promotion_status == "blocked"
    assert {case.response.status for case in batch.cases} == {"answered", "abstained"}
    assert all(case.input_tokens + 256 <= 4096 for case in batch.cases)


def test_model_blind_review_and_descriptive_score() -> None:
    task = _task()
    batch = _batch(task)
    sheet = build_grounded_answer_review_sheet(task, batch)
    assert sheet.pending_count == 3
    assert batch.system.model_id not in sheet.content
    assert batch.system.candidate_id not in sheet.content
    assert "question_json" in sheet.content and "evidence_json" in sheet.content

    review = _review(task, batch)
    assert review.review_sha256 == grounded_answer_review_sha256(review)
    assert review.review_id == f"grounded-review-{review.review_sha256[:20]}"
    assert review.review_status == "first_pass_complete"
    assert review.promotion_status == "blocked"

    report = score_grounded_answer_review(
        task,
        batch,
        review,
        generated_at=REPORTED_AT,
    )
    assert report.report_sha256 == grounded_answer_report_sha256(report)
    assert report.overall.case_count == 2
    assert report.overall.answered_case_count == 1
    assert report.overall.abstained_case_count == 1
    assert report.overall.claim_count == 2
    assert report.overall.mean_support_grade_0_to_3 == 2
    assert report.overall.fully_supported_claim_rate == 0.5
    assert report.overall.unsupported_or_contradicted_claim_rate == 0.5
    assert report.overall.mean_citation_quality_0_to_2 == 1.5
    assert report.overall.complete_citation_rate == 0.5
    assert report.overall.mean_answer_relevance_0_to_2 == 2
    assert report.overall.appropriate_abstention_rate == 1
    assert report.overall.strict_case_pass_rate == 0.5
    assert report.overall.p95_latency_ms == pytest.approx(19.5)
    assert report.slices["weather"].case_count == 2
    assert report.slices["action"].appropriate_abstention_rate is None
    assert report.slices["abstention"].claim_count == 0
    assert report.slices["abstention"].mean_support_grade_0_to_3 is None
    markdown = render_grounded_answer_markdown(report)
    assert "Promotion status: **BLOCKED**" in markdown
    assert "A second independent review" in markdown
    assert "p95 ms" in markdown


def test_independent_review_agreement_is_canonical_and_dimension_specific() -> None:
    task = _task()
    batch = _batch(task)
    first = _review(task, batch)
    second = _second_review(task, batch)

    report = compare_grounded_answer_reviews(second, first, generated_at=REPORTED_AT)
    reverse = compare_grounded_answer_reviews(first, second, generated_at=REPORTED_AT)
    assert report == reverse
    assert report.first_review.reviewer == "Alex Reviewer"
    assert report.second_review.reviewer == "Blair Reviewer"
    assert report.judgments.judgment_count == 3
    assert report.judgments.complete_agreement_count == 0
    assert report.judgments.disagreement_count == 3
    assert report.judgments.disputed_field_count == 4
    assert report.judgments.exact_judgment_agreement == 0
    assert report.dimensions["support_grade"].rating_count == 2
    assert report.dimensions["support_grade"].observed_agreement == 0.5
    assert report.dimensions["support_grade"].cohen_kappa == pytest.approx(1 / 3)
    assert report.dimensions["citation_quality"].observed_agreement == 0.5
    assert report.dimensions["citation_quality"].cohen_kappa == 0
    assert report.dimensions["answer_relevance"].observed_agreement == 0
    assert report.dimensions["abstention_appropriate"].observed_agreement == 0
    assert (
        sum(
            sum(row.values())
            for row in report.dimensions["support_grade"].confusion_matrix.values()
        )
        == 2
    )

    sheet = build_grounded_answer_adjudication_sheet(task, batch, second, first)
    assert sheet.pending_judgment_count == 3
    assert sheet.pending_field_count == 4
    assert batch.system.model_id not in sheet.content
    assert batch.system.candidate_id not in sheet.content
    assert first.reviewer not in sheet.content
    assert second.reviewer not in sheet.content
    rows = list(csv.DictReader(io.StringIO(sheet.content)))
    assert len(rows) == 3
    assert tuple(rows[0]) == GROUNDING_ADJUDICATION_COLUMNS
    assert all(row["agreement_report_id"] == sheet.agreement_report.report_id for row in rows)
    assert all(row["disputed_fields"] for row in rows)

    support_data = report.dimensions["support_grade"].model_dump(mode="python")
    with pytest.raises(ValidationError, match="observed agreement"):
        type(report.dimensions["support_grade"]).model_validate(
            {**support_data, "observed_agreement": 0.75}
        )
    with pytest.raises(ValidationError, match="task ID"):
        type(report).model_validate(
            {**report.model_dump(mode="python"), "task_id": "grounded-task-" + "0" * 20}
        )
    markdown = render_grounded_answer_agreement_markdown(report)
    assert "Rubric-field agreement" in markdown
    assert "support_grade" in markdown
    assert "Promotion status: **BLOCKED**" in markdown

    formula_rows = list(csv.DictReader(io.StringIO(_completed_review_csv(task, batch))))
    for row in formula_rows:
        row["rationale"] = "=FORMULA-LIKE reviewer rationale remains inert text"
    formula_csv = io.StringIO(newline="")
    writer = csv.DictWriter(formula_csv, fieldnames=GROUNDING_REVIEW_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(formula_rows)
    formula_review = apply_grounded_answer_review(
        task,
        batch,
        formula_csv.getvalue(),
        reviewer="Alex Reviewer",
        reviewed_at=REVIEWED_AT,
    )
    formula_sheet = build_grounded_answer_adjudication_sheet(task, batch, formula_review, second)
    assert "'=FORMULA-LIKE" in formula_sheet.content


def test_disagreement_only_adjudication_finalizes_a_blocked_review_and_score() -> None:
    task = _task()
    batch = _batch(task)
    first = _review(task, batch)
    second = _second_review(task, batch)
    sheet = build_grounded_answer_adjudication_sheet(task, batch, first, second)

    final_review, record = apply_grounded_answer_adjudication(
        task,
        batch,
        second,
        first,
        _completed_adjudication_csv(sheet.content),
        adjudicator="Casey Adjudicator",
        adjudicated_at=ADJUDICATED_AT,
    )
    assert final_review.schema_version == "1.1.0"
    assert final_review.review_status == "independent_adjudication_complete"
    assert final_review.promotion_status == "blocked"
    assert final_review.review_sha256 == grounded_answer_review_sha256(final_review)
    assert final_review.adjudication is not None
    assert final_review.adjudication.independent_reviewers == (
        "Alex Reviewer",
        "Blair Reviewer",
    )
    assert final_review.adjudication.adjudication_decision_count == 3
    assert final_review.adjudication.adjudicated_field_count == 4
    assert record.adjudicator == "Casey Adjudicator"
    assert record.adjudication_decision_count == 3
    assert record.adjudicated_field_count == 4
    assert record.inherited_agreement_count == 0
    assert record.final_review_id == final_review.review_id
    assert record.final_review_sha256 == final_review.review_sha256
    assert GroundedAnswerAdjudicationReport.model_validate_json(record.model_dump_json()) == record
    assert ReviewedGroundedAnswerBatch.model_validate_json(final_review.model_dump_json()) == (
        final_review
    )
    assert all(
        decision.final_judgment.rationale.startswith("The final grade")
        for decision in record.decisions
    )
    with pytest.raises(ValidationError, match="agreement ID"):
        GroundedAnswerAdjudicationReport.model_validate(
            {
                **record.model_dump(mode="python"),
                "agreement_report_id": "grounded-agreement-" + "0" * 20,
            }
        )
    partial_decision = next(
        decision
        for decision in record.decisions
        if "citation_quality" not in decision.disputed_fields
        and decision.final_judgment.citation_quality is not None
    )
    changed_final = partial_decision.final_judgment.model_copy(update={"citation_quality": 1})
    with pytest.raises(ValidationError, match="cannot change an agreed rubric field"):
        type(partial_decision).model_validate(
            {
                **partial_decision.model_dump(mode="python"),
                "final_judgment": changed_final.model_dump(mode="python"),
            }
        )

    scored = score_grounded_answer_review(
        task,
        batch,
        final_review,
        generated_at=datetime(2026, 9, 14, 9, tzinfo=UTC),
    )
    assert scored.schema_version == "1.1.0"
    assert scored.adjudication == final_review.adjudication
    assert scored.promotion_status == "blocked"
    assert not any(
        "second independent review" in item.casefold() for item in scored.promotion_blockers
    )
    assert any("representative live evidence" in item for item in scored.promotion_blockers)
    score_markdown = render_grounded_answer_markdown(scored)
    assert "Final adjudicated review" in score_markdown
    assert "Promotion status: **BLOCKED**" in score_markdown
    adjudication_markdown = render_grounded_answer_adjudication_markdown(record)
    assert "Disputed rows / fields resolved: 3 / 4" in adjudication_markdown
    assert "Casey Adjudicator" in adjudication_markdown


def test_complete_independent_agreement_needs_no_decision_rows() -> None:
    task = _task()
    batch = _batch(task)
    first = _review(task, batch)
    second = _second_review(task, batch, disagree=False)
    sheet = build_grounded_answer_adjudication_sheet(task, batch, first, second)

    assert sheet.pending_judgment_count == 0
    assert sheet.pending_field_count == 0
    assert list(csv.DictReader(io.StringIO(sheet.content))) == []
    assert all(
        value.observed_agreement == 1 for value in sheet.agreement_report.dimensions.values()
    )
    final_review, record = apply_grounded_answer_adjudication(
        task,
        batch,
        first,
        second,
        sheet.content,
        adjudicator="Casey Adjudicator",
        adjudicated_at=ADJUDICATED_AT,
    )
    assert record.inherited_agreement_count == 3
    assert record.adjudication_decision_count == 0
    assert record.adjudicated_field_count == 0
    assert record.decisions == ()
    assert final_review.adjudication is not None
    assert final_review.adjudication.adjudication_decision_count == 0
    assert all("Independent reviewers agreed" in item.rationale for item in final_review.judgments)
    assert "No disagreements" in render_grounded_answer_adjudication_markdown(record)


def test_grounded_answer_adjudication_fails_closed_on_identity_and_sheet_drift() -> None:
    task = _task()
    batch = _batch(task)
    first = _review(task, batch)
    second = _second_review(task, batch)
    sheet = build_grounded_answer_adjudication_sheet(task, batch, first, second)
    completed = _completed_adjudication_csv(sheet.content)

    same_person = apply_grounded_answer_review(
        task,
        batch,
        _completed_review_csv(task, batch),
        reviewer=" alex reviewer ",
        reviewed_at=SECOND_REVIEWED_AT,
    )
    with pytest.raises(ValueError, match="different reviewer identities"):
        compare_grounded_answer_reviews(first, same_person)
    with pytest.raises(ValueError, match="exact same task and candidate batch"):
        compare_grounded_answer_reviews(
            first,
            second.model_copy(update={"batch_sha256": "0" * 64}),
        )
    with pytest.raises(ValueError, match="independent from both reviewers"):
        apply_grounded_answer_adjudication(
            task,
            batch,
            first,
            second,
            completed,
            adjudicator="ALEX REVIEWER",
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        apply_grounded_answer_adjudication(
            task,
            batch,
            first,
            second,
            completed,
            adjudicator="Casey Adjudicator",
            adjudicated_at=datetime(2026, 9, 14, 8),
        )

    rows = list(csv.DictReader(io.StringIO(completed)))

    def encode(changed: list[dict[str, str]]) -> str:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output, fieldnames=GROUNDING_ADJUDICATION_COLUMNS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(changed)
        return output.getvalue()

    tampered = [dict(row) for row in rows]
    tampered[0]["question_json"] = '"changed"'
    with pytest.raises(ValueError, match="changed protected fields"):
        apply_grounded_answer_adjudication(
            task,
            batch,
            first,
            second,
            encode(tampered),
            adjudicator="Casey Adjudicator",
        )
    with pytest.raises(ValueError, match="missing 1 disagreement"):
        apply_grounded_answer_adjudication(
            task,
            batch,
            first,
            second,
            encode(rows[:-1]),
            adjudicator="Casey Adjudicator",
        )
    with pytest.raises(ValueError, match="duplicates a disagreement"):
        apply_grounded_answer_adjudication(
            task,
            batch,
            first,
            second,
            encode([*rows, rows[0]]),
            adjudicator="Casey Adjudicator",
        )

    support_row = next(row for row in rows if "support_grade" in row["disputed_fields"])
    invalid_grade = [dict(row) for row in rows]
    invalid_grade[rows.index(support_row)]["adjudicated_support_0_to_3"] = "4"
    with pytest.raises(ValueError, match="requires one of 0, 1, 2, 3"):
        apply_grounded_answer_adjudication(
            task,
            batch,
            first,
            second,
            encode(invalid_grade),
            adjudicator="Casey Adjudicator",
        )
    partial_row = next(
        row
        for row in rows
        if "support_grade" in row["disputed_fields"]
        and "citation_quality" not in row["disputed_fields"]
    )
    regraded = [dict(row) for row in rows]
    regraded[rows.index(partial_row)]["adjudicated_citation_quality_0_to_2"] = "2"
    with pytest.raises(ValueError, match="cannot regrade agreed field"):
        apply_grounded_answer_adjudication(
            task,
            batch,
            first,
            second,
            encode(regraded),
            adjudicator="Casey Adjudicator",
        )
    short_rationale = [dict(row) for row in rows]
    short_rationale[0]["adjudication_rationale"] = "short"
    with pytest.raises(ValueError, match="at least ten characters"):
        apply_grounded_answer_adjudication(
            task,
            batch,
            first,
            second,
            encode(short_rationale),
            adjudicator="Casey Adjudicator",
        )

    final_review, _ = apply_grounded_answer_adjudication(
        task,
        batch,
        first,
        second,
        completed,
        adjudicator="Casey Adjudicator",
        adjudicated_at=ADJUDICATED_AT,
    )
    with pytest.raises(ValueError, match="independent first pass"):
        compare_grounded_answer_reviews(first, final_review)


def test_review_sheet_json_quotes_formula_like_candidate_text() -> None:
    task = _task()
    submission = _completed_submission(task)
    changed = list(submission.cases)
    answered_index = next(
        index
        for index, case in enumerate(changed)
        if case.response is not None and case.response.status == "answered"
    )
    response = changed[answered_index].response
    assert response is not None
    formula_claim = response.claims[0].model_copy(update={"text": "=2+2"})
    changed[answered_index] = changed[answered_index].model_copy(
        update={
            "response": response.model_copy(
                update={"claims": (formula_claim, *response.claims[1:])}
            )
        }
    )
    batch = apply_grounded_answer_submission(
        task,
        submission.model_copy(update={"cases": tuple(changed)}),
        system=_system(),
        generated_at=GENERATED_AT,
    )

    rows = list(
        csv.DictReader(io.StringIO(build_grounded_answer_review_sheet(task, batch).content))
    )
    formula_cell = next(row["claim_text_json"] for row in rows if row["claim_id"] == "claim-01")
    assert formula_cell.startswith('"=')
    assert json.loads(formula_cell) == "=2+2"


def test_benchmark_and_response_contracts_reject_ambiguous_shapes() -> None:
    benchmark = _benchmark()
    base = benchmark.model_dump(mode="python")
    invalid_benchmarks = (
        ({**base, "candidate_limit": 4}, "at least retrieval_limit"),
        ({**base, "max_total_characters": 100}, "at least max_characters_per_item"),
        ({**base, "queries": tuple(reversed(benchmark.queries))}, "canonical query ID order"),
        ({**base, "queries": (benchmark.queries[0], benchmark.queries[0])}, "must be unique"),
    )
    for payload, message in invalid_benchmarks:
        with pytest.raises(ValidationError, match=message):
            GroundedAnswerBenchmark.model_validate(payload)

    with pytest.raises(ValidationError, match="claims and no abstention"):
        GroundedAnswerResponse(status="answered")
    with pytest.raises(ValidationError, match="one reason and no claims"):
        GroundedAnswerResponse(status="abstained")
    with pytest.raises(ValidationError, match="consecutive"):
        GroundedAnswerResponse(
            status="answered",
            claims=(
                GroundedAnswerClaim(
                    claim_id="claim-02",
                    text="A valid statement.",
                    evidence_ids=("evidence-" + "a" * 64,),
                ),
            ),
        )
    with pytest.raises(ValidationError, match="duplicate normalized text"):
        GroundedAnswerResponse(
            status="answered",
            claims=(
                GroundedAnswerClaim(
                    claim_id="claim-01",
                    text="Same claim.",
                    evidence_ids=("evidence-" + "a" * 64,),
                ),
                GroundedAnswerClaim(
                    claim_id="claim-02",
                    text=" same   claim. ",
                    evidence_ids=("evidence-" + "a" * 64,),
                ),
            ),
        )
    with pytest.raises(ValidationError, match="canonical order"):
        GroundedAnswerClaim(
            claim_id="claim-01",
            text="One claim.",
            evidence_ids=("evidence-" + "b" * 64, "evidence-" + "a" * 64),
        )


def test_nested_contracts_reject_invalid_evidence_claim_and_judgment_shapes() -> None:
    task = _task()
    available = next(case for case in task.cases if case.evidence)
    empty = next(case for case in task.cases if not case.evidence)
    evidence = available.evidence[0]

    with pytest.raises(ValidationError, match="timezone-aware"):
        GroundedAnswerEvidence.model_validate(
            {**evidence.model_dump(mode="python"), "occurred_at": datetime(2026, 9, 14, 3)}
        )
    with pytest.raises(ValidationError, match="text_sha256"):
        GroundedAnswerEvidence.model_validate(
            {**evidence.model_dump(mode="python"), "text_sha256": "0" * 64}
        )
    with pytest.raises(ValidationError, match="at least two characters"):
        GroundedAnswerClaim(
            claim_id="claim-01",
            text="  ",
            evidence_ids=(evidence.evidence_id,),
        )
    with pytest.raises(ValidationError, match="must be unique"):
        GroundedAnswerClaim(
            claim_id="claim-01",
            text="One valid claim.",
            evidence_ids=(evidence.evidence_id, evidence.evidence_id),
        )
    with pytest.raises(ValidationError, match="canonical evidence IDs"):
        GroundedAnswerClaim(
            claim_id="claim-01",
            text="One valid claim.",
            evidence_ids=("evidence-not-a-digest",),
        )
    with pytest.raises(ValidationError, match="canonical evidence IDs"):
        GroundedAnswerClaim(
            claim_id="claim-01",
            text="One valid claim.",
            evidence_ids=("evidence-" + "z" * 64,),
        )

    second = evidence.model_copy(
        update={"evidence_id": "evidence-" + "0" * 64, "retrieval_rank": evidence.retrieval_rank}
    )
    invalid_cases = (
        (available.model_copy(update={"evidence": (evidence, evidence)}), "IDs must be unique"),
        (
            available.model_copy(update={"evidence": (evidence, second)}),
            "unique retrieval order",
        ),
        (
            empty.model_copy(update={"pack_status": "traceable_evidence_available"}),
            "status must match",
        ),
        (
            available.model_copy(update={"source_text_characters": 0}),
            "must match included evidence",
        ),
        (available.model_copy(update={"case_id": "answer-case-" + "0" * 20}), "case_id"),
    )
    for changed_case, message in invalid_cases:
        with pytest.raises(ValueError, match=message):
            GroundedAnswerTaskCase.model_validate(changed_case.model_dump(mode="python"))

    judgment_base = {
        "case_id": available.case_id,
        "rationale": "A sufficiently detailed review rationale.",
    }
    invalid_judgments = (
        ({**judgment_base, "abstention_appropriate": None}, "abstention rows"),
        (
            {**judgment_base, "support_grade": 1, "abstention_appropriate": True},
            "abstention rows",
        ),
        ({**judgment_base, "claim_id": "claim-02"}, "support and citation"),
        (
            {
                **judgment_base,
                "claim_id": "claim-02",
                "support_grade": 3,
                "citation_quality": 2,
                "answer_relevance": 2,
            },
            "only the first claim",
        ),
        (
            {
                **judgment_base,
                "claim_id": "claim-01",
                "support_grade": 3,
                "citation_quality": 2,
                "answer_relevance": 2,
                "abstention_appropriate": False,
            },
            "must not contain abstention",
        ),
    )
    for payload, message in invalid_judgments:
        with pytest.raises(ValidationError, match=message):
            GroundedAnswerJudgment.model_validate(payload)
    with pytest.raises(ValidationError, match="at least ten characters"):
        GroundedAnswerJudgment(
            case_id=empty.case_id,
            abstention_appropriate=True,
            rationale="   too short   ",
        )


def test_content_addressed_container_validators_reject_internal_drift() -> None:
    task = _task()
    batch = _batch(task)
    review = _review(task, batch)
    report = score_grounded_answer_review(task, batch, review, generated_at=REPORTED_AT)

    duplicate_query_case = task.cases[1].model_copy(update={"query_id": task.cases[0].query_id})
    duplicate_query_digest = grounded_answer_case_sha256(duplicate_query_case)
    duplicate_query_case = duplicate_query_case.model_copy(
        update={"case_id": f"answer-case-{duplicate_query_digest[:20]}"}
    )
    duplicate_query_cases = tuple(
        sorted((task.cases[0], duplicate_query_case), key=lambda item: item.case_id)
    )

    invalid_tasks = (
        (task.model_copy(update={"captured_at": datetime(2026, 9, 14, 4)}), "timezone-aware"),
        (task.model_copy(update={"case_count": 3}), "case_count"),
        (task.model_copy(update={"cases": tuple(reversed(task.cases))}), "canonically ordered"),
        (
            task.model_copy(update={"cases": (task.cases[0], task.cases[0])}),
            "unique and canonically ordered",
        ),
        (
            task.model_copy(update={"cases": duplicate_query_cases}),
            "query IDs must be unique",
        ),
        (task.model_copy(update={"task_id": "grounded-task-" + "0" * 20}), "task_id"),
    )
    for changed_task, message in invalid_tasks:
        with pytest.raises(ValueError, match=message):
            GroundedAnswerTask.model_validate(changed_task.model_dump(mode="python"))

    invalid_batches = (
        (batch.model_copy(update={"generated_at": datetime(2026, 9, 14, 5)}), "timezone-aware"),
        (batch.model_copy(update={"case_count": 3}), "case_count"),
        (
            batch.model_copy(update={"cases": (batch.cases[0], batch.cases[0])}),
            "unique and ordered",
        ),
        (batch.model_copy(update={"caveats": ("changed",)}), "promotion boundary"),
        (batch.model_copy(update={"batch_id": "grounded-batch-" + "0" * 20}), "batch_id"),
    )
    for changed_batch, message in invalid_batches:
        with pytest.raises(ValueError, match=message):
            GroundedAnswerCandidateBatch.model_validate(changed_batch.model_dump(mode="python"))

    invalid_reviews = (
        (review.model_copy(update={"reviewed_at": datetime(2026, 9, 14, 6)}), "timezone-aware"),
        (review.model_copy(update={"judgment_count": 4}), "judgment_count"),
        (
            review.model_copy(update={"judgments": (review.judgments[0],) * 3}),
            "unique and canonically ordered",
        ),
        (review.model_copy(update={"caveats": ("changed",)}), "promotion boundary"),
        (review.model_copy(update={"review_id": "grounded-review-" + "0" * 20}), "review_id"),
    )
    for changed_review, message in invalid_reviews:
        with pytest.raises(ValueError, match=message):
            ReviewedGroundedAnswerBatch.model_validate(changed_review.model_dump(mode="python"))
    with pytest.raises(ValidationError, match="at least two characters"):
        ReviewedGroundedAnswerBatch.model_validate(
            {**review.model_dump(mode="python"), "reviewer": "  "}
        )

    invalid_reports = (
        (report.model_copy(update={"generated_at": datetime(2026, 9, 14, 7)}), "timezone-aware"),
        (
            report.model_copy(update={"outcomes": (report.outcomes[0],) * 2}),
            "unique and ordered",
        ),
        (
            report.model_copy(
                update={
                    "overall": report.overall.model_copy(
                        update={"case_count": 3, "answered_case_count": 2}
                    )
                }
            ),
            "overall count",
        ),
        (report.model_copy(update={"slices": {" Weather ": report.overall}}), "normalized"),
        (report.model_copy(update={"report_id": "grounded-report-" + "0" * 20}), "report_id"),
    )
    for changed_report, message in invalid_reports:
        with pytest.raises(ValueError, match=message):
            GroundedAnswerEvaluationReport.model_validate(changed_report.model_dump(mode="python"))


def test_metric_and_case_outcome_contracts_fail_closed() -> None:
    task = _task()
    batch = _batch(task)
    report = score_grounded_answer_review(
        task, batch, _review(task, batch), generated_at=REPORTED_AT
    )
    summary = report.overall
    invalid_summaries = (
        (summary.model_copy(update={"answered_case_count": 2}), "cover every case"),
        (
            summary.model_copy(
                update={
                    "claim_count": 0,
                    "mean_support_grade_0_to_3": 1,
                }
            ),
            "claim metrics must be null",
        ),
        (
            summary.model_copy(
                update={
                    "answered_case_count": 2,
                    "abstained_case_count": 0,
                    "appropriate_abstention_rate": 1,
                }
            ),
            "abstention rate must be null",
        ),
    )
    for changed_summary, message in invalid_summaries:
        with pytest.raises(ValueError, match=message):
            GroundedAnswerMetricSummary.model_validate(changed_summary.model_dump(mode="python"))

    answered = next(item for item in report.outcomes if item.response_status == "answered")
    abstained = next(item for item in report.outcomes if item.response_status == "abstained")
    invalid_outcomes = (
        (answered.model_copy(update={"claim_count": 0}), "require claim measurements"),
        (
            answered.model_copy(update={"abstention_appropriate": True}),
            "cannot contain an abstention",
        ),
        (answered.model_copy(update={"strict_pass": not answered.strict_pass}), "strict_pass"),
        (abstained.model_copy(update={"claim_count": 1}), "cannot contain claim measurements"),
        (
            abstained.model_copy(update={"abstention_appropriate": None}),
            "require an appropriateness judgment",
        ),
        (abstained.model_copy(update={"strict_pass": False}), "strict_pass"),
    )
    for changed_outcome, message in invalid_outcomes:
        with pytest.raises(ValueError, match=message):
            GroundedAnswerCaseOutcome.model_validate(changed_outcome.model_dump(mode="python"))


def test_candidate_import_rejects_identity_evidence_and_measurement_drift() -> None:
    task = _task()
    submission = _completed_submission(task)
    base = submission.model_dump(mode="python")
    with pytest.raises(ValueError, match="exact task"):
        apply_grounded_answer_submission(
            task,
            GroundedAnswerSubmission.model_validate({**base, "task_sha256": "0" * 64}),
            system=_system(),
        )

    changed_cases = list(submission.cases)
    changed_cases[0] = changed_cases[0].model_copy(update={"query_id": "changed-query"})
    with pytest.raises(ValueError, match="protected case identities"):
        apply_grounded_answer_submission(
            task,
            submission.model_copy(update={"cases": tuple(changed_cases)}),
            system=_system(),
        )

    incomplete = list(submission.cases)
    incomplete[0] = incomplete[0].model_copy(update={"latency_ms": None})
    with pytest.raises(ValueError, match="complete every candidate field"):
        apply_grounded_answer_submission(
            task,
            submission.model_copy(update={"cases": tuple(incomplete)}),
            system=_system(),
        )

    answered_index = next(
        index
        for index, case in enumerate(submission.cases)
        if case.response is not None and case.response.status == "answered"
    )
    unknown_cases = list(submission.cases)
    unknown_response = unknown_cases[answered_index].response
    assert unknown_response is not None
    unknown_claim = unknown_response.claims[0].model_copy(
        update={"evidence_ids": ("evidence-" + "f" * 64,)}
    )
    unknown_cases[answered_index] = unknown_cases[answered_index].model_copy(
        update={
            "response": unknown_response.model_copy(
                update={"claims": (unknown_claim, *unknown_response.claims[1:])}
            )
        }
    )
    with pytest.raises(ValueError, match="outside the exact task"):
        apply_grounded_answer_submission(
            task,
            submission.model_copy(update={"cases": tuple(unknown_cases)}),
            system=_system(),
        )

    empty_index = next(
        index
        for index, case in enumerate(task.cases)
        if case.pack_status == "no_traceable_evidence"
    )
    wrong_empty = list(submission.cases)
    valid_answer = submission.cases[answered_index].response
    wrong_empty[empty_index] = wrong_empty[empty_index].model_copy(
        update={"response": valid_answer}
    )
    with pytest.raises(ValueError, match="matching abstention"):
        apply_grounded_answer_submission(
            task,
            submission.model_copy(update={"cases": tuple(wrong_empty)}),
            system=_system(),
        )

    wrong_available = list(submission.cases)
    wrong_available[answered_index] = wrong_available[answered_index].model_copy(
        update={
            "response": GroundedAnswerResponse(
                status="abstained",
                abstention_reason="no_traceable_evidence",
            )
        }
    )
    with pytest.raises(ValueError, match="cannot claim that no evidence"):
        apply_grounded_answer_submission(
            task,
            submission.model_copy(update={"cases": tuple(wrong_available)}),
            system=_system(),
        )

    overflow = list(submission.cases)
    overflow[0] = overflow[0].model_copy(update={"input_tokens": 4000})
    with pytest.raises(ValueError, match="exceeds its context"):
        apply_grounded_answer_submission(
            task,
            submission.model_copy(update={"cases": tuple(overflow)}),
            system=_system(),
        )
    with pytest.raises(ValueError, match="tokenizer_artifact_sha256"):
        apply_grounded_answer_submission(
            task, submission, system=_system(tokenizer_artifact_sha256="bad")
        )
    with pytest.raises(ValueError, match="context_length"):
        apply_grounded_answer_submission(task, submission, system=_system(context_length=True))
    with pytest.raises(ValueError, match="max_output_tokens"):
        apply_grounded_answer_submission(task, submission, system=_system(max_output_tokens=5000))

    excess_output = list(submission.cases)
    excess_output[0] = excess_output[0].model_copy(update={"output_tokens": 257})
    with pytest.raises(ValueError, match="output token count"):
        apply_grounded_answer_submission(
            task,
            submission.model_copy(update={"cases": tuple(excess_output)}),
            system=_system(),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        apply_grounded_answer_submission(
            task,
            submission,
            system=_system(),
            generated_at=datetime(2026, 9, 14, 5),
        )


def test_review_import_rejects_tampering_missing_rows_and_invalid_grades() -> None:
    task = _task()
    batch = _batch(task)
    valid = _completed_review_csv(task, batch)
    rows = list(csv.DictReader(io.StringIO(valid)))

    def encode(
        changed: list[dict[str, str]], fields: tuple[str, ...] = GROUNDING_REVIEW_COLUMNS
    ) -> str:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output,
            fieldnames=fields,
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(changed)
        return output.getvalue()

    with pytest.raises(ValueError, match="header"):
        apply_grounded_answer_review(
            task,
            batch,
            encode(rows, GROUNDING_REVIEW_COLUMNS[:-1]),
            reviewer="Alex Reviewer",
        )
    tampered = [dict(row) for row in rows]
    tampered[0]["question_json"] = json.dumps("Changed question")
    with pytest.raises(ValueError, match="changed protected"):
        apply_grounded_answer_review(task, batch, encode(tampered), reviewer="Alex Reviewer")
    with pytest.raises(ValueError, match="exactly cover"):
        apply_grounded_answer_review(task, batch, encode(rows[:-1]), reviewer="Alex Reviewer")
    with pytest.raises(ValueError, match="duplicate rows"):
        apply_grounded_answer_review(
            task, batch, encode([*rows, rows[0]]), reviewer="Alex Reviewer"
        )
    malformed_lines = valid.splitlines()
    malformed_lines[1] += ",unexpected"
    with pytest.raises(ValueError, match="malformed rows"):
        apply_grounded_answer_review(
            task,
            batch,
            "\n".join(malformed_lines) + "\n",
            reviewer="Alex Reviewer",
        )

    invalid_grade = [dict(row) for row in rows]
    claim_index = next(index for index, row in enumerate(invalid_grade) if row["claim_id"])
    invalid_grade[claim_index]["support_0_to_3"] = "4"
    with pytest.raises(ValueError, match="between 0 and 3"):
        apply_grounded_answer_review(task, batch, encode(invalid_grade), reviewer="Alex Reviewer")
    non_integer_grade = [dict(row) for row in rows]
    non_integer_grade[claim_index]["support_0_to_3"] = "1.5"
    with pytest.raises(ValueError, match="whole number"):
        apply_grounded_answer_review(
            task, batch, encode(non_integer_grade), reviewer="Alex Reviewer"
        )
    invalid_bool = [dict(row) for row in rows]
    abstain_index = next(index for index, row in enumerate(invalid_bool) if not row["claim_id"])
    invalid_bool[abstain_index]["abstention_appropriate"] = "maybe"
    with pytest.raises(ValueError, match="yes or no"):
        apply_grounded_answer_review(task, batch, encode(invalid_bool), reviewer="Alex Reviewer")
    missing_relevance = [dict(row) for row in rows]
    first_index = next(
        index for index, row in enumerate(missing_relevance) if row["claim_id"] == "claim-01"
    )
    missing_relevance[first_index]["answer_relevance_0_to_2"] = ""
    with pytest.raises(ValidationError, match="first claim row"):
        apply_grounded_answer_review(
            task, batch, encode(missing_relevance), reviewer="Alex Reviewer"
        )
    second_relevance = [dict(row) for row in rows]
    second_index = next(
        index for index, row in enumerate(second_relevance) if row["claim_id"] == "claim-02"
    )
    second_relevance[second_index]["answer_relevance_0_to_2"] = "2"
    with pytest.raises(ValidationError, match="only the first claim"):
        apply_grounded_answer_review(
            task, batch, encode(second_relevance), reviewer="Alex Reviewer"
        )
    claim_abstention = [dict(row) for row in rows]
    claim_abstention[claim_index]["abstention_appropriate"] = "no"
    with pytest.raises(ValidationError, match="must not contain abstention"):
        apply_grounded_answer_review(
            task, batch, encode(claim_abstention), reviewer="Alex Reviewer"
        )
    abstention_grade = [dict(row) for row in rows]
    abstention_grade[abstain_index]["support_0_to_3"] = "1"
    with pytest.raises(ValidationError, match="abstention rows"):
        apply_grounded_answer_review(
            task, batch, encode(abstention_grade), reviewer="Alex Reviewer"
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        apply_grounded_answer_review(
            task,
            batch,
            valid,
            reviewer="Alex Reviewer",
            reviewed_at=datetime(2026, 9, 14, 6),
        )


def test_content_addressed_artifacts_reject_tampering() -> None:
    task = _task()
    batch = _batch(task)
    review = _review(task, batch)
    report = score_grounded_answer_review(task, batch, review, generated_at=REPORTED_AT)

    with pytest.raises(ValidationError, match="task_sha256"):
        GroundedAnswerTask.model_validate(
            {**task.model_dump(mode="python"), "task_sha256": "0" * 64}
        )
    with pytest.raises(ValidationError, match="batch_sha256"):
        GroundedAnswerCandidateBatch.model_validate(
            {**batch.model_dump(mode="python"), "batch_sha256": "0" * 64}
        )
    with pytest.raises(ValidationError, match="review_sha256"):
        ReviewedGroundedAnswerBatch.model_validate(
            {**review.model_dump(mode="python"), "review_sha256": "0" * 64}
        )
    with pytest.raises(ValidationError, match="report_sha256"):
        GroundedAnswerEvaluationReport.model_validate(
            {**report.model_dump(mode="python"), "report_sha256": "0" * 64}
        )
    with pytest.raises(ValidationError, match="closed promotion boundary"):
        GroundedAnswerEvaluationReport.model_validate(
            {**report.model_dump(mode="python"), "promotion_blockers": ("changed",)}
        )


def test_evidence_pack_identity_rejects_deserialized_drift() -> None:
    benchmark = _benchmark()
    pack = _pack(benchmark, benchmark.queries[0])
    validate_evidence_pack_identity(pack)
    item = pack.items[0]
    duplicate_id_item = replace(item, retrieval_rank=2)
    variants = (
        (replace(pack, pack_id="pack-" + "0" * 64), "pack ID"),
        (replace(pack, status="no_traceable_evidence"), "status"),
        (replace(pack, trust_boundary="changed"), "metadata"),
        (replace(pack, source_text_characters=0), "source-text count"),
        (
            replace(pack, retrieval=replace(pack.retrieval, returned_hits=2)),
            "retrieval count",
        ),
        (replace(pack, items=(replace(item, retrieval_rank=2),)), "ranks"),
        (
            replace(pack, retrieval=replace(pack.retrieval, candidates_considered=0)),
            "candidate count",
        ),
        (
            replace(
                pack,
                budget=EvidencePackBudget(
                    max_items=3,
                    max_characters_per_item=1,
                    max_total_characters=500,
                ),
            ),
            "declared budget",
        ),
        (
            replace(
                pack,
                retrieval=replace(
                    pack.retrieval,
                    candidates_considered=2,
                    returned_hits=2,
                ),
                items=(duplicate_id_item, item),
                source_text_characters=item.text_characters * 2,
            ),
            "preserve retrieval order",
        ),
        (
            replace(
                pack,
                retrieval=replace(
                    pack.retrieval,
                    candidates_considered=2,
                    returned_hits=2,
                ),
                items=(item, duplicate_id_item),
                source_text_characters=item.text_characters * 2,
            ),
            "unique identities",
        ),
        (
            replace(
                pack,
                items=(
                    replace(
                        item,
                        citation=replace(item.citation, status="missing", url=None),
                    ),
                ),
            ),
            "traceable citations",
        ),
        (replace(pack, items=(replace(item, text_sha256="0" * 64),)), "text measurements"),
        (
            replace(pack, items=(replace(item, document_characters=item.text_characters - 1),)),
            "document length",
        ),
        (replace(pack, items=(replace(item, truncated=True),)), "truncation flag"),
        (replace(pack, items=(replace(item, evidence_id="evidence-" + "0" * 64),)), "item ID"),
    )
    for changed, message in variants:
        with pytest.raises(ValueError, match=message):
            validate_evidence_pack_identity(changed)


def test_capture_rejects_endpoint_budget_parameter_and_pack_drift() -> None:
    benchmark = _benchmark()
    with pytest.raises(ValueError, match="absolute HTTP"):
        asyncio.run(capture_grounded_answer_task(benchmark, base_url="not-a-url"))
    with pytest.raises(ValueError, match="embedded credentials"):
        asyncio.run(capture_grounded_answer_task(benchmark, base_url="http://user:pass@atlas.test"))
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(
            capture_grounded_answer_task(
                benchmark,
                base_url="http://atlas.test",
                captured_at=datetime(2026, 9, 14, 4),
            )
        )

    async def run_mutation(mutate: Callable[[dict[str, object]], None]) -> None:
        async with _capture_client(benchmark, mutate=mutate) as client:
            await capture_grounded_answer_task(
                benchmark,
                base_url="http://atlas.test",
                client=client,
                captured_at=CAPTURED_AT,
            )

    def change_budget(payload: dict[str, object]) -> None:
        payload["budget"]["max_items"] = 2  # type: ignore[index]

    with pytest.raises(RuntimeError, match="changed the requested"):
        asyncio.run(run_mutation(change_budget))

    def change_item_count(payload: dict[str, object]) -> None:
        payload["item_count"] = 2

    with pytest.raises(RuntimeError, match="inconsistent item counts"):
        asyncio.run(run_mutation(change_item_count))

    def change_returned_hits(payload: dict[str, object]) -> None:
        payload["retrieval"]["returned_hits"] = 2  # type: ignore[index]

    with pytest.raises(RuntimeError, match="account for every returned"):
        asyncio.run(run_mutation(change_returned_hits))

    def change_query(payload: dict[str, object]) -> None:
        payload["retrieval"]["parameters"]["query"] = "changed"  # type: ignore[index]

    with pytest.raises(RuntimeError, match="exact requested query"):
        asyncio.run(run_mutation(change_query))

    def change_pack_id(payload: dict[str, object]) -> None:
        payload["pack_id"] = "pack-" + "0" * 64

    with pytest.raises(ValueError, match="pack ID"):
        asyncio.run(run_mutation(change_pack_id))


def test_capture_preserves_temporal_bbox_and_radius_filters() -> None:
    benchmark = GroundedAnswerBenchmark(
        benchmark_id="filter-coverage-v1",
        title="Spatial filter coverage",
        description="Exercises every optional deployed evidence-pack request parameter.",
        retrieval_limit=5,
        candidate_limit=10,
        max_items=2,
        max_characters_per_item=200,
        max_total_characters=300,
        queries=(
            GroundedAnswerQuery(
                query_id="bbox-query",
                question="What alerts are inside this region?",
                slices=("spatial",),
                filters={
                    "source": "nws",
                    "occurred_after": datetime(2026, 9, 13, tzinfo=UTC),
                    "occurred_before": datetime(2026, 9, 15, tzinfo=UTC),
                    "active_only": False,
                    "bbox": (-125.0, 24.0, -66.0, 50.0),
                    "alert_type": "Tornado Warning",
                },
            ),
            GroundedAnswerQuery(
                query_id="near-query",
                question="What disruptions are near this point?",
                slices=("spatial",),
                filters={
                    "active_only": False,
                    "near": (-118.25, 34.05),
                    "radius_km": 75.0,
                    "min_confidence_rank": 3,
                    "observation_period": "night",
                },
            ),
        ),
    )

    async def capture() -> GroundedAnswerTask:
        async with _capture_client(benchmark) as client:
            return await capture_grounded_answer_task(
                benchmark,
                base_url="https://atlas.test/api/",
                client=client,
                captured_at=CAPTURED_AT,
            )

    task = asyncio.run(capture())
    assert task.endpoint == "https://atlas.test/api"
    assert task.case_count == 2


def test_scoring_rejects_cross_artifact_mismatch_and_incomplete_review() -> None:
    task = _task()
    batch = _batch(task)
    review = _review(task, batch)
    with pytest.raises(ValueError, match="batch does not match"):
        score_grounded_answer_review(
            task,
            batch.model_copy(update={"task_sha256": "0" * 64}),
            review,
        )
    with pytest.raises(ValueError, match="review does not match"):
        score_grounded_answer_review(
            task,
            batch,
            review.model_copy(update={"batch_sha256": "0" * 64}),
        )
    with pytest.raises(ValueError, match="judgments do not exactly cover"):
        score_grounded_answer_review(
            task,
            batch,
            review.model_copy(update={"judgments": review.judgments[:-1]}),
        )
    with pytest.raises(ValueError, match="batch cases do not exactly cover"):
        score_grounded_answer_review(
            task,
            batch.model_copy(update={"cases": batch.cases[:-1]}),
            review,
        )
    removed_case_id = review.judgments[0].case_id
    incomplete_cases = tuple(item for item in review.judgments if item.case_id != removed_case_id)
    with pytest.raises(ValueError, match="review cases do not exactly cover"):
        score_grounded_answer_review(
            task,
            batch,
            review.model_copy(update={"judgments": incomplete_cases}),
        )

    abstained_case_id = next(
        case.case_id for case in batch.cases if case.response.status == "abstained"
    )
    malformed_abstention = tuple(
        item.model_copy(update={"claim_id": "claim-01"})
        if item.case_id == abstained_case_id
        else item
        for item in review.judgments
    )
    with pytest.raises(ValueError, match="exactly one abstention"):
        score_grounded_answer_review(
            task,
            batch,
            review.model_copy(update={"judgments": malformed_abstention}),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        score_grounded_answer_review(
            task,
            batch,
            review,
            generated_at=datetime(2026, 9, 14, 7),
        )


def test_cli_candidate_review_score_and_input_protection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    task = _task()
    task_path = tmp_path / "task.json"
    submission_path = tmp_path / "submission.json"
    system_path = tmp_path / "system.json"
    batch_path = tmp_path / "batch.json"
    sheet_path = tmp_path / "review.csv"
    task_path.write_text(task.model_dump_json(indent=2), encoding="utf-8")
    submission_path.write_text(
        _completed_submission(task).model_dump_json(indent=2), encoding="utf-8"
    )
    system_path.write_text(_system().model_dump_json(indent=2), encoding="utf-8")

    assert (
        grounded_cli.run_cli(
            [
                "candidate-import",
                "--task",
                str(task_path),
                "--submission",
                str(submission_path),
                "--candidate-definition",
                str(system_path),
                "--output-batch",
                str(batch_path),
                "--output-review-sheet",
                str(sheet_path),
            ]
        )
        == 0
    )
    assert "model-blind" in capsys.readouterr().out
    batch = GroundedAnswerCandidateBatch.model_validate_json(batch_path.read_text(encoding="utf-8"))
    sheet_path.write_text(_completed_review_csv(task, batch), encoding="utf-8")
    review_path = tmp_path / "review.json"
    assert (
        grounded_cli.run_cli(
            [
                "review",
                "--task",
                str(task_path),
                "--batch",
                str(batch_path),
                "--judgments",
                str(sheet_path),
                "--reviewer",
                "Alex Reviewer",
                "--output-review",
                str(review_path),
            ]
        )
        == 0
    )
    assert "promotion remains blocked" in capsys.readouterr().out
    report_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    assert (
        grounded_cli.run_cli(
            [
                "score",
                "--task",
                str(task_path),
                "--batch",
                str(batch_path),
                "--review",
                str(review_path),
                "--output-json",
                str(report_path),
                "--output-markdown",
                str(markdown_path),
            ]
        )
        == 0
    )
    assert "blocked descriptive report" in capsys.readouterr().out
    assert "Promotion status: **BLOCKED**" in markdown_path.read_text(encoding="utf-8")

    original = task_path.read_text(encoding="utf-8")
    assert (
        grounded_cli.run_cli(
            [
                "score",
                "--task",
                str(task_path),
                "--batch",
                str(batch_path),
                "--review",
                str(review_path),
                "--output-json",
                str(task_path),
                "--output-markdown",
                str(markdown_path),
                "--force",
            ]
        )
        == 2
    )
    assert "must not replace input artifacts" in capsys.readouterr().err
    assert task_path.read_text(encoding="utf-8") == original


def test_cli_compare_adjudicate_and_score_final_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    task = _task()
    batch = _batch(task)
    first = _review(task, batch)
    second = _second_review(task, batch)
    task_path = tmp_path / "task.json"
    batch_path = tmp_path / "batch.json"
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    task_path.write_text(task.model_dump_json(indent=2), encoding="utf-8")
    batch_path.write_text(batch.model_dump_json(indent=2), encoding="utf-8")
    first_path.write_text(first.model_dump_json(indent=2), encoding="utf-8")
    second_path.write_text(second.model_dump_json(indent=2), encoding="utf-8")
    agreement_json = tmp_path / "agreement.json"
    agreement_markdown = tmp_path / "agreement.md"
    adjudication_sheet = tmp_path / "adjudication.csv"

    assert (
        grounded_cli.run_cli(
            [
                "compare-reviews",
                "--task",
                str(task_path),
                "--batch",
                str(batch_path),
                "--first-review",
                str(second_path),
                "--second-review",
                str(first_path),
                "--output-json",
                str(agreement_json),
                "--output-markdown",
                str(agreement_markdown),
                "--output-adjudication-sheet",
                str(adjudication_sheet),
            ]
        )
        == 0
    )
    assert "3 disputed row(s), 4 field(s)" in capsys.readouterr().out
    assert "review_a" in adjudication_sheet.read_text(encoding="utf-8")
    assert "Rubric-field agreement" in agreement_markdown.read_text(encoding="utf-8")
    adjudication_sheet.write_text(
        _completed_adjudication_csv(adjudication_sheet.read_text(encoding="utf-8")),
        encoding="utf-8",
    )

    final_path = tmp_path / "final.json"
    adjudication_json = tmp_path / "adjudication.json"
    adjudication_markdown = tmp_path / "adjudication.md"
    assert (
        grounded_cli.run_cli(
            [
                "adjudicate",
                "--task",
                str(task_path),
                "--batch",
                str(batch_path),
                "--first-review",
                str(first_path),
                "--second-review",
                str(second_path),
                "--judgments",
                str(adjudication_sheet),
                "--adjudicator",
                "Casey Adjudicator",
                "--output-review",
                str(final_path),
                "--output-json",
                str(adjudication_json),
                "--output-markdown",
                str(adjudication_markdown),
            ]
        )
        == 0
    )
    assert "3 disputed row(s) adjudicated" in capsys.readouterr().out
    final_review = ReviewedGroundedAnswerBatch.model_validate_json(
        final_path.read_text(encoding="utf-8")
    )
    assert final_review.review_status == "independent_adjudication_complete"
    assert "Promotion status: **BLOCKED**" in adjudication_markdown.read_text(encoding="utf-8")

    report_path = tmp_path / "final-report.json"
    report_markdown = tmp_path / "final-report.md"
    assert (
        grounded_cli.run_cli(
            [
                "score",
                "--task",
                str(task_path),
                "--batch",
                str(batch_path),
                "--review",
                str(final_path),
                "--output-json",
                str(report_path),
                "--output-markdown",
                str(report_markdown),
            ]
        )
        == 0
    )
    scored = GroundedAnswerEvaluationReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    assert scored.adjudication is not None
    assert "Final adjudicated review" in report_markdown.read_text(encoding="utf-8")


def test_cli_capture_writes_blank_gold_free_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task = _task()

    async def fake_capture(
        _benchmark_value: GroundedAnswerBenchmark,
        *,
        base_url: str,
    ) -> GroundedAnswerTask:
        assert base_url == "http://atlas.test"
        return task

    monkeypatch.setattr(grounded_cli, "capture_grounded_answer_task", fake_capture)
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text(_benchmark().model_dump_json(indent=2), encoding="utf-8")
    task_path = tmp_path / "task.json"
    submission_path = tmp_path / "submission.json"
    assert (
        grounded_cli.run_cli(
            [
                "capture",
                "--benchmark",
                str(benchmark_path),
                "--base-url",
                "http://atlas.test",
                "--output-task",
                str(task_path),
                "--output-submission",
                str(submission_path),
            ]
        )
        == 0
    )
    assert "gold-free" in capsys.readouterr().out
    submission = GroundedAnswerSubmission.model_validate_json(
        submission_path.read_text(encoding="utf-8")
    )
    assert all(case.response is None and case.input_tokens is None for case in submission.cases)
    assert "reviewer" not in task_path.read_text(encoding="utf-8")

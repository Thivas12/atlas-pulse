"""Observable agent trajectory, drift, and release-threshold contract tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import atlas_pulse.agent_trajectory.cli as trajectory_cli
from atlas_pulse.agent_trajectory import (
    AGENT_RELEASE_ASSESSMENT_MAX_BYTES,
    AgentCapabilityObservation,
    AgentReleaseAssessment,
    AgentReleaseThresholdPolicy,
    AgentTrajectoryBatch,
    AgentTrajectoryDriftReport,
    AgentTrajectoryScoreReport,
    AgentTrajectoryStep,
    AgentTrajectorySubmission,
    AgentTrajectorySubmissionCase,
    agent_release_policy_sha256,
    apply_agent_trajectory_submission,
    build_agent_trajectory_submission,
    build_default_agent_release_policy,
    compare_agent_trajectory_reports,
    evaluate_agent_release,
    load_agent_release_assessment,
    render_agent_release_markdown,
    render_agent_trajectory_drift_markdown,
    render_agent_trajectory_markdown,
    score_agent_trajectory,
)
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.grounded_answer_evaluation import (
    GroundedAnswerCandidateBatch,
    GroundedAnswerCaseOutcome,
    GroundedAnswerClaim,
    GroundedAnswerEvaluationReport,
    GroundedAnswerEvidence,
    GroundedAnswerIndependentAdjudicationProvenance,
    GroundedAnswerMetricSummary,
    GroundedAnswerResponse,
    GroundedAnswerSubmission,
    GroundedAnswerSubmissionCase,
    GroundedAnswerTask,
    GroundedAnswerTaskCase,
    apply_grounded_answer_submission,
    grounded_answer_case_sha256,
    grounded_answer_task_sha256,
)
from atlas_pulse.relationship_evaluation import (
    CandidateLatencyMetrics,
    CandidateSystemDefinition,
    IndependentAdjudicationProvenance,
    PairedOutcomeSummary,
    RelationshipCandidateComparisonReport,
    RelationshipMetricDelta,
    RelationshipSliceComparison,
    RelationshipSliceMetrics,
)

CAPTURED_AT = datetime(2026, 9, 10, 8, tzinfo=UTC)


def _system() -> CandidateSystemDefinition:
    return CandidateSystemDefinition(
        candidate_id="qwen-grounded-agent-v1",
        model_id="Qwen/Qwen3-1.7B-GGUF",
        model_revision="a" * 40,
        model_artifact_sha256="b" * 64,
        adapter_version="observable-trajectory-adapter-v1",
        input_template_sha256="c" * 64,
        runtime="llama.cpp-cpu",
        runtime_version="build-123",
        parameters={
            "context_length": 8192,
            "max_output_tokens": 768,
            "tokenizer_artifact_sha256": "d" * 64,
            "temperature": 0,
        },
    )


def _relationship_system() -> CandidateSystemDefinition:
    return CandidateSystemDefinition(
        candidate_id="deberta-relationship-v1",
        model_id="cross-encoder/deberta-v3-small",
        model_revision="e" * 40,
        model_artifact_sha256="f" * 64,
        adapter_version="predicate-nli-v1",
        input_template_sha256="1" * 64,
        runtime="onnxruntime-cpu",
        runtime_version="1.22.0",
        parameters={"max_length": 384},
    )


def _task(*, captured_at: datetime = CAPTURED_AT, seed: str = "first") -> GroundedAnswerTask:
    evidence_text = f"Residents should shelter indoors. Capture {seed}."
    evidence = GroundedAnswerEvidence(
        evidence_id=f"evidence-{hashlib.sha256(seed.encode()).hexdigest()}",
        retrieval_rank=1,
        stream_id="100-1",
        event_id=f"alert-{seed}",
        event_type="weather.alert",
        source="nws",
        occurred_at=captured_at - timedelta(minutes=10),
        text=evidence_text,
        document_sha256=hashlib.sha256(f"document-{seed}".encode()).hexdigest(),
        text_sha256=hashlib.sha256(evidence_text.encode()).hexdigest(),
        truncated=False,
        citation_url=f"https://api.weather.gov/alerts/{seed}",
    )
    answered_draft = GroundedAnswerTaskCase.model_construct(
        case_id="answer-case-" + "0" * 20,
        query_id=f"answered-{seed}",
        question="What protective action is stated?",
        slices=("answered", "weather"),
        pack_id=f"pack-{hashlib.sha256(f'pack-{seed}'.encode()).hexdigest()}",
        pack_status="traceable_evidence_available",
        evidence=(evidence,),
        exclusion_count=0,
        source_text_characters=len(evidence_text),
    )
    answered_hash = grounded_answer_case_sha256(answered_draft)
    answered = GroundedAnswerTaskCase(
        **answered_draft.model_dump(mode="python", exclude={"case_id"}),
        case_id=f"answer-case-{answered_hash[:20]}",
    )
    abstained_draft = GroundedAnswerTaskCase.model_construct(
        case_id="answer-case-" + "0" * 20,
        query_id=f"empty-{seed}",
        question="What road restriction is stated?",
        slices=("abstention", "weather"),
        pack_id=f"pack-{hashlib.sha256(f'empty-{seed}'.encode()).hexdigest()}",
        pack_status="no_traceable_evidence",
        evidence=(),
        exclusion_count=0,
        source_text_characters=0,
    )
    abstained_hash = grounded_answer_case_sha256(abstained_draft)
    abstained = GroundedAnswerTaskCase(
        **abstained_draft.model_dump(mode="python", exclude={"case_id"}),
        case_id=f"answer-case-{abstained_hash[:20]}",
    )
    cases = tuple(sorted((answered, abstained), key=lambda case: case.case_id))
    draft = GroundedAnswerTask.model_construct(
        task_id="grounded-task-" + "0" * 20,
        task_sha256="0" * 64,
        benchmark_id="live-grounded-briefs-v1",
        benchmark_sha256=hashlib.sha256(b"benchmark").hexdigest(),
        captured_at=captured_at,
        endpoint="http://atlas.test",
        case_count=len(cases),
        cases=cases,
    )
    digest = grounded_answer_task_sha256(draft)
    return GroundedAnswerTask(
        **draft.model_dump(mode="python", exclude={"task_id", "task_sha256"}),
        task_id=f"grounded-task-{digest[:20]}",
        task_sha256=digest,
    )


def _candidate_batch(task: GroundedAnswerTask) -> GroundedAnswerCandidateBatch:
    cases: list[GroundedAnswerSubmissionCase] = []
    for case in task.cases:
        if case.evidence:
            response = GroundedAnswerResponse(
                status="answered",
                claims=(
                    GroundedAnswerClaim(
                        claim_id="claim-01",
                        text="Residents should shelter indoors.",
                        evidence_ids=(case.evidence[0].evidence_id,),
                    ),
                ),
            )
            output_tokens = 12
        else:
            response = GroundedAnswerResponse(
                status="abstained", abstention_reason="no_traceable_evidence"
            )
            output_tokens = 4
        cases.append(
            GroundedAnswerSubmissionCase(
                case_id=case.case_id,
                query_id=case.query_id,
                response=response,
                input_tokens=120,
                output_tokens=output_tokens,
                latency_ms=20 if case.evidence else 10,
            )
        )
    submission = GroundedAnswerSubmission(
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        cases=tuple(cases),
    )
    return apply_grounded_answer_submission(
        task,
        submission,
        system=_system(),
        generated_at=task.captured_at + timedelta(minutes=5),
    )


def _grounded_report(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
    *,
    strict: bool = True,
) -> GroundedAnswerEvaluationReport:
    outcomes = tuple(
        GroundedAnswerCaseOutcome(
            case_id=case.case_id,
            query_id=case.query_id,
            response_status=case.response.status,
            claim_count=len(case.response.claims),
            minimum_support_grade=(3 if strict else 1)
            if case.response.status == "answered"
            else None,
            minimum_citation_quality=(2 if strict else 1)
            if case.response.status == "answered"
            else None,
            answer_relevance=(2 if strict else 1) if case.response.status == "answered" else None,
            abstention_appropriate=(strict if case.response.status == "abstained" else None),
            strict_pass=strict,
        )
        for case in batch.cases
    )
    summary = GroundedAnswerMetricSummary(
        case_count=len(outcomes),
        answered_case_count=1,
        abstained_case_count=1,
        claim_count=1,
        mean_support_grade_0_to_3=3 if strict else 1,
        fully_supported_claim_rate=1 if strict else 0,
        unsupported_or_contradicted_claim_rate=0 if strict else 1,
        mean_citation_quality_0_to_2=2 if strict else 1,
        complete_citation_rate=1 if strict else 0,
        mean_answer_relevance_0_to_2=2 if strict else 1,
        appropriate_abstention_rate=1 if strict else 0,
        strict_case_pass_rate=1 if strict else 0,
        mean_input_tokens=120,
        mean_output_tokens=8,
        mean_latency_ms=15,
        p95_latency_ms=19.5,
    )
    digest = canonical_sha256(
        {"task": task.task_sha256, "batch": batch.batch_sha256, "strict": strict}
    )
    provenance = GroundedAnswerIndependentAdjudicationProvenance(
        independent_reviewers=("Alex Reviewer", "Blair Reviewer"),
        independent_review_ids=(
            "grounded-review-" + "a" * 20,
            "grounded-review-" + "b" * 20,
        ),
        independent_review_sha256s=("c" * 64, "d" * 64),
        agreement_report_id="grounded-agreement-" + "e" * 20,
        dimension_observed_agreement={"support_grade": 1.0},
        dimension_cohen_kappa={"support_grade": 1.0},
        adjudicator="Casey Adjudicator",
        adjudicated_at=task.captured_at + timedelta(minutes=50),
        judgment_count=2,
        adjudication_decision_count=0,
        adjudicated_field_count=0,
    )
    return GroundedAnswerEvaluationReport.model_construct(
        report_id=f"grounded-report-{digest[:20]}",
        report_sha256=digest,
        generated_at=task.captured_at + timedelta(hours=1),
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        batch_id=batch.batch_id,
        batch_sha256=batch.batch_sha256,
        review_id="grounded-review-" + "f" * 20,
        review_sha256="1" * 64,
        reviewer="Casey Adjudicator",
        adjudication=provenance,
        system=batch.system,
        overall=summary,
        slices={},
        outcomes=outcomes,
    )


def _trajectory_submission(
    task: GroundedAnswerTask,
    batch: GroundedAnswerCandidateBatch,
    *,
    network_accessed: bool = False,
    omit_inspection: bool = False,
) -> AgentTrajectorySubmission:
    cases: list[AgentTrajectorySubmissionCase] = []
    candidates = {case.case_id: case for case in batch.cases}
    for task_case in task.cases:
        candidate = candidates[task_case.case_id]
        steps: list[AgentTrajectoryStep] = []
        if candidate.response.status == "answered":
            evidence_id = candidate.response.claims[0].evidence_ids[0]
            if not omit_inspection:
                steps.append(
                    AgentTrajectoryStep(
                        sequence=len(steps) + 1,
                        action="inspect_evidence",
                        evidence_ids=(evidence_id,),
                        duration_ms=2,
                    )
                )
            steps.append(
                AgentTrajectoryStep(
                    sequence=len(steps) + 1,
                    action="draft_claim",
                    evidence_ids=(evidence_id,),
                    claim_ids=("claim-01",),
                    duration_ms=7,
                )
            )
        else:
            steps.append(
                AgentTrajectoryStep(
                    sequence=1,
                    action="choose_abstention",
                    duration_ms=3,
                )
            )
        steps.append(
            AgentTrajectoryStep(
                sequence=len(steps) + 1,
                action="finalize",
                duration_ms=1,
            )
        )
        cases.append(
            AgentTrajectorySubmissionCase(
                case_id=task_case.case_id,
                query_id=task_case.query_id,
                steps=tuple(steps),
                capabilities=AgentCapabilityObservation(
                    network_accessed=network_accessed,
                    tools_invoked=False,
                    external_side_effects_performed=False,
                ),
            )
        )
    return AgentTrajectorySubmission(
        task_id=task.task_id,
        task_sha256=task.task_sha256,
        candidate_batch_id=batch.batch_id,
        candidate_batch_sha256=batch.batch_sha256,
        cases=tuple(sorted(cases, key=lambda case: case.case_id)),
    )


def _score(
    *,
    captured_at: datetime = CAPTURED_AT,
    seed: str = "first",
    network_accessed: bool = False,
    omit_inspection: bool = False,
    grounded_strict: bool = True,
) -> tuple[
    GroundedAnswerTask,
    GroundedAnswerCandidateBatch,
    GroundedAnswerEvaluationReport,
    AgentTrajectoryBatch,
    AgentTrajectoryScoreReport,
]:
    task = _task(captured_at=captured_at, seed=seed)
    candidate = _candidate_batch(task)
    grounded = _grounded_report(task, candidate, strict=grounded_strict)
    trajectory = apply_agent_trajectory_submission(
        task,
        candidate,
        _trajectory_submission(
            task,
            candidate,
            network_accessed=network_accessed,
            omit_inspection=omit_inspection,
        ),
        observed_at=captured_at + timedelta(minutes=10),
    )
    score = score_agent_trajectory(
        task,
        candidate,
        grounded,
        trajectory,
        generated_at=captured_at + timedelta(hours=2),
    )
    return task, candidate, grounded, trajectory, score


def _relationship_report(*, case_count: int = 30) -> RelationshipCandidateComparisonReport:
    baseline = RelationshipSliceMetrics(
        case_count=case_count,
        accuracy=0.90,
        macro_f1=0.90,
        decisive_coverage=0.90,
        abstention_rate=0.10,
        selective_accuracy=0.95,
        labels={},
    )
    candidate = baseline.model_copy()
    delta = RelationshipMetricDelta(
        accuracy=0.0,
        macro_f1=0.0,
        decisive_coverage=0.0,
        abstention_rate=0.0,
        selective_accuracy=0.0,
    )
    overall = RelationshipSliceComparison(
        baseline=baseline,
        candidate=candidate,
        delta=delta,
    )
    paired = PairedOutcomeSummary(
        case_count=case_count,
        unchanged_correct=case_count,
        unchanged_incorrect=0,
        improvements=0,
        regressions=0,
        changed_incorrect=0,
        label_disagreements=0,
    )
    adjudication = IndependentAdjudicationProvenance(
        independent_reviewers=("Alex Reviewer", "Blair Reviewer"),
        review_pool_sha256s=("2" * 64, "3" * 64),
        agreement_report_id="relationship-agreement-v1",
        observed_agreement=1.0,
        cohen_kappa=1.0,
        adjudicator="Casey Adjudicator",
        adjudicated_at=CAPTURED_AT,
        adjudication_decision_count=0,
    )
    return RelationshipCandidateComparisonReport.model_construct(
        report_id="relationship-live-v1-candidate-123456789abc",
        generated_at=CAPTURED_AT,
        gold_pool_id="relationship-live-v1",
        gold_pool_sha256="4" * 64,
        source_capture_sha256="5" * 64,
        task_id="candidate-task-" + "6" * 20,
        task_sha256="7" * 64,
        prediction_batch_id="candidate-batch-" + "8" * 20,
        prediction_batch_sha256="9" * 64,
        rubric_version="claim-pair-rubric-v1",
        baseline_relationship_rule_version="structured-claims-v1",
        candidate_system=_relationship_system(),
        adjudication=adjudication,
        case_count=case_count,
        overall=overall,
        predicates={},
        source_pairs={},
        baseline_confusion_matrix={},
        candidate_confusion_matrix={},
        prediction_transition_matrix={},
        paired_outcomes=paired,
        candidate_latency=CandidateLatencyMetrics(
            case_count=case_count,
            total_ms=float(case_count * 10),
            mean_ms=10,
            p50_ms=10,
            p95_ms=10,
            max_ms=10,
        ),
        outcomes=(),
    )


def _relaxed_policy() -> AgentReleaseThresholdPolicy:
    default = build_default_agent_release_policy()
    values = default.model_dump(mode="python", exclude={"policy_id", "policy_sha256"})
    values["drift_thresholds"] = default.drift_thresholds
    values.update(
        {
            "min_relationship_case_count": 2,
            "min_trajectory_capture_count": 2,
            "min_total_trajectory_case_count": 4,
            "min_case_count_per_capture": 2,
        }
    )
    draft = AgentReleaseThresholdPolicy.model_construct(
        **values,
        policy_id="release-policy-" + "0" * 20,
        policy_sha256="0" * 64,
    )
    digest = agent_release_policy_sha256(draft)
    return AgentReleaseThresholdPolicy(
        **values,
        policy_id=f"release-policy-{digest[:20]}",
        policy_sha256=digest,
    )


def _eligible_assessment() -> AgentReleaseAssessment:
    _, _, grounded_a, _, trajectory_a = _score()
    _, _, grounded_b, _, trajectory_b = _score(
        captured_at=CAPTURED_AT + timedelta(days=1), seed="second"
    )
    policy = _relaxed_policy()
    drift = compare_agent_trajectory_reports(
        trajectory_a,
        trajectory_b,
        thresholds=policy.drift_thresholds,
        generated_at=CAPTURED_AT + timedelta(days=1, hours=3),
    )
    return evaluate_agent_release(
        policy,
        _relationship_report(case_count=2),
        (grounded_b, grounded_a),
        (trajectory_b, trajectory_a),
        (drift,),
        generated_at=CAPTURED_AT + timedelta(days=1, hours=4),
    )


def test_trajectory_template_import_is_content_addressed_and_reasoning_free() -> None:
    task = _task()
    candidate = _candidate_batch(task)
    template = build_agent_trajectory_submission(task, candidate)
    assert all(case.steps is None and case.capabilities is None for case in template.cases)

    batch = apply_agent_trajectory_submission(
        task,
        candidate,
        _trajectory_submission(task, candidate),
        observed_at=CAPTURED_AT + timedelta(minutes=10),
    )
    repeated = apply_agent_trajectory_submission(
        task,
        candidate,
        _trajectory_submission(task, candidate),
        observed_at=CAPTURED_AT + timedelta(minutes=10),
    )

    assert batch == repeated
    assert batch.trajectory_batch_id.startswith("trajectory-batch-")
    assert batch.execution_authorized is False
    assert set(AgentTrajectoryStep.model_fields) == {
        "sequence",
        "action",
        "evidence_ids",
        "claim_ids",
        "duration_ms",
    }
    assert AgentTrajectoryBatch.model_validate_json(batch.model_dump_json()) == batch


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (
            {"action": "inspect_evidence", "evidence_ids": (), "claim_ids": ()},
            "requires evidence IDs",
        ),
        (
            {
                "action": "draft_claim",
                "evidence_ids": ("evidence-" + "a" * 64,),
                "claim_ids": (),
            },
            "exactly one claim ID",
        ),
        (
            {
                "action": "finalize",
                "evidence_ids": ("evidence-" + "a" * 64,),
                "claim_ids": (),
            },
            "cannot contain evidence",
        ),
    ],
)
def test_trajectory_step_rejects_ambiguous_action_shapes(
    values: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        AgentTrajectoryStep.model_validate({"sequence": 1, "duration_ms": 1, **values})


def test_trajectory_import_rejects_incomplete_tampered_and_cross_case_inputs() -> None:
    task = _task()
    candidate = _candidate_batch(task)
    template = build_agent_trajectory_submission(task, candidate)
    with pytest.raises(ValueError, match="incomplete"):
        apply_agent_trajectory_submission(task, candidate, template)

    submission = _trajectory_submission(task, candidate)
    changed = submission.model_copy(
        update={"task_sha256": "f" * 64},
    )
    with pytest.raises(ValueError, match="does not match"):
        apply_agent_trajectory_submission(task, candidate, changed)

    answered = next(case for case in submission.cases if len(case.steps or ()) == 3)
    foreign = AgentTrajectoryStep(
        sequence=1,
        action="inspect_evidence",
        evidence_ids=("evidence-" + "f" * 64,),
        duration_ms=1,
    )
    assert answered.steps is not None
    bad_case = answered.model_copy(update={"steps": (foreign, *answered.steps[1:])})
    bad_submission = submission.model_copy(
        update={
            "cases": tuple(
                bad_case if case.case_id == bad_case.case_id else case for case in submission.cases
            )
        }
    )
    with pytest.raises(ValidationError, match="outside the bound task case"):
        apply_agent_trajectory_submission(task, candidate, bad_submission)


def test_scoring_joins_adjudicated_quality_and_exposes_strict_outcomes() -> None:
    _, _, _, _, report = _score()

    assert report.overall.policy_compliance_rate == 1
    assert report.overall.evidence_inspection_rate == 1
    assert report.overall.claim_trace_rate == 1
    assert report.overall.strict_trajectory_pass_rate == 1
    assert report.overall.grounded_strict_pass_rate == 1
    assert report.overall.strict_end_to_end_pass_rate == 1
    assert report.grounded_review_status == "independently_adjudicated"
    assert report.execution_authorized is False
    assert AgentTrajectoryScoreReport.model_validate_json(report.model_dump_json()) == report
    markdown = render_agent_trajectory_markdown(report)
    assert "Promotion status: BLOCKED" in markdown
    assert "End-to-end pass" in markdown


def test_scoring_measures_policy_trace_and_grounded_failures_without_executing() -> None:
    _, _, _, _, report = _score(
        network_accessed=True,
        omit_inspection=True,
        grounded_strict=False,
    )
    answered = next(outcome for outcome in report.outcomes if outcome.response_status == "answered")

    assert answered.policy_compliant is False
    assert answered.evidence_inspection_rate == 0
    assert answered.claim_trace_rate == 1
    assert answered.strict_trajectory_pass is False
    assert answered.grounded_strict_pass is False
    assert answered.strict_end_to_end_pass is False
    assert report.promotion_status == "blocked"
    assert report.execution_authorized is False


def test_scoring_rejects_unadjudicated_and_identity_drift() -> None:
    task = _task()
    candidate = _candidate_batch(task)
    grounded = _grounded_report(task, candidate)
    trajectory = apply_agent_trajectory_submission(
        task, candidate, _trajectory_submission(task, candidate)
    )

    with pytest.raises(ValueError, match="independently adjudicated"):
        score_agent_trajectory(
            task,
            candidate,
            grounded.model_copy(update={"adjudication": None}),
            trajectory,
        )
    with pytest.raises(ValueError, match="exact task and candidate"):
        score_agent_trajectory(
            task,
            candidate,
            grounded,
            trajectory.model_copy(update={"candidate_batch_sha256": "f" * 64}),
        )

    answered = next(case for case in trajectory.cases if case.response_status == "answered")
    changed_case = answered.model_copy(update={"required_evidence_ids": ()})
    changed_cases = tuple(
        changed_case if case.case_id == changed_case.case_id else case for case in trajectory.cases
    )
    with pytest.raises(ValueError, match="protected task and candidate fields"):
        score_agent_trajectory(
            task,
            candidate,
            grounded,
            trajectory.model_copy(update={"cases": changed_cases}),
        )


def test_trajectory_case_rejects_impossible_step_timing() -> None:
    task = _task()
    candidate = _candidate_batch(task)
    submission = _trajectory_submission(task, candidate)
    answered = next(case for case in submission.cases if len(case.steps or ()) == 3)
    assert answered.steps is not None
    slow_step = answered.steps[0].model_copy(update={"duration_ms": 1000})
    changed = answered.model_copy(update={"steps": (slow_step, *answered.steps[1:])})
    changed_submission = submission.model_copy(
        update={
            "cases": tuple(
                changed if case.case_id == changed.case_id else case for case in submission.cases
            )
        }
    )

    with pytest.raises(ValidationError, match="durations cannot exceed"):
        apply_agent_trajectory_submission(task, candidate, changed_submission)


def test_trajectory_report_rejects_summary_not_derived_from_outcomes() -> None:
    *_, report = _score()
    changed_summary = report.overall.model_copy(update={"strict_end_to_end_pass_rate": 0.5})
    changed = report.model_copy(update={"overall": changed_summary})

    with pytest.raises(ValidationError, match="summary must match exact case outcomes"):
        AgentTrajectoryScoreReport.model_validate_json(changed.model_dump_json())


def test_drift_reports_stability_and_quality_regression() -> None:
    *_, baseline = _score()
    *_, stable_current = _score(captured_at=CAPTURED_AT + timedelta(days=1), seed="second")
    stable = compare_agent_trajectory_reports(
        baseline,
        stable_current,
        generated_at=CAPTURED_AT + timedelta(days=1, hours=3),
    )

    assert stable.status == "stable"
    assert stable.failed_metrics == ()
    assert AgentTrajectoryDriftReport.model_validate_json(stable.model_dump_json()) == stable
    assert "Drift status: STABLE" in render_agent_trajectory_drift_markdown(stable)

    *_, degraded = _score(
        captured_at=CAPTURED_AT + timedelta(days=2),
        seed="third",
        network_accessed=True,
        omit_inspection=True,
        grounded_strict=False,
    )
    drifted = compare_agent_trajectory_reports(
        stable_current,
        degraded,
        generated_at=CAPTURED_AT + timedelta(days=2, hours=3),
    )
    assert drifted.status == "drift_detected"
    assert "policy_compliance_rate" in drifted.failed_metrics
    assert "strict_end_to_end_pass_rate" in drifted.failed_metrics
    assert "DRIFT DETECTED" in render_agent_trajectory_drift_markdown(drifted)


def test_drift_rejects_incomparable_candidate_benchmark_and_time() -> None:
    *_, baseline = _score()
    *_, current = _score(captured_at=CAPTURED_AT + timedelta(days=1), seed="second")

    with pytest.raises(ValueError, match="distinct reports"):
        compare_agent_trajectory_reports(baseline, baseline)
    with pytest.raises(ValueError, match="same benchmark"):
        compare_agent_trajectory_reports(
            baseline,
            current.model_copy(update={"benchmark_id": "other-benchmark-v1"}),
        )
    with pytest.raises(ValueError, match="chronological"):
        compare_agent_trajectory_reports(
            baseline,
            current.model_copy(update={"captured_at": baseline.captured_at}),
        )


def test_default_policy_is_content_addressed_and_conservative() -> None:
    policy = build_default_agent_release_policy()
    repeated = build_default_agent_release_policy()
    checked_in = AgentReleaseThresholdPolicy.model_validate_json(
        (Path(__file__).parents[1] / "evals/agent-trajectories/release-policy-v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert policy == repeated
    assert checked_in == policy
    assert policy.policy_sha256 == agent_release_policy_sha256(policy)
    assert policy.min_trajectory_capture_count == 3
    assert policy.min_trajectory_policy_compliance_rate == 1
    assert policy.execution_enabled is False
    assert AgentReleaseThresholdPolicy.model_validate_json(policy.model_dump_json()) == policy

    payload = policy.model_dump(mode="python")
    payload["min_grounded_strict_pass_rate"] = 0.5
    with pytest.raises(ValidationError, match="SHA-256"):
        AgentReleaseThresholdPolicy.model_validate(payload)


def test_release_assessment_can_reach_human_review_but_never_execution() -> None:
    assessment = _eligible_assessment()

    assert assessment.status == "eligible_for_human_review"
    assert assessment.blocked_gate_count == 0
    assert assessment.human_approval_required is True
    assert assessment.execution_authorized is False
    assert AgentReleaseAssessment.model_validate_json(assessment.model_dump_json()) == assessment
    markdown = render_agent_release_markdown(assessment)
    assert "ELIGIBLE FOR HUMAN REVIEW" in markdown
    assert "execution is hard-disabled" in markdown


def test_release_assessment_loader_rejects_tampering_symlinks_and_oversize(
    tmp_path: Path,
) -> None:
    assessment = _eligible_assessment()
    assessment_path = tmp_path / "assessment.json"
    assessment_path.write_text(assessment.model_dump_json(), encoding="utf-8")

    assert load_agent_release_assessment(assessment_path) == assessment

    tampered = tmp_path / "tampered.json"
    tampered.write_text(
        assessment.model_dump_json().replace(
            '"status":"eligible_for_human_review"',
            '"status":"blocked"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="release status must match gate outcomes"):
        load_agent_release_assessment(tampered)

    linked = tmp_path / "linked.json"
    linked.symlink_to(assessment_path)
    with pytest.raises(ValueError, match="must not be a symlink"):
        load_agent_release_assessment(linked)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (AGENT_RELEASE_ASSESSMENT_MAX_BYTES + 1))
    with pytest.raises(ValueError, match="exceeds the 5 MB limit"):
        load_agent_release_assessment(oversized)


def test_release_assessment_blocks_incomplete_or_drifting_evidence() -> None:
    _, _, grounded_a, _, trajectory_a = _score()
    blocked = evaluate_agent_release(
        build_default_agent_release_policy(),
        _relationship_report(case_count=2),
        (grounded_a,),
        (trajectory_a,),
        (),
        generated_at=CAPTURED_AT + timedelta(hours=4),
    )
    assert blocked.status == "blocked"
    assert "relationship_sample_too_small" in blocked.blocking_reasons
    assert "trajectory_capture_count_too_small" in blocked.blocking_reasons
    assert "trajectory_drift_chain_incomplete" not in blocked.blocking_reasons

    _, _, grounded_b, _, trajectory_b = _score(
        captured_at=CAPTURED_AT + timedelta(days=1),
        seed="second",
        network_accessed=True,
    )
    policy = _relaxed_policy()
    drift = compare_agent_trajectory_reports(
        trajectory_a,
        trajectory_b,
        thresholds=policy.drift_thresholds,
    )
    blocked_drift = evaluate_agent_release(
        policy,
        _relationship_report(case_count=2),
        (grounded_a, grounded_b),
        (trajectory_a, trajectory_b),
        (drift,),
    )
    assert blocked_drift.status == "blocked"
    assert "trajectory_policy_compliance_below_threshold" in blocked_drift.blocking_reasons
    assert "trajectory_drift_detected" in blocked_drift.blocking_reasons
    assert blocked_drift.execution_authorized is False


def test_release_recomputes_exact_drift_instead_of_trusting_references() -> None:
    _, _, grounded_a, _, trajectory_a = _score()
    _, _, grounded_b, _, trajectory_b = _score(
        captured_at=CAPTURED_AT + timedelta(days=1), seed="second"
    )
    policy = _relaxed_policy()
    drift = compare_agent_trajectory_reports(
        trajectory_a,
        trajectory_b,
        thresholds=policy.drift_thresholds,
    )
    changed = drift.model_copy(update={"baseline_report_sha256": "f" * 64})

    assessment = evaluate_agent_release(
        policy,
        _relationship_report(case_count=2),
        (grounded_a, grounded_b),
        (trajectory_a, trajectory_b),
        (changed,),
    )

    assert assessment.status == "blocked"
    assert "trajectory_drift_chain_incomplete" in assessment.blocking_reasons
    assert assessment.execution_authorized is False


def test_release_rejects_missing_mismatched_or_extra_grounded_reports() -> None:
    _, _, grounded, _, trajectory = _score()
    policy = build_default_agent_release_policy()
    relationship = _relationship_report()

    with pytest.raises(ValueError, match="missing grounded report"):
        evaluate_agent_release(policy, relationship, (), (trajectory,), ())
    with pytest.raises(ValueError, match="does not match its exact grounded report"):
        evaluate_agent_release(
            policy,
            relationship,
            (grounded.model_copy(update={"task_sha256": "f" * 64}),),
            (trajectory,),
            (),
        )
    extra = grounded.model_copy(update={"report_id": "grounded-report-" + "f" * 20})
    with pytest.raises(ValueError, match="unreferenced"):
        evaluate_agent_release(policy, relationship, (grounded, extra), (trajectory,), ())


def test_trajectory_cli_runs_complete_offline_artifact_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path
    policy_path = root / "policy.json"
    assert trajectory_cli.run_cli(["write-policy", "--output", str(policy_path)]) == 0
    assert trajectory_cli._json_model(
        policy_path, AgentReleaseThresholdPolicy
    ).policy_id.startswith("release-policy-")
    assert "execution remains disabled" in capsys.readouterr().out

    task_a, candidate_a, grounded_a, trajectory_batch_a, report_a = _score()
    task_b, candidate_b, grounded_b, trajectory_batch_b, report_b = _score(
        captured_at=CAPTURED_AT + timedelta(days=1), seed="second"
    )
    policy = _relaxed_policy()
    drift = compare_agent_trajectory_reports(
        report_a,
        report_b,
        thresholds=policy.drift_thresholds,
        generated_at=CAPTURED_AT + timedelta(days=1, hours=3),
    )
    relationship = _relationship_report(case_count=2)
    submission = _trajectory_submission(task_a, candidate_a)

    paths = {
        "task-a.json": task_a,
        "task-b.json": task_b,
        "candidate-a.json": candidate_a,
        "candidate-b.json": candidate_b,
        "submission-a.json": submission,
        "grounded-a.json": grounded_a,
        "grounded-b.json": grounded_b,
        "trajectory-batch-a.json": trajectory_batch_a,
        "trajectory-batch-b.json": trajectory_batch_b,
        "trajectory-report-a.json": report_a,
        "trajectory-report-b.json": report_b,
        "drift.json": drift,
        "relationship.json": relationship,
        "relaxed-policy.json": policy,
    }
    for name in paths:
        (root / name).write_text("{}", encoding="utf-8")

    def fake_json_model(path: Path, _model: object) -> object:
        return paths[path.name]

    monkeypatch.setattr(trajectory_cli, "_json_model", fake_json_model)

    template_output = root / "template.json"
    assert (
        trajectory_cli.run_cli(
            [
                "template",
                "--task",
                str(root / "task-a.json"),
                "--candidate-batch",
                str(root / "candidate-a.json"),
                "--output",
                str(template_output),
            ]
        )
        == 0
    )
    assert "steps" in template_output.read_text(encoding="utf-8")

    imported_output = root / "imported.json"
    assert (
        trajectory_cli.run_cli(
            [
                "import",
                "--task",
                str(root / "task-a.json"),
                "--candidate-batch",
                str(root / "candidate-a.json"),
                "--submission",
                str(root / "submission-a.json"),
                "--output",
                str(imported_output),
            ]
        )
        == 0
    )
    assert "trajectory_batch_id" in imported_output.read_text(encoding="utf-8")

    score_json = root / "score.json"
    score_markdown = root / "score.md"
    assert (
        trajectory_cli.run_cli(
            [
                "score",
                "--task",
                str(root / "task-a.json"),
                "--candidate-batch",
                str(root / "candidate-a.json"),
                "--grounded-report",
                str(root / "grounded-a.json"),
                "--trajectory-batch",
                str(root / "trajectory-batch-a.json"),
                "--output-json",
                str(score_json),
                "--output-markdown",
                str(score_markdown),
            ]
        )
        == 0
    )
    assert "Promotion status: BLOCKED" in score_markdown.read_text(encoding="utf-8")

    drift_json = root / "drift-output.json"
    drift_markdown = root / "drift.md"
    assert (
        trajectory_cli.run_cli(
            [
                "drift",
                "--baseline",
                str(root / "trajectory-report-a.json"),
                "--current",
                str(root / "trajectory-report-b.json"),
                "--policy",
                str(root / "relaxed-policy.json"),
                "--output-json",
                str(drift_json),
                "--output-markdown",
                str(drift_markdown),
            ]
        )
        == 0
    )
    assert "Drift status: STABLE" in drift_markdown.read_text(encoding="utf-8")

    release_json = root / "release.json"
    release_markdown = root / "release.md"
    assert (
        trajectory_cli.run_cli(
            [
                "release",
                "--policy",
                str(root / "relaxed-policy.json"),
                "--relationship-report",
                str(root / "relationship.json"),
                "--grounded-report",
                str(root / "grounded-a.json"),
                "--grounded-report",
                str(root / "grounded-b.json"),
                "--trajectory-report",
                str(root / "trajectory-report-a.json"),
                "--trajectory-report",
                str(root / "trajectory-report-b.json"),
                "--drift-report",
                str(root / "drift.json"),
                "--output-json",
                str(release_json),
                "--output-markdown",
                str(release_markdown),
            ]
        )
        == 0
    )
    assert "ELIGIBLE FOR HUMAN REVIEW" in release_markdown.read_text(encoding="utf-8")
    assert 'execution_authorized": false' in release_json.read_text(encoding="utf-8")

    assert trajectory_cli.run_cli(["write-policy", "--output", str(policy_path)]) == 2
    assert "refusing to overwrite" in capsys.readouterr().err


def test_trajectory_cli_parser_requires_a_command() -> None:
    with pytest.raises(SystemExit):
        trajectory_cli._parser().parse_args([])

"""Gold-blind local NLI candidate runner tests."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
import pytest
from pydantic import ValidationError

import atlas_pulse.relationship_nli as relationship_nli
import atlas_pulse.relationship_nli_cli as relationship_nli_cli
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.relationship_evaluation.base import EventEvidence, relationship_case_id
from atlas_pulse.relationship_evaluation.candidates import (
    CandidateCaseInput,
    CandidateEvaluationTask,
    CandidateSystemDefinition,
    build_candidate_prediction_sheet,
)
from atlas_pulse.relationship_nli import (
    NliCandidateCaseResult,
    NliCandidateConfig,
    NliCandidateRunReport,
    NliModelOutput,
    build_candidate_system_definition,
    choose_relationship_label,
    model_artifact_sha256,
    nli_candidate_run_sha256,
    nli_input_template_sha256,
    render_candidate_prediction_csv,
    render_nli_pairs,
    run_nli_candidate,
    validate_nli_model_artifacts,
)
from atlas_pulse.relationship_nli_cli import cache_cli, run_cli
from atlas_pulse.relationships import ClaimPredicate


def _evidence(source: str, event_id: str, text: str) -> EventEvidence:
    return EventEvidence(
        node_id=f"{source}:{event_id}",
        source=source,
        event_id=event_id,
        event_type="weather.alert" if source == "nws" else "fire.thermal_anomaly",
        occurred_at=datetime(2026, 9, 14, 8, tzinfo=UTC),
        document_text=text,
        document_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def _task() -> CandidateEvaluationTask:
    left = _evidence(
        "firms",
        "thermal-one",
        "Place: Test County\nTitle: Thermal anomaly\n" + "A" * 800 + "\nTAIL-A",
    )
    right = _evidence(
        "nws",
        "alert-one",
        "Place: Test County\nHeadline: Wildfire warning\n" + "B" * 800 + "\nTAIL-B",
    )
    predicates: tuple[ClaimPredicate, ...] = (
        "hazard_domain",
        "evacuation_state",
        "road_access_state",
    )
    cases: list[CandidateCaseInput] = []
    for predicate in predicates:
        case_id = relationship_case_id(
            edge_id="edge-local-nli",
            predicate=predicate,
            left=left,
            right=right,
            distance_km=2.5,
            time_delta_minutes=8.0,
            left_geometry_basis="point",
            right_geometry_basis="polygon",
        )
        cases.append(
            CandidateCaseInput(
                case_id=case_id,
                edge_id="edge-local-nli",
                predicate=predicate,
                source_pair=("firms", "nws"),
                left=left,
                right=right,
                distance_km=2.5,
                time_delta_minutes=8.0,
                left_geometry_basis="point",
                right_geometry_basis="polygon",
            )
        )
    ordered = tuple(sorted(cases, key=lambda case: case.case_id))
    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "source_capture_sha256": "1" * 64,
        "benchmark_id": "live-claim-pairs.test",
        "rubric_version": "claim-pair-rubric-v1",
        "predicates": predicates,
        "case_count": len(ordered),
        "cases": [case.model_dump(mode="json", exclude_none=False) for case in ordered],
    }
    digest = canonical_sha256(payload)
    return CandidateEvaluationTask(
        task_id=f"candidate-task-{digest[:20]}",
        task_sha256=digest,
        **payload,
    )


def _config(*, warmup_runs: int = 1) -> NliCandidateConfig:
    return NliCandidateConfig(
        candidate_id="local-predicate-nli-v1",
        model_id="example/revision-pinned-nli",
        model_revision="a" * 40,
        model_license="apache-2.0",
        model_file="onnx/model.onnx",
        artifact_files=("config.json", "onnx/model.onnx", "tokenizer.json"),
        label_ids={"contradiction": 0, "entailment": 1, "neutral": 2},
        max_length=384,
        document_character_budget=640,
        pad_token_id=0,
        decision_threshold=0.7,
        minimum_margin=0.1,
        warmup_runs=warmup_runs,
        intra_op_num_threads=2,
    )


def _system(config: NliCandidateConfig) -> CandidateSystemDefinition:
    return build_candidate_system_definition(
        config,
        artifact_sha256="b" * 64,
        runtime_version="1.30.0",
        tokenizer_version="0.23.2",
        numpy_version="2.5.3",
    )


def _output(entailment: float, *, contradiction: float = 0.1) -> NliModelOutput:
    return NliModelOutput(
        contradiction_probability=contradiction,
        entailment_probability=entailment,
        neutral_probability=1 - entailment - contradiction,
        token_count=128,
        truncated=False,
    )


class _FakeRuntime:
    def __init__(
        self,
        system: CandidateSystemDefinition,
        outputs: Sequence[tuple[NliModelOutput, ...]],
    ) -> None:
        self._system = system
        self._outputs = list(outputs)
        self.calls: list[tuple[tuple[str, str], ...]] = []

    @property
    def system(self) -> CandidateSystemDefinition:
        return self._system

    def predict(self, pairs: Sequence[tuple[str, str]]) -> tuple[NliModelOutput, ...]:
        self.calls.append(tuple(pairs))
        if not self._outputs:
            raise AssertionError("fake runtime received an unexpected inference call")
        return self._outputs.pop(0)


def _runtime_outputs(*, include_warmup: bool) -> list[tuple[NliModelOutput, ...]]:
    outputs: list[tuple[NliModelOutput, ...]] = []
    if include_warmup:
        outputs.append((_output(0.6), _output(0.2)))
    outputs.extend(
        (
            (_output(0.82), _output(0.12)),
            (_output(0.08), _output(0.86)),
            (_output(0.55), _output(0.50)),
        )
    )
    return outputs


def _valid_run() -> tuple[CandidateEvaluationTask, NliCandidateConfig, NliCandidateRunReport]:
    task = _task()
    config = _config(warmup_runs=0)
    runtime = _FakeRuntime(_system(config), _runtime_outputs(include_warmup=False))
    ticks = iter((0.0, 0.01, 0.01, 0.03, 0.03, 0.06))
    run = run_nli_candidate(
        task,
        config,
        runtime,
        generated_at=datetime(2026, 9, 14, 9, tzinfo=UTC),
        clock=lambda: next(ticks),
    )
    return task, config, run


def test_candidate_run_is_predicate_conditioned_gold_blind_and_content_addressed() -> None:
    task = _task()
    config = _config()
    runtime = _FakeRuntime(_system(config), _runtime_outputs(include_warmup=True))
    ticks = iter((0.0, 0.01, 0.01, 0.03, 0.03, 0.06))
    run = run_nli_candidate(
        task,
        config,
        runtime,
        generated_at=datetime(2026, 9, 14, 9, tzinfo=UTC),
        clock=lambda: next(ticks),
    )

    assert run.run_sha256 == nli_candidate_run_sha256(run)
    assert run.run_id == f"candidate-run-{run.run_sha256[:20]}"
    assert run.promotion_status == "blocked"
    assert run.decision_counts == {
        "corroborates": 1,
        "contradicts": 1,
        "insufficient_evidence": 1,
    }
    assert [case.latency_ms for case in run.cases] == [10, 20, 30]
    assert len(runtime.calls) == 4
    assert all(len(call) == 2 for call in runtime.calls)
    first_premise, first_hypothesis = runtime.calls[1][0]
    assert "Thermal anomaly" in first_premise
    assert "Wildfire warning" in first_premise
    assert first_hypothesis != runtime.calls[1][1][1]
    assert runtime.calls[1][0][0] == runtime.calls[1][1][0]
    assert "source text truncated by candidate adapter" in first_premise
    assert "TAIL-A" in first_premise and "TAIL-B" in first_premise
    assert all(case.left_document_truncated for case in run.cases)
    assert all(case.right_document_truncated for case in run.cases)

    unbudgeted = render_nli_pairs(task.cases[0], document_character_budget=4_000)
    assert "source text truncated by candidate adapter" not in unbudgeted[0][0]

    serialized = run.model_dump_json()
    assert "gold_label" not in serialized
    assert "system_prediction" not in serialized
    assert "reviewer" not in run.model_dump(mode="json")
    csv_text = render_candidate_prediction_csv(task, run)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert {row["predicted_label"] for row in rows} == {
        "corroborates",
        "contradicts",
        "insufficient_evidence",
    }
    assert [float(row["latency_ms"]) for row in rows] == [10, 20, 30]


def test_candidate_decision_abstains_below_threshold_or_inside_margin() -> None:
    decisive = choose_relationship_label(
        _output(0.8),
        _output(0.2),
        decision_threshold=0.7,
        minimum_margin=0.1,
    )
    assert decisive[0] == "corroborates"

    below = choose_relationship_label(
        _output(0.69),
        _output(0.1),
        decision_threshold=0.7,
        minimum_margin=0.1,
    )
    assert below[0] == "insufficient_evidence"

    ambiguous = choose_relationship_label(
        _output(0.76),
        _output(0.70),
        decision_threshold=0.7,
        minimum_margin=0.1,
    )
    assert ambiguous[0] == "insufficient_evidence"

    with pytest.raises(ValidationError, match="sum to one"):
        NliModelOutput(
            contradiction_probability=0.5,
            entailment_probability=0.5,
            neutral_probability=0.5,
            token_count=3,
            truncated=False,
        )


def test_candidate_run_contract_rejects_tampered_trace_fields() -> None:
    _task_value, _config_value, run = _valid_run()
    case = run.cases[0]
    with pytest.raises(ValidationError, match="decision_score"):
        NliCandidateCaseResult.model_validate(
            {**case.model_dump(mode="python"), "decision_score": 0}
        )
    with pytest.raises(ValidationError, match="decision_margin"):
        NliCandidateCaseResult.model_validate(
            {**case.model_dump(mode="python"), "decision_margin": 0}
        )

    base = run.model_dump(mode="python")
    variants: tuple[tuple[dict[str, object], str], ...] = (
        ({**base, "generated_at": datetime(2026, 9, 14, 9)}, "timezone-aware"),
        ({**base, "case_count": 4}, "case_count"),
        ({**base, "cases": tuple(reversed(run.cases))}, "canonically ordered"),
        (
            {**base, "decision_counts": {"corroborates": 1, "contradicts": 2}},
            "contain every",
        ),
        (
            {
                **base,
                "decision_counts": {
                    "corroborates": -1,
                    "contradicts": 2,
                    "insufficient_evidence": 2,
                },
            },
            "non-negative",
        ),
        (
            {
                **base,
                "decision_counts": {
                    "corroborates": 1,
                    "contradicts": 1,
                    "insufficient_evidence": 2,
                },
            },
            "cover every case",
        ),
        (
            {
                **base,
                "decision_counts": {
                    "corroborates": 2,
                    "contradicts": 0,
                    "insufficient_evidence": 1,
                },
            },
            "match case outputs",
        ),
        ({**base, "run_sha256": "0" * 64}, "run_sha256"),
        ({**base, "run_id": "candidate-run-" + "0" * 20}, "run_id"),
        ({**base, "caveats": ("changed",)}, "closed promotion boundary"),
    )
    for payload, message in variants:
        with pytest.raises(ValidationError, match=message):
            NliCandidateRunReport.model_validate(payload)

    missing_policy = run.system.model_dump(mode="python")
    missing_policy["parameters"].pop("decision_threshold")
    with pytest.raises(ValidationError, match="missing numeric decision parameters"):
        NliCandidateRunReport.model_validate({**base, "system": missing_policy})

    wrong_label_cases = [item.model_dump(mode="python") for item in run.cases]
    wrong_label_cases[0]["predicted_label"], wrong_label_cases[1]["predicted_label"] = (
        wrong_label_cases[1]["predicted_label"],
        wrong_label_cases[0]["predicted_label"],
    )
    wrong_counts = Counter(item["predicted_label"] for item in wrong_label_cases)
    with pytest.raises(ValidationError, match="recorded decision policy"):
        NliCandidateRunReport.model_validate(
            {
                **base,
                "cases": wrong_label_cases,
                "decision_counts": {
                    label: wrong_counts[label]
                    for label in ("corroborates", "contradicts", "insufficient_evidence")
                },
            }
        )

    excessive_tokens = [item.model_dump(mode="python") for item in run.cases]
    excessive_tokens[0]["corroboration_hypothesis"]["token_count"] = 400
    with pytest.raises(ValidationError, match="exceeds the recorded maximum"):
        NliCandidateRunReport.model_validate({**base, "cases": excessive_tokens})

    wrong_template = run.system.model_dump(mode="python")
    wrong_template["input_template_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="executable input template"):
        NliCandidateRunReport.model_validate({**base, "system": wrong_template})


def _write_artifacts(model_dir: Path, config: NliCandidateConfig) -> None:
    for relative in config.artifact_files:
        path = model_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == "config.json":
            path.write_text(
                json.dumps(
                    {
                        "id2label": {
                            "0": "contradiction",
                            "1": "entailment",
                            "2": "neutral",
                        },
                        "pad_token_id": 0,
                        "max_position_embeddings": 512,
                    }
                ),
                encoding="utf-8",
            )
        else:
            path.write_bytes(f"artifact:{relative}".encode())


def test_model_artifact_identity_and_label_contract_fail_closed(tmp_path: Path) -> None:
    config = _config()
    model_dir = tmp_path / "model"
    _write_artifacts(model_dir, config)
    initial = validate_nli_model_artifacts(config, model_dir)
    assert initial == model_artifact_sha256(config, model_dir)
    (model_dir / "ignored.txt").write_text("not an inference input", encoding="utf-8")
    assert model_artifact_sha256(config, model_dir) == initial

    model_path = model_dir / config.model_file
    model_path.write_bytes(b"changed model bytes")
    assert model_artifact_sha256(config, model_dir) != initial

    (model_dir / "config.json").write_text(
        json.dumps({"id2label": {"0": "entailment", "1": "neutral", "2": "contradiction"}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="label mapping"):
        validate_nli_model_artifacts(config, model_dir)

    with pytest.raises(ValidationError, match="normalized relative paths"):
        NliCandidateConfig.model_validate(
            {**config.model_dump(mode="python"), "model_file": "../model.onnx"}
        )


def test_model_artifact_validation_rejects_filesystem_and_config_drift(tmp_path: Path) -> None:
    config = _config()
    not_directory = tmp_path / "not-a-directory"
    not_directory.write_text("file", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a directory"):
        model_artifact_sha256(config, not_directory)

    symlink_dir = tmp_path / "symlink-model"
    _write_artifacts(symlink_dir, config)
    external = tmp_path / "external.onnx"
    external.write_bytes(b"outside")
    model_path = symlink_dir / config.model_file
    model_path.unlink()
    model_path.symlink_to(external)
    with pytest.raises(ValueError, match="must not be a symlink"):
        model_artifact_sha256(config, symlink_dir)

    directory_dir = tmp_path / "directory-model"
    _write_artifacts(directory_dir, config)
    directory_model = directory_dir / config.model_file
    directory_model.unlink()
    directory_model.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        model_artifact_sha256(config, directory_dir)

    model_dir = tmp_path / "config-model"
    _write_artifacts(model_dir, config)
    config_path = model_dir / "config.json"
    invalid_configs: tuple[tuple[object, str], ...] = (
        ([], "must contain an object"),
        ({}, "must define id2label"),
        ({"id2label": {"0": 3}}, "string indexes"),
        (
            {
                "id2label": {"0": "contradiction", "1": "entailment", "2": "neutral"},
                "pad_token_id": 1,
                "max_position_embeddings": 512,
            },
            "pad_token_id",
        ),
        (
            {
                "id2label": {"0": "contradiction", "1": "entailment", "2": "neutral"},
                "pad_token_id": 0,
                "max_position_embeddings": 128,
            },
            "max_position_embeddings",
        ),
    )
    for model_config, message in invalid_configs:
        config_path.write_text(json.dumps(model_config), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            validate_nli_model_artifacts(config, model_dir)


def test_candidate_configuration_rejects_ambiguous_or_drifting_contracts() -> None:
    config = _config()
    base = config.model_dump(mode="python")
    invalid: tuple[tuple[dict[str, object], str], ...] = (
        ({**base, "model_id": "   "}, "must not be blank"),
        ({**base, "model_file": "onnx\\model.onnx"}, "forward slashes"),
        ({**base, "model_file": "onnx//model.onnx"}, "normalized relative paths"),
        (
            {**base, "artifact_files": ("config.json", "config.json", "tokenizer.json")},
            "must be unique",
        ),
        (
            {
                **base,
                "artifact_files": ("tokenizer.json", "config.json", "onnx/model.onnx"),
            },
            "canonical path order",
        ),
        (
            {**base, "artifact_files": ("config.json", "other.json", "tokenizer.json")},
            "must include the model",
        ),
        (
            {**base, "label_ids": {"contradiction": 0, "entailment": 1}},
            "must contain contradiction",
        ),
        (
            {
                **base,
                "label_ids": {"contradiction": 0, "entailment": 0, "neutral": 2},
            },
            "distinct non-negative",
        ),
        (
            {
                **base,
                "label_ids": {"contradiction": 1, "entailment": 2, "neutral": 3},
            },
            "indexes 0, 1, and 2",
        ),
        ({**base, "decision_threshold": 0.5, "minimum_margin": 0.6}, "cannot exceed"),
    )
    for payload, message in invalid:
        with pytest.raises(ValidationError, match=message):
            NliCandidateConfig.model_validate(payload)


class _FakeEncoding:
    def __init__(self, values: list[int], *, truncated: bool = False) -> None:
        self.ids = values
        self.attention_mask = [1] * len(values)
        self.type_ids = [0] * len(values)
        self.overflowing = [object()] if truncated else []


class _FakeTokenizer:
    loaded_path = ""
    truncation: tuple[int, str] | None = None

    @classmethod
    def from_file(cls, path: str) -> _FakeTokenizer:
        cls.loaded_path = path
        return cls()

    def enable_truncation(self, *, max_length: int, strategy: str) -> None:
        self.truncation = (max_length, strategy)

    def encode_batch(
        self,
        pairs: list[tuple[str, str]],
        *,
        add_special_tokens: bool,
    ) -> list[_FakeEncoding]:
        assert add_special_tokens is True
        assert len(pairs) == 2
        return [_FakeEncoding([1, 2, 3]), _FakeEncoding([4, 5], truncated=True)]


class _FakeSessionInput:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeSession:
    def __init__(self, input_names: tuple[str, ...]) -> None:
        self.input_names = input_names
        self.feeds: dict[str, np.ndarray[tuple[int, ...], np.dtype[np.int64]]] | None = None
        self.outputs: list[object] = [np.asarray([[0.0, 2.0, 1.0], [2.0, 0.0, 1.0]])]

    def get_inputs(self) -> list[_FakeSessionInput]:
        return [_FakeSessionInput(name) for name in self.input_names]

    def run(
        self,
        _output_names: None,
        feeds: dict[str, np.ndarray[tuple[int, ...], np.dtype[np.int64]]],
    ) -> list[object]:
        self.feeds = feeds
        return self.outputs


class _FakeSessionOptions:
    intra_op_num_threads = 0


def test_onnx_runtime_is_cpu_only_padded_and_three_way(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    model_dir = tmp_path / "model"
    _write_artifacts(model_dir, config)
    sessions: list[_FakeSession] = []
    calls: list[tuple[object, ...]] = []

    def session_factory(*args: object, **kwargs: object) -> _FakeSession:
        calls.append((*args, kwargs))
        session = _FakeSession(("input_ids", "attention_mask", "token_type_ids"))
        sessions.append(session)
        return session

    monkeypatch.setattr(ort, "SessionOptions", _FakeSessionOptions)
    monkeypatch.setattr(ort, "InferenceSession", session_factory)
    monkeypatch.setattr(relationship_nli, "Tokenizer", _FakeTokenizer)
    versions = {"onnxruntime": "1.30.0", "tokenizers": "0.23.2", "numpy": "2.5.3"}
    monkeypatch.setattr(relationship_nli, "version", versions.__getitem__)

    runtime = relationship_nli.OnnxNliRuntime(config, model_dir)
    assert runtime.system.runtime == "onnxruntime-cpu+tokenizers+numpy"
    assert runtime.system.parameters["cpu_only"] is True
    assert _FakeTokenizer.loaded_path == str(model_dir / "tokenizer.json")
    assert calls[0][-1] == {
        "sess_options": cast(dict[str, object], calls[0][-1])["sess_options"],
        "providers": ["CPUExecutionProvider"],
    }

    outputs = runtime.predict((("record a", "same claim"), ("record b", "opposite claim")))
    assert len(outputs) == 2
    assert outputs[0].entailment_probability > outputs[0].neutral_probability
    assert outputs[1].contradiction_probability > outputs[1].neutral_probability
    assert outputs[1].truncated is True
    feeds = sessions[0].feeds
    assert feeds is not None
    assert feeds["input_ids"].tolist() == [[1, 2, 3], [4, 5, 0]]
    assert feeds["attention_mask"].tolist() == [[1, 1, 1], [1, 1, 0]]
    assert feeds["token_type_ids"].tolist() == [[0, 0, 0], [0, 0, 0]]

    with pytest.raises(ValueError, match="at least one"):
        runtime.predict(())
    sessions[0].outputs = []
    with pytest.raises(RuntimeError, match="no outputs"):
        runtime.predict((("a", "b"), ("c", "d")))
    sessions[0].outputs = [np.asarray([[1.0, 2.0]])]
    with pytest.raises(RuntimeError, match="invalid three-way logits"):
        runtime.predict((("a", "b"), ("c", "d")))


def test_onnx_runtime_rejects_unknown_input_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    model_dir = tmp_path / "model"
    _write_artifacts(model_dir, config)
    monkeypatch.setattr(ort, "SessionOptions", _FakeSessionOptions)
    monkeypatch.setattr(
        ort,
        "InferenceSession",
        lambda *_args, **_kwargs: _FakeSession(("input_ids", "position_ids")),
    )
    monkeypatch.setattr(relationship_nli, "Tokenizer", _FakeTokenizer)

    with pytest.raises(ValueError, match="unsupported input contract"):
        relationship_nli.OnnxNliRuntime(config, model_dir)


def test_candidate_runtime_identity_and_batch_completeness_fail_closed() -> None:
    task = _task()
    config = _config(warmup_runs=0)
    system = _system(config)
    base = system.model_dump(mode="python")
    variants: tuple[tuple[dict[str, object], str], ...] = (
        ({**base, "candidate_id": "different-candidate"}, "candidate_id"),
        ({**base, "model_id": "different/model"}, "model identity"),
        ({**base, "adapter_version": "different-adapter-v1"}, "adapter version"),
        ({**base, "input_template_sha256": "0" * 64}, "template hash"),
        ({**base, "runtime": "remote-runtime"}, "CPU-only"),
        (
            {**base, "parameters": {**system.parameters, "max_length": 256}},
            "parameters",
        ),
    )
    for payload, message in variants:
        changed = CandidateSystemDefinition.model_validate(payload)
        with pytest.raises(ValueError, match=message):
            run_nli_candidate(task, config, _FakeRuntime(changed, ()))

    warmup_config = _config(warmup_runs=1)
    with pytest.raises(RuntimeError, match="warm-up"):
        run_nli_candidate(
            task,
            warmup_config,
            _FakeRuntime(_system(warmup_config), ((_output(0.8),),)),
        )
    with pytest.raises(RuntimeError, match="one output per"):
        run_nli_candidate(
            task,
            config,
            _FakeRuntime(_system(config), ((_output(0.8),),)),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        run_nli_candidate(
            task,
            config,
            _FakeRuntime(_system(config), _runtime_outputs(include_warmup=False)),
            generated_at=datetime(2026, 9, 14, 9),
        )

    valid_task, _valid_config, run = _valid_run()
    changed_task = valid_task.model_copy(update={"task_id": "candidate-task-" + "0" * 20})
    with pytest.raises(ValueError, match="does not match the exact task"):
        render_candidate_prediction_csv(changed_task, run)
    incomplete_run = run.model_copy(update={"cases": run.cases[:-1]})
    with pytest.raises(ValueError, match="do not exactly cover"):
        render_candidate_prediction_csv(valid_task, incomplete_run)


def test_checked_in_candidate_is_revision_pinned_and_hashes_template() -> None:
    path = (
        Path(__file__).parents[1]
        / "evals/relationships/candidates/deberta-v3-small-predicate-nli-v1.json"
    )
    config = NliCandidateConfig.model_validate_json(path.read_text(encoding="utf-8"))

    assert config.model_id == "cross-encoder/nli-deberta-v3-small"
    assert config.model_revision == "fa2804872c3b4bd748f38c0185cc85775361e735"
    assert config.model_license == "apache-2.0"
    assert config.model_file == "onnx/model_quint8_avx2.onnx"
    assert config.document_character_budget == 640
    assert config.pad_token_id == 0
    assert config.decision_threshold == 0.7
    assert config.minimum_margin == 0.1
    assert len(nli_input_template_sha256()) == 64


def test_runner_cli_protects_blank_task_and_writes_auditable_outputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task = _task()
    config = _config(warmup_runs=0)
    task_path = tmp_path / "task.json"
    template_path = tmp_path / "predictions-template.csv"
    config_path = tmp_path / "candidate.json"
    task_path.write_text(task.model_dump_json(indent=2), encoding="utf-8")
    template_path.write_text(build_candidate_prediction_sheet(task).content, encoding="utf-8")
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    predictions_path = tmp_path / "predictions.csv"
    definition_path = tmp_path / "definition.json"
    run_path = tmp_path / "run.json"
    runtimes: list[_FakeRuntime] = []

    def runtime_factory(
        supplied_config: NliCandidateConfig,
        _model_dir: Path,
    ) -> _FakeRuntime:
        runtime = _FakeRuntime(
            _system(supplied_config),
            _runtime_outputs(include_warmup=False),
        )
        runtimes.append(runtime)
        return runtime

    arguments = [
        "--task",
        str(task_path),
        "--predictions-template",
        str(template_path),
        "--candidate-config",
        str(config_path),
        "--model-dir",
        str(tmp_path / "unused-by-fake"),
        "--output-predictions",
        str(predictions_path),
        "--output-definition",
        str(definition_path),
        "--output-run",
        str(run_path),
    ]
    assert run_cli(arguments, runtime_factory=runtime_factory) == 0
    assert "Wrote blocked candidate-run-" in capsys.readouterr().out
    assert len(runtimes) == 1
    assert all(row["predicted_label"] for row in csv.DictReader(predictions_path.open()))
    assert CandidateSystemDefinition.model_validate_json(
        definition_path.read_text(encoding="utf-8")
    ) == _system(config)
    run = NliCandidateRunReport.model_validate_json(run_path.read_text(encoding="utf-8"))
    assert run.task_sha256 == task.task_sha256

    assert run_cli(arguments, runtime_factory=runtime_factory) == 2
    assert "refusing to overwrite" in capsys.readouterr().err

    duplicate_outputs = [
        *arguments,
        "--output-predictions",
        str(tmp_path / "duplicate-output"),
        "--output-definition",
        str(tmp_path / "duplicate-output"),
        "--output-run",
        str(tmp_path / "duplicate-output"),
    ]
    assert run_cli(duplicate_outputs, runtime_factory=runtime_factory) == 2
    assert "output paths must be distinct" in capsys.readouterr().err

    replaces_input = [
        *arguments,
        "--output-predictions",
        str(task_path),
        "--output-definition",
        str(tmp_path / "safe-definition.json"),
        "--output-run",
        str(tmp_path / "safe-run.json"),
    ]
    assert run_cli(replaces_input, runtime_factory=runtime_factory) == 2
    assert "must not replace input" in capsys.readouterr().err

    tampered_template = tmp_path / "tampered.csv"
    tampered_template.write_text(
        template_path.read_text(encoding="utf-8").replace(
            ",predicted_label,latency_ms\n", ",predicted_label,latency_ms\n", 1
        )
        + "unexpected-row\n",
        encoding="utf-8",
    )
    second_arguments = [
        *arguments,
        "--predictions-template",
        str(tampered_template),
        "--output-predictions",
        str(tmp_path / "second-predictions.csv"),
        "--output-definition",
        str(tmp_path / "second-definition.json"),
        "--output-run",
        str(tmp_path / "second-run.json"),
    ]
    assert run_cli(second_arguments, runtime_factory=runtime_factory) == 2
    assert "exact blank sheet" in capsys.readouterr().err
    assert len(runtimes) == 1


def test_cache_cli_downloads_exact_revision_and_validates_bytes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config()
    config_path = tmp_path / "candidate.json"
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    model_dir = tmp_path / "model"
    calls: list[dict[str, object]] = []

    def downloader(**kwargs: object) -> str:
        calls.append(dict(kwargs))
        local_dir = Path(cast(str, kwargs["local_dir"]))
        _write_artifacts(local_dir, config)
        return str(local_dir)

    assert (
        cache_cli(
            ["--candidate-config", str(config_path), "--output", str(model_dir)],
            downloader=downloader,
        )
        == 0
    )
    assert calls == [
        {
            "repo_id": config.model_id,
            "revision": config.model_revision,
            "allow_patterns": list(config.artifact_files),
            "local_dir": str(model_dir),
        }
    ]
    output = capsys.readouterr().out
    assert config.model_revision in output
    assert validate_nli_model_artifacts(config, model_dir) in output

    def failing_downloader(**_kwargs: object) -> str:
        raise OSError("network unavailable")

    assert (
        cache_cli(
            [
                "--candidate-config",
                str(config_path),
                "--output",
                str(tmp_path / "failed-model"),
            ],
            downloader=failing_downloader,
        )
        == 2
    )
    assert "network unavailable" in capsys.readouterr().err


def test_installed_cli_entry_points_return_exit_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relationship_nli_cli, "run_cli", lambda: 7)
    with pytest.raises(SystemExit, match="7"):
        relationship_nli_cli.main()
    monkeypatch.setattr(relationship_nli_cli, "cache_cli", lambda: 8)
    with pytest.raises(SystemExit, match="8"):
        relationship_nli_cli.cache_main()

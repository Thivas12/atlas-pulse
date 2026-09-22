"""Pinned, offline grounded-answer runner and fail-closed boundary tests."""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError

import atlas_pulse.grounded_answer_runner as runner_module
from atlas_pulse.evaluation.metrics import canonical_sha256
from atlas_pulse.evidence_packs import EvidencePackStatus
from atlas_pulse.grounded_answer_evaluation import (
    GroundedAnswerClaim,
    GroundedAnswerEvidence,
    GroundedAnswerResponse,
    GroundedAnswerTask,
    GroundedAnswerTaskCase,
    grounded_answer_case_sha256,
    grounded_answer_task_sha256,
)
from atlas_pulse.grounded_answer_runner import (
    GROUNDING_RUNNER_TEMPLATE_VERSION,
    GroundedAnswerCandidateRun,
    GroundedAnswerGeneration,
    GroundedAnswerRunnerConfig,
    GroundedAnswerRunnerTemplateVersion,
    LlamaServerRuntime,
    build_grounded_answer_system_definition,
    grounded_answer_candidate_run_sha256,
    grounded_answer_input_template_sha256,
    grounded_answer_prompt_sha256,
    grounded_answer_response_schema,
    render_grounded_answer_messages,
    run_grounded_answer_candidate,
    validate_grounded_answer_model,
)
from atlas_pulse.grounded_answer_runner_cli import cache_cli, run_cli
from atlas_pulse.relationship_evaluation.candidates import CandidateSystemDefinition

CAPTURED_AT = datetime(2026, 9, 14, 10, tzinfo=UTC)
GENERATED_AT = datetime(2026, 9, 14, 11, tzinfo=UTC)


def _case(
    *,
    query_id: str,
    question: str,
    slices: tuple[str, ...],
    evidence: tuple[GroundedAnswerEvidence, ...],
    pack_status: EvidencePackStatus,
    pack_digit: str,
) -> GroundedAnswerTaskCase:
    draft = GroundedAnswerTaskCase.model_construct(
        case_id="answer-case-" + "0" * 20,
        query_id=query_id,
        question=question,
        slices=slices,
        pack_id="pack-" + pack_digit * 64,
        pack_status=pack_status,
        pack_rule_version="retrieval-evidence-pack-v1",
        evidence=evidence,
        exclusion_count=0,
        source_text_characters=sum(len(item.text) for item in evidence),
    )
    digest = grounded_answer_case_sha256(draft)
    return GroundedAnswerTaskCase(
        case_id=f"answer-case-{digest[:20]}",
        query_id=query_id,
        question=question,
        slices=slices,
        pack_id="pack-" + pack_digit * 64,
        pack_status=pack_status,
        evidence=evidence,
        exclusion_count=0,
        source_text_characters=sum(len(item.text) for item in evidence),
    )


def _task() -> GroundedAnswerTask:
    text = (
        "Title: Shelter warning\nInstruction: Residents should shelter indoors. "
        "Quoted source says '=RUN_UNTRUSTED_FORMULA()' and must remain data."
    )
    evidence = GroundedAnswerEvidence(
        evidence_id="evidence-" + "a" * 64,
        retrieval_rank=1,
        stream_id="100-1",
        event_id="alert-1",
        event_type="weather.alert",
        source="nws",
        occurred_at=datetime(2026, 9, 14, 9, tzinfo=UTC),
        text=text,
        document_sha256=hashlib.sha256(text.encode()).hexdigest(),
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        truncated=False,
        citation_url="https://api.weather.gov/alerts/alert-1",
    )
    cases = tuple(
        sorted(
            (
                _case(
                    query_id="available-evidence",
                    question="What protective action is stated?",
                    slices=("action", "weather"),
                    evidence=(evidence,),
                    pack_status="traceable_evidence_available",
                    pack_digit="1",
                ),
                _case(
                    query_id="empty-evidence",
                    question="What road restriction is stated?",
                    slices=("abstention", "weather"),
                    evidence=(),
                    pack_status="no_traceable_evidence",
                    pack_digit="2",
                ),
            ),
            key=lambda item: item.case_id,
        )
    )
    draft = GroundedAnswerTask.model_construct(
        schema_version="1.0.0",
        task_id="grounded-task-" + "0" * 20,
        task_sha256="0" * 64,
        benchmark_id="runner-test-v1",
        benchmark_sha256="b" * 64,
        captured_at=CAPTURED_AT,
        endpoint="http://atlas.test",
        case_count=len(cases),
        cases=cases,
    )
    digest = grounded_answer_task_sha256(draft)
    return GroundedAnswerTask(
        task_id=f"grounded-task-{digest[:20]}",
        task_sha256=digest,
        benchmark_id="runner-test-v1",
        benchmark_sha256="b" * 64,
        captured_at=CAPTURED_AT,
        endpoint="http://atlas.test",
        case_count=len(cases),
        cases=cases,
    )


def _config(
    *,
    model_sha256: str = "c" * 64,
    template_version: GroundedAnswerRunnerTemplateVersion = GROUNDING_RUNNER_TEMPLATE_VERSION,
) -> GroundedAnswerRunnerConfig:
    return GroundedAnswerRunnerConfig(
        candidate_id="local-grounded-runner-v1",
        model_id="example/revision-pinned-gguf",
        model_revision="d" * 40,
        model_license="apache-2.0",
        model_file="model.gguf",
        model_file_sha256=model_sha256,
        adapter_version=template_version,
        template_version=template_version,
        context_length=4096,
        max_output_tokens=256,
        temperature=0.7,
        top_p=0.8,
        top_k=20,
        min_p=0,
        presence_penalty=1.5,
        seed=42,
        threads=2,
        startup_timeout_seconds=5,
        request_timeout_seconds=10,
    )


def _system(config: GroundedAnswerRunnerConfig) -> CandidateSystemDefinition:
    return build_grounded_answer_system_definition(
        config,
        executable_sha256="e" * 64,
        runtime_version="llama.cpp test-build",
    )


def _response(case: GroundedAnswerTaskCase) -> GroundedAnswerResponse:
    if not case.evidence:
        return GroundedAnswerResponse(
            status="abstained",
            abstention_reason="no_traceable_evidence",
        )
    return GroundedAnswerResponse(
        status="answered",
        claims=(
            GroundedAnswerClaim(
                claim_id="claim-01",
                text="Residents should shelter indoors.",
                evidence_ids=(case.evidence[0].evidence_id,),
            ),
        ),
    )


class _FakeRuntime:
    def __init__(
        self,
        config: GroundedAnswerRunnerConfig,
        *,
        prompt_hash: str | None = None,
        input_tokens: int = 100,
        output_tokens: int = 20,
        response_factory: Any = _response,
        system: CandidateSystemDefinition | None = None,
    ) -> None:
        self._system = system or _system(config)
        self.template_version = config.template_version
        self.prompt_hash = prompt_hash
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.response_factory = response_factory
        self.calls: list[str] = []

    @property
    def system(self) -> CandidateSystemDefinition:
        return self._system

    def generate(self, case: GroundedAnswerTaskCase) -> GroundedAnswerGeneration:
        self.calls.append(case.case_id)
        response = cast(GroundedAnswerResponse, self.response_factory(case))
        return GroundedAnswerGeneration(
            response=response,
            response_sha256=canonical_sha256(response),
            prompt_sha256=self.prompt_hash
            or grounded_answer_prompt_sha256(case, self.template_version),
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            latency_ms=12.5,
        )


def test_prompt_is_gold_free_injection_quoted_and_case_constrained() -> None:
    task = _task()
    available = next(case for case in task.cases if case.evidence)
    empty = next(case for case in task.cases if not case.evidence)
    messages = render_grounded_answer_messages(available)
    assert messages[0]["role"] == "system"
    assert "untrusted quoted data" in messages[0]["content"]
    assert "=RUN_UNTRUSTED_FORMULA()" in messages[1]["content"]
    assert available.evidence[0].evidence_id in messages[1]["content"]
    assert "reviewer" not in json.dumps(messages).casefold()
    assert "gold_label" not in json.dumps(messages).casefold()

    schema = grounded_answer_response_schema(available)
    schema_text = json.dumps(schema)
    assert available.evidence[0].evidence_id in schema_text
    assert "no_traceable_evidence" not in schema_text
    answered_schema = cast(list[dict[str, Any]], schema["oneOf"])[0]
    claims_schema = answered_schema["properties"]["claims"]
    assert claims_schema["maxItems"] == 3
    claim_properties = claims_schema["items"]["properties"]
    assert claim_properties["text"]["maxLength"] == 180
    assert claim_properties["evidence_ids"]["maxItems"] == min(
        2,
        len(available.evidence),
    )
    empty_schema = grounded_answer_response_schema(empty)
    assert empty_schema["properties"]["abstention_reason"]["enum"] == [  # type: ignore[index]
        "no_traceable_evidence"
    ]
    assert len(grounded_answer_prompt_sha256(available)) == 64
    assert len(grounded_answer_input_template_sha256()) == 64


def test_v3_prompt_narrows_abstention_without_changing_v2_identity() -> None:
    available = next(case for case in _task().cases if case.evidence)
    v2: GroundedAnswerRunnerTemplateVersion = "grounded-brief-qwen3-v2"
    v3: GroundedAnswerRunnerTemplateVersion = "grounded-brief-qwen3-v3"

    v2_messages = render_grounded_answer_messages(available, v2)
    v3_messages = render_grounded_answer_messages(available, v3)

    assert (
        "excerpts are absent, insufficient, or materially conflicting" in v2_messages[0]["content"]
    )
    assert "zero responsive claims are possible" not in v2_messages[1]["content"]
    assert "supported negative finding is still an answer" in v3_messages[1]["content"]
    assert "zero responsive claims are possible" in v3_messages[1]["content"]
    assert grounded_answer_input_template_sha256(v2) == (
        "e8dfd7aba49e02d4d73b84441ffb1e217ed2a681d4c1804935359d3e36fc148f"
    )
    assert grounded_answer_prompt_sha256(available, v2) == (
        "6f4936cb40acc702a3ae6f72c28be858c8ccc0b2d4d2994262a8aa3f0031fde4"
    )
    assert grounded_answer_input_template_sha256(v2) != grounded_answer_input_template_sha256(v3)
    assert grounded_answer_prompt_sha256(available, v2) != grounded_answer_prompt_sha256(
        available,
        v3,
    )

    legacy_config = _config(template_version=v2)
    _submission, legacy_run, _batch = run_grounded_answer_candidate(
        _task(),
        legacy_config,
        _FakeRuntime(legacy_config),
        generated_at=GENERATED_AT,
    )
    assert legacy_run.template_version == v2


def test_runner_produces_complete_submission_batch_and_blocked_trace() -> None:
    task = _task()
    config = _config()
    runtime = _FakeRuntime(config)
    submission, run, batch = run_grounded_answer_candidate(
        task,
        config,
        runtime,
        generated_at=GENERATED_AT,
    )

    assert runtime.calls == [case.case_id for case in task.cases]
    assert run.run_sha256 == grounded_answer_candidate_run_sha256(run)
    assert run.run_id == f"grounded-run-{run.run_sha256[:20]}"
    assert run.response_counts == {"answered": 1, "abstained": 1}
    assert run.promotion_status == "blocked"
    assert batch.promotion_status == "blocked"
    assert batch.system == run.system
    assert submission.task_sha256 == task.task_sha256
    assert all(case.response is not None for case in submission.cases)
    serialized = run.model_dump_json()
    assert "reviewer" not in serialized
    assert "support_grade" not in serialized
    assert "answer_relevance" not in serialized
    assert run.system.parameters["external_network_access"] is False
    assert run.system.parameters["tool_access"] is False
    assert run.system.parameters["thinking"] is False


def test_runner_rejects_runtime_prompt_citation_budget_and_identity_drift() -> None:
    task = _task()
    config = _config()
    with pytest.raises(ValueError, match="prompt hash"):
        run_grounded_answer_candidate(
            task,
            config,
            _FakeRuntime(config, prompt_hash="0" * 64),
        )
    with pytest.raises(ValueError, match="context length"):
        run_grounded_answer_candidate(
            task,
            config,
            _FakeRuntime(config, input_tokens=4000),
        )
    with pytest.raises(ValueError, match="output token count"):
        run_grounded_answer_candidate(
            task,
            config,
            _FakeRuntime(config, output_tokens=257),
        )

    def foreign_citation(case: GroundedAnswerTaskCase) -> GroundedAnswerResponse:
        if not case.evidence:
            return _response(case)
        return GroundedAnswerResponse(
            status="answered",
            claims=(
                GroundedAnswerClaim(
                    claim_id="claim-01",
                    text="An unsupported foreign citation.",
                    evidence_ids=("evidence-" + "f" * 64,),
                ),
            ),
        )

    with pytest.raises(ValueError, match="outside the exact task"):
        run_grounded_answer_candidate(
            task,
            config,
            _FakeRuntime(config, response_factory=foreign_citation),
        )

    system_payload = _system(config).model_dump(mode="python")
    system_payload["parameters"]["seed"] = 7
    with pytest.raises(ValueError, match="parameters do not match"):
        run_grounded_answer_candidate(
            task,
            config,
            _FakeRuntime(
                config,
                system=CandidateSystemDefinition.model_validate(system_payload),
            ),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        run_grounded_answer_candidate(
            task,
            config,
            _FakeRuntime(config),
            generated_at=datetime(2026, 9, 14, 11),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ({"candidate_id": "different-candidate-v1"}, "candidate_id"),
        ({"model_id": "different/model"}, "model identity"),
        ({"model_artifact_sha256": "a" * 64}, "model artifact"),
        ({"adapter_version": "different-adapter-v1"}, "adapter"),
        ({"input_template_sha256": "a" * 64}, "template hash"),
        ({"runtime": "different-runtime"}, "CPU-only"),
    ),
)
def test_runner_rejects_each_protected_runtime_identity(
    mutation: dict[str, object],
    message: str,
) -> None:
    task = _task()
    config = _config()
    payload = {**_system(config).model_dump(mode="python"), **mutation}
    with pytest.raises(ValueError, match=message):
        run_grounded_answer_candidate(
            task,
            config,
            _FakeRuntime(config, system=CandidateSystemDefinition.model_validate(payload)),
        )


def test_runner_requires_runtime_executable_identity() -> None:
    task = _task()
    config = _config()
    payload = _system(config).model_dump(mode="python")
    payload["parameters"].pop("llama_server_executable_sha256")
    with pytest.raises(ValueError, match="executable identity"):
        run_grounded_answer_candidate(
            task,
            config,
            _FakeRuntime(config, system=CandidateSystemDefinition.model_validate(payload)),
        )


def test_runner_and_generation_contracts_reject_tampering() -> None:
    task = _task()
    config = _config()
    _submission, run, _batch = run_grounded_answer_candidate(
        task,
        config,
        _FakeRuntime(config),
        generated_at=GENERATED_AT,
    )
    generation = _FakeRuntime(config).generate(task.cases[0])
    with pytest.raises(ValidationError, match="response_sha256"):
        GroundedAnswerGeneration.model_validate(
            {**generation.model_dump(mode="python"), "response_sha256": "0" * 64}
        )

    base = run.model_dump(mode="python")
    variants: tuple[tuple[dict[str, object], str], ...] = (
        ({**base, "generated_at": datetime(2026, 9, 14, 11)}, "timezone-aware"),
        ({**base, "case_count": 3}, "case_count"),
        ({**base, "cases": tuple(reversed(run.cases))}, "canonically ordered"),
        ({**base, "response_counts": {"answered": 2}}, "both response statuses"),
        (
            {**base, "response_counts": {"answered": -1, "abstained": 3}},
            "non-negative",
        ),
        (
            {**base, "response_counts": {"answered": 2, "abstained": 0}},
            "match case responses",
        ),
        ({**base, "run_sha256": "0" * 64}, "run_sha256"),
        ({**base, "run_id": "grounded-run-" + "0" * 20}, "run_id"),
        ({**base, "caveats": ("changed",)}, "closed promotion boundary"),
    )
    for payload, message in variants:
        with pytest.raises(ValidationError, match=message):
            GroundedAnswerCandidateRun.model_validate(payload)

    missing_limits = run.system.model_dump(mode="python")
    missing_limits["parameters"].pop("context_length")
    with pytest.raises(ValidationError, match="missing integer token limits"):
        GroundedAnswerCandidateRun.model_validate({**base, "system": missing_limits})


def test_model_and_system_identity_fail_closed(tmp_path: Path) -> None:
    model_bytes = b"small fake GGUF bytes"
    digest = hashlib.sha256(model_bytes).hexdigest()
    config = _config(model_sha256=digest)
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    model_path = model_dir / config.model_file
    model_path.write_bytes(model_bytes)
    assert validate_grounded_answer_model(config, model_dir) == model_path

    system = build_grounded_answer_system_definition(
        config,
        executable_sha256="e" * 64,
        runtime_version=" llama.cpp   build-42 ",
    )
    assert system.runtime == "llama.cpp-server-cpu"
    assert system.model_artifact_sha256 == digest
    assert system.parameters["tokenizer_artifact_sha256"] == digest
    assert (
        system.parameters["structured_output_transport"] == "llama.cpp-sse-json-schema-wrapper-v1"
    )
    assert (
        system.parameters["token_count_transport"]
        == "llama.cpp-chat-input-tokens-without-response-format-v1"
    )
    assert system.runtime_version.startswith("llama.cpp build-42;")

    model_path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_grounded_answer_model(config, model_dir)
    with pytest.raises(ValueError, match="lowercase hexadecimal"):
        build_grounded_answer_system_definition(
            config,
            executable_sha256="not-a-hash",
            runtime_version="build",
        )
    with pytest.raises(ValidationError, match="normalized relative paths"):
        GroundedAnswerRunnerConfig.model_validate(
            {**config.model_dump(mode="python"), "model_file": "../model.gguf"}
        )
    for invalid_path, message in (
        ("nested\\model.gguf", "portable forward slashes"),
        ("nested//model.gguf", "normalized relative paths"),
    ):
        with pytest.raises(ValidationError, match=message):
            GroundedAnswerRunnerConfig.model_validate(
                {**config.model_dump(mode="python"), "model_file": invalid_path}
            )
    with pytest.raises(ValidationError, match="must not be blank"):
        GroundedAnswerRunnerConfig.model_validate(
            {**config.model_dump(mode="python"), "model_id": "   "}
        )
    with pytest.raises(ValidationError, match="smaller than context_length"):
        GroundedAnswerRunnerConfig.model_validate(
            {
                **config.model_dump(mode="python"),
                "context_length": 1024,
                "max_output_tokens": 1024,
            }
        )
    for runtime_version in (" ", "x" * 121):
        with pytest.raises(ValueError, match="1-120 normalized characters"):
            build_grounded_answer_system_definition(
                config,
                executable_sha256="e" * 64,
                runtime_version=runtime_version,
            )


def test_model_artifact_rejects_non_directory_symlink_and_directory(tmp_path: Path) -> None:
    model_bytes = b"model"
    config = _config(model_sha256=hashlib.sha256(model_bytes).hexdigest())
    regular_file = tmp_path / "not-a-directory"
    regular_file.write_bytes(model_bytes)
    with pytest.raises(ValueError, match="must be a directory"):
        validate_grounded_answer_model(config, regular_file)

    model_dir = tmp_path / "models"
    model_dir.mkdir()
    target = tmp_path / "target.gguf"
    target.write_bytes(model_bytes)
    (model_dir / config.model_file).symlink_to(target)
    with pytest.raises(ValueError, match="must not be a symlink"):
        validate_grounded_answer_model(config, model_dir)

    (model_dir / config.model_file).unlink()
    (model_dir / config.model_file).mkdir()
    with pytest.raises(ValueError, match="regular file inside model-dir"):
        validate_grounded_answer_model(config, model_dir)


class _FakeResponse:
    def __init__(
        self,
        payload: object,
        *,
        status_code: int = 200,
        text: str | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload) if text is None else text

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://127.0.0.1/test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("failure", request=request, response=response)


class _QueueClient:
    def __init__(self, responses: list[_FakeResponse | BaseException]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, object] | None]] = []
        self.closed = False

    def get(self, path: str) -> _FakeResponse:
        self.requests.append((path, None))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def post(self, path: str, *, json: dict[str, object]) -> _FakeResponse:
        self.requests.append((path, json))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self) -> None:
        self.closed = True


def _stream_response(completion: dict[str, object]) -> _FakeResponse:
    events: list[dict[str, object]] = []
    choices = completion.get("choices")
    if isinstance(choices, list):
        for raw_choice in choices:
            choice = cast(dict[str, object], raw_choice)
            message = cast(dict[str, object], choice.get("message", {}))
            events.append(
                {
                    "choices": [
                        {
                            "delta": {
                                "content": message.get("content"),
                                "reasoning_content": message.get("reasoning_content"),
                            },
                            "finish_reason": choice.get("finish_reason"),
                        }
                    ]
                }
            )
    if "usage" in completion:
        events.append({"choices": [], "usage": completion["usage"]})
    text = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    return _FakeResponse(None, text=text + "data: [DONE]\n\n")


class _FakeProcess:
    def __init__(self, return_code: int | None = None) -> None:
        self.return_code = return_code
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = 0

    def wait(self, *, timeout: int) -> int:
        assert timeout == 5
        return self.return_code or 0

    def kill(self) -> None:
        self.killed = True
        self.return_code = -9


def _bare_runtime(
    config: GroundedAnswerRunnerConfig,
    client: _QueueClient,
    ticks: Iterator[float],
) -> LlamaServerRuntime:
    runtime = object.__new__(LlamaServerRuntime)
    runtime._config = config
    runtime._clock = lambda: next(ticks)
    runtime._sleeper = lambda _seconds: None
    runtime._client = cast(Any, client)
    runtime._process = cast(Any, _FakeProcess())
    runtime._system = _system(config)
    return runtime


def test_llama_runtime_counts_before_generation_and_validates_response() -> None:
    task = _task()
    available = next(case for case in task.cases if case.evidence)
    config = _config()
    response = _response(available)
    content = response.model_dump_json(exclude_none=True)
    client = _QueueClient(
        [
            _FakeResponse({"input_tokens": 120}),
            _stream_response(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": content, "reasoning_content": None},
                        }
                    ],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 18},
                }
            ),
        ]
    )
    runtime = _bare_runtime(config, client, iter((1.0, 1.025)))
    generation = runtime.generate(available)

    assert generation.response == response
    assert generation.input_tokens == 120
    assert generation.output_tokens == 18
    assert generation.latency_ms == 25
    assert [request[0] for request in client.requests] == [
        "/v1/chat/completions/input_tokens",
        "/v1/chat/completions",
    ]
    token_body = client.requests[0][1]
    assert token_body is not None
    assert token_body["stream"] is False
    assert "stream_options" not in token_body
    assert "response_format" not in token_body
    completion_body = client.requests[1][1]
    assert completion_body is not None
    assert completion_body["reasoning_effort"] == "none"
    assert completion_body["chat_template_kwargs"] == {"enable_thinking": False}
    assert completion_body["stream"] is True
    assert completion_body["stream_options"] == {"include_usage": True}
    assert completion_body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "grounded_answer_response",
            "strict": True,
            "schema": grounded_answer_response_schema(available),
        },
    }


def test_llama_runtime_rejects_context_and_server_accounting_drift() -> None:
    available = next(case for case in _task().cases if case.evidence)
    config = _config()
    overflow = _bare_runtime(
        config,
        _QueueClient([_FakeResponse({"input_tokens": 4000})]),
        iter(()),
    )
    with pytest.raises(ValueError, match="exceeds context length"):
        overflow.generate(available)

    response = _response(available)
    mismatch = _bare_runtime(
        config,
        _QueueClient(
            [
                _FakeResponse({"input_tokens": 120}),
                _stream_response(
                    {
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "content": response.model_dump_json(exclude_none=True),
                                    "reasoning_content": "hidden reasoning",
                                },
                            }
                        ],
                        "usage": {"prompt_tokens": 121, "completion_tokens": 18},
                    }
                ),
            ]
        ),
        iter((0.0, 0.01)),
    )
    with pytest.raises(RuntimeError, match="emitted reasoning"):
        mismatch.generate(available)


@pytest.mark.parametrize(
    ("completion", "message"),
    (
        ({"choices": [], "usage": {}}, "exactly one completion choice"),
        (
            {
                "choices": [{"finish_reason": "length", "message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 18},
            },
            "finish_reason='length' after 18 output tokens",
        ),
        (
            {
                "choices": [{"finish_reason": "stop", "message": {"content": " "}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 18},
            },
            "non-empty JSON text",
        ),
        (
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"status":"abstained","abstention_reason":"insufficient_evidence"}'
                        },
                    }
                ],
                "usage": {"prompt_tokens": 121, "completion_tokens": 18},
            },
            "token preflight and completion usage disagree",
        ),
        (
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"status":"abstained","abstention_reason":"insufficient_evidence"}'
                        },
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 257},
            },
            "exceeded the requested output-token maximum",
        ),
    ),
)
def test_llama_runtime_rejects_malformed_completion_contracts(
    completion: dict[str, object],
    message: str,
) -> None:
    available = next(case for case in _task().cases if case.evidence)
    runtime = _bare_runtime(
        _config(),
        _QueueClient([_FakeResponse({"input_tokens": 120}), _stream_response(completion)]),
        iter((0.0, 0.01)),
    )
    with pytest.raises(RuntimeError, match=message):
        runtime.generate(available)


def test_llama_runtime_rejects_invalid_token_payload_and_stopped_process() -> None:
    available = next(case for case in _task().cases if case.evidence)
    config = _config()
    invalid_payload = _bare_runtime(
        config,
        _QueueClient([_FakeResponse([])]),
        iter(()),
    )
    with pytest.raises(RuntimeError, match="invalid input-token response"):
        invalid_payload.generate(available)

    invalid_count = _bare_runtime(
        config,
        _QueueClient([_FakeResponse({"input_tokens": 0})]),
        iter(()),
    )
    with pytest.raises(RuntimeError, match="invalid input_tokens"):
        invalid_count.generate(available)

    stopped = _bare_runtime(config, _QueueClient([]), iter(()))
    cast(_FakeProcess, stopped._process).return_code = 1
    with pytest.raises(RuntimeError, match="not running"):
        stopped.generate(available)


def test_llama_runtime_reports_timed_out_stage_and_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    available = next(case for case in _task().cases if case.evidence)
    runtime = _bare_runtime(
        _config(),
        _QueueClient(
            [
                _FakeResponse({"input_tokens": 120}),
                httpx.ReadTimeout("timed out"),
            ]
        ),
        iter((0.0,)),
    )
    monkeypatch.setattr(runtime, "_log_tail", lambda: "generation still active")

    with pytest.raises(
        TimeoutError,
        match=(
            rf"schema-constrained completion timed out for {available.query_id} after "
            r"10s without response progress; llama-server log tail: generation still active"
        ),
    ):
        runtime.generate(available)


def test_llama_runtime_owns_loopback_authenticated_tool_free_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_bytes = b"fake model"
    config = _config(model_sha256=hashlib.sha256(model_bytes).hexdigest())
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / config.model_file).write_bytes(model_bytes)
    executable = tmp_path / "llama-server"
    executable.write_bytes(b"fake executable")
    executable.chmod(0o755)
    process = _FakeProcess()
    commands: list[list[str]] = []
    process_kwargs: list[dict[str, object]] = []
    client_kwargs: list[dict[str, object]] = []
    health_client = _QueueClient([_FakeResponse({"status": "ok"})])

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            [],
            0,
            stdout=(
                "0.00.000.343 I srv llama_server: initializing ...\n"
                "version: 0.4.1-dev (build 11073, commit 1aa2954bd)\n"
                "built with GNU 11.4.0 for Linux x86_64\n"
            ),
            stderr="",
        )

    def fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        commands.append(command)
        process_kwargs.append(kwargs)
        return process

    def fake_client(**kwargs: object) -> _QueueClient:
        client_kwargs.append(kwargs)
        return health_client

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(httpx, "Client", fake_client)
    monkeypatch.setattr(runner_module, "_free_loopback_port", lambda: 43123)
    monkeypatch.setattr(secrets, "token_urlsafe", lambda _length: "local-secret")
    monkeypatch.setenv("HTTP_PROXY", "http://should-not-leak.test")
    monkeypatch.setenv("LLAMA_ARG_TOOLS", "all")

    runtime = LlamaServerRuntime(
        config,
        model_dir,
        executable,
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )
    command = commands[0]
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "43123"
    assert command[command.index("--n-gpu-layers") + 1] == "0"
    assert "--offline" in command
    assert "--no-agent" in command
    assert "--no-webui" in command
    assert "--no-cache-prompt" in command
    assert "--no-cors-credentials" in command
    assert command[command.index("--api-key") + 1] == "local-secret"
    assert "HTTP_PROXY" not in cast(dict[str, str], process_kwargs[0]["env"])
    assert "LLAMA_ARG_TOOLS" not in cast(dict[str, str], process_kwargs[0]["env"])
    assert client_kwargs[0]["base_url"] == "http://127.0.0.1:43123"
    assert client_kwargs[0]["trust_env"] is False
    assert client_kwargs[0]["headers"] == {"Authorization": "Bearer local-secret"}
    timeout = cast(httpx.Timeout, client_kwargs[0]["timeout"])
    assert timeout.connect == 10
    assert timeout.read == config.request_timeout_seconds
    assert timeout.write == 30
    assert timeout.pool == 10
    assert health_client.requests == [("/health", None)]
    assert runtime.system.runtime_version.startswith(
        "version: 0.4.1-dev (build 11073, commit 1aa2954bd);binary_sha256="
    )
    runtime.close()
    assert health_client.closed is True
    assert process.terminated is True


def test_llama_runtime_reports_version_and_startup_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_bytes = b"fake model"
    config = _config(model_sha256=hashlib.sha256(model_bytes).hexdigest())
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / config.model_file).write_bytes(model_bytes)
    executable = tmp_path / "llama-server"
    executable.write_bytes(b"fake executable")

    with pytest.raises(ValueError, match="executable regular file"):
        LlamaServerRuntime(config, model_dir, executable)
    executable.chmod(0o755)

    def failed_version(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 2, stdout="", stderr="bad build")

    monkeypatch.setattr(subprocess, "run", failed_version)
    with pytest.raises(RuntimeError, match="--version failed"):
        LlamaServerRuntime(config, model_dir, executable)

    def valid_version(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout="llama.cpp build-42", stderr="")

    stopped_process = _FakeProcess(return_code=7)
    client = _QueueClient([])
    monkeypatch.setattr(subprocess, "run", valid_version)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: stopped_process)
    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: client)
    monkeypatch.setattr(runner_module, "_free_loopback_port", lambda: 43123)
    with pytest.raises(RuntimeError, match="exited during startup with 7"):
        LlamaServerRuntime(
            config,
            model_dir,
            executable,
            clock=lambda: 0,
            sleeper=lambda _seconds: None,
        )
    assert client.closed is True


class _RuntimeContext:
    def __init__(self, runtime: _FakeRuntime) -> None:
        self.runtime = runtime

    def __enter__(self) -> _FakeRuntime:
        return self.runtime

    def __exit__(self, *_exc: object) -> None:
        return None


def test_runner_cli_writes_only_gold_free_outputs_and_protects_inputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task = _task()
    config = _config()
    task_path = tmp_path / "task.json"
    config_path = tmp_path / "config.json"
    task_path.write_text(task.model_dump_json(indent=2), encoding="utf-8")
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    submission_path = tmp_path / "submission.json"
    definition_path = tmp_path / "definition.json"
    run_path = tmp_path / "run.json"

    def runtime_factory(
        supplied: GroundedAnswerRunnerConfig,
        _model_dir: Path,
        _server: Path,
    ) -> _RuntimeContext:
        return _RuntimeContext(_FakeRuntime(supplied))

    arguments = [
        "--task",
        str(task_path),
        "--candidate-config",
        str(config_path),
        "--model-dir",
        str(tmp_path / "model"),
        "--llama-server",
        str(tmp_path / "llama-server"),
        "--output-submission",
        str(submission_path),
        "--output-definition",
        str(definition_path),
        "--output-run",
        str(run_path),
    ]
    assert run_cli(arguments, runtime_factory=runtime_factory) == 0
    cli_output = capsys.readouterr().out
    assert "Generating grounded answer 1/2" in cli_output
    assert "Completed grounded answer 2/2" in cli_output
    assert "Wrote blocked" in cli_output
    assert "reviewer" not in run_path.read_text(encoding="utf-8")
    checkpoint_path = run_path.with_name("run.checkpoint.json")
    checkpoint_payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert len(checkpoint_payload["cases"]) == task.case_count
    assert (
        CandidateSystemDefinition.model_validate_json(
            definition_path.read_text(encoding="utf-8")
        ).runtime
        == "llama.cpp-server-cpu"
    )

    protected = [*arguments]
    protected[protected.index(str(submission_path))] = str(task_path)
    protected.extend(["--force"])
    original = task_path.read_text(encoding="utf-8")
    assert run_cli(protected, runtime_factory=runtime_factory) == 2
    assert "must not replace input" in capsys.readouterr().err
    assert task_path.read_text(encoding="utf-8") == original

    assert run_cli(arguments, runtime_factory=runtime_factory) == 2
    assert "refusing to overwrite" in capsys.readouterr().err

    duplicate_outputs = [*arguments]
    duplicate_outputs[duplicate_outputs.index(str(definition_path))] = str(submission_path)
    assert run_cli(duplicate_outputs, runtime_factory=runtime_factory) == 2
    assert "output paths must be distinct" in capsys.readouterr().err


def test_runner_cli_checkpoints_and_resumes_an_exact_prefix(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task = _task()
    config = _config()
    task_path = tmp_path / "task.json"
    config_path = tmp_path / "config.json"
    task_path.write_text(task.model_dump_json(indent=2), encoding="utf-8")
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    submission_path = tmp_path / "submission.json"
    definition_path = tmp_path / "definition.json"
    run_path = tmp_path / "run.json"
    created_runtimes: list[_FakeRuntime] = []

    class _FailsOnSecondCase(_FakeRuntime):
        def generate(self, case: GroundedAnswerTaskCase) -> GroundedAnswerGeneration:
            if case.case_id == task.cases[1].case_id:
                raise RuntimeError("planned second-case failure")
            return super().generate(case)

    def failing_factory(
        supplied: GroundedAnswerRunnerConfig,
        _model_dir: Path,
        _server: Path,
    ) -> _RuntimeContext:
        runtime = _FailsOnSecondCase(supplied)
        created_runtimes.append(runtime)
        return _RuntimeContext(runtime)

    arguments = [
        "--task",
        str(task_path),
        "--candidate-config",
        str(config_path),
        "--model-dir",
        str(tmp_path / "model"),
        "--llama-server",
        str(tmp_path / "llama-server"),
        "--output-submission",
        str(submission_path),
        "--output-definition",
        str(definition_path),
        "--output-run",
        str(run_path),
    ]
    assert run_cli(arguments, runtime_factory=failing_factory) == 2
    assert "planned second-case failure" in capsys.readouterr().err
    checkpoint_path = run_path.with_name("run.checkpoint.json")
    checkpoint_payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert len(checkpoint_payload["cases"]) == 1

    def resumed_factory(
        supplied: GroundedAnswerRunnerConfig,
        _model_dir: Path,
        _server: Path,
    ) -> _RuntimeContext:
        runtime = _FakeRuntime(supplied)
        created_runtimes.append(runtime)
        return _RuntimeContext(runtime)

    assert run_cli(arguments, runtime_factory=resumed_factory) == 0
    resumed_output = capsys.readouterr().out
    assert "Resuming from checkpoint: 1/2 case(s)" in resumed_output
    assert "Reusing checkpointed case" in resumed_output
    assert created_runtimes[1].calls == [task.cases[1].case_id]
    assert submission_path.exists()
    assert definition_path.exists()
    assert run_path.exists()


def test_cache_cli_downloads_exact_revision_and_rejects_wrong_bytes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model_bytes = b"downloaded fake model"
    config = _config(model_sha256=hashlib.sha256(model_bytes).hexdigest())
    config_path = tmp_path / "config.json"
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    output = tmp_path / "model"
    calls: list[dict[str, object]] = []

    def downloader(**kwargs: object) -> str:
        calls.append(kwargs)
        path = output / config.model_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(model_bytes)
        return str(path)

    assert (
        cache_cli(
            ["--candidate-config", str(config_path), "--output", str(output)],
            downloader=downloader,
        )
        == 0
    )
    assert calls == [
        {
            "repo_id": config.model_id,
            "filename": config.model_file,
            "revision": config.model_revision,
            "local_dir": str(output),
        }
    ]
    assert "Cached" in capsys.readouterr().out

    def wrong_downloader(**_kwargs: object) -> str:
        path = output / config.model_file
        path.write_bytes(b"wrong bytes")
        return str(path)

    assert (
        cache_cli(
            ["--candidate-config", str(config_path), "--output", str(output)],
            downloader=wrong_downloader,
        )
        == 2
    )
    assert "SHA-256 mismatch" in capsys.readouterr().err

    outside = tmp_path / "outside.gguf"
    outside.write_bytes(model_bytes)

    def outside_downloader(**_kwargs: object) -> str:
        return str(outside)

    assert (
        cache_cli(
            ["--candidate-config", str(config_path), "--output", str(output)],
            downloader=outside_downloader,
        )
        == 2
    )
    assert "outside the exact output artifact" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("version", "is_current"),
    (("v2", False), ("v3", True)),
)
def test_checked_in_qwen_candidates_are_exactly_pinned(
    version: str,
    is_current: bool,
) -> None:
    path = Path(__file__).parents[1] / (
        f"evals/grounded-answers/candidates/qwen3-1.7b-q8-grounded-brief-{version}.json"
    )
    config = GroundedAnswerRunnerConfig.model_validate_json(path.read_text(encoding="utf-8"))
    assert config.candidate_id == f"qwen3-1.7b-q8-grounded-brief-{version}"
    assert config.model_id == "Qwen/Qwen3-1.7B-GGUF"
    assert config.model_revision == "90862c4b9d2787eaed51d12237eafdfe7c5f6077"
    assert config.model_license == "apache-2.0"
    assert config.model_file == "Qwen3-1.7B-Q8_0.gguf"
    assert config.model_file_sha256 == (
        "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a"
    )
    assert config.template_version == f"grounded-brief-qwen3-{version}"
    assert config.adapter_version == config.template_version
    assert (config.template_version == GROUNDING_RUNNER_TEMPLATE_VERSION) is is_current
    assert config.context_length == 8192
    assert config.max_output_tokens == 512

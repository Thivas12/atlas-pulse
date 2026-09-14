"""Strict runtime contracts for source polling and upstream-data freshness."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas_pulse.projections.base import SourceName

SOURCE_POLL_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
SOURCE_POLL_RULE_VERSION: Literal["source-poll-freshness-v1"] = "source-poll-freshness-v1"
SOURCE_POLL_HISTORY_RULE_VERSION: Literal["source-poll-history-v1"] = "source-poll-history-v1"
SOURCE_POLL_CAVEAT = (
    "Freshness is a point-in-time evaluation of worker-written Valkey state using the API host "
    "clock. It is not independent monitoring, an availability SLA, or proof that an upstream "
    "publisher is complete."
)
SOURCE_POLL_HISTORY_CAVEAT = (
    "History is a bounded newest-first view of credential-free worker transitions retained in "
    "Valkey. Retention can expire older entries; it is not independent monitoring, an "
    "availability SLA, or proof of upstream completeness."
)

SourceTimestampBasis = Literal["source_metadata", "latest_record", "fetch_fallback"]
SourcePollOutcome = Literal["in_progress", "succeeded", "failed"]
SourcePollStage = Literal["fetch", "snapshot", "normalize", "publish", "complete"]
SourcePollFailureCode = Literal[
    "transport_exhausted",
    "source_rejected",
    "source_payload_invalid",
    "snapshot_write_failed",
    "publication_unavailable",
    "unexpected_failure",
]
SourcePollStatus = Literal["healthy", "degraded", "stale", "starting", "clock_skew"]
SourceDataStatus = Literal["current", "stale", "not_reported", "future_clock_skew"]
SourcePollTransitionKind = Literal["started", "succeeded", "failed"]


class StrictModel(BaseModel):
    """Reject unknown runtime fields so corrupted state fails closed."""

    model_config = ConfigDict(extra="forbid")


def _require_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must use UTC")
    return value.astimezone(UTC)


class SourcePollPolicy(StrictModel):
    """Expected cadence and independent upstream-age bound for one enabled source."""

    source: SourceName
    interval_seconds: float = Field(gt=0, allow_inf_nan=False)
    poll_stale_after_seconds: float = Field(gt=0, allow_inf_nan=False)
    source_stale_after_seconds: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_thresholds(self) -> SourcePollPolicy:
        if self.poll_stale_after_seconds <= self.interval_seconds:
            raise ValueError("poll stale threshold must exceed the configured interval")
        if self.source_stale_after_seconds <= self.interval_seconds:
            raise ValueError("source stale threshold must exceed the configured interval")
        return self


class SourcePollAttempt(StrictModel):
    """One bounded poll transition without raw payloads, URLs, or exception text."""

    schema_version: Literal["1.0.0"] = SOURCE_POLL_SCHEMA_VERSION
    rule_version: Literal["source-poll-freshness-v1"] = SOURCE_POLL_RULE_VERSION
    attempt_id: str = Field(pattern=r"^source-poll-[0-9a-f]{32}$")
    source: SourceName
    started_at: datetime
    completed_at: datetime | None = None
    outcome: SourcePollOutcome
    stage: SourcePollStage
    transport_attempts: int = Field(default=0, ge=0, le=20)
    source_generated_at: datetime | None = None
    timestamp_basis: SourceTimestampBasis | None = None
    fetched_events: int | None = Field(default=None, ge=0)
    published_events: int | None = Field(default=None, ge=0)
    deduplicated_events: int | None = Field(default=None, ge=0)
    failure_code: SourcePollFailureCode | None = None
    execution_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_attempt(self) -> SourcePollAttempt:
        started = _require_utc(self.started_at, "started_at")
        completed = (
            _require_utc(self.completed_at, "completed_at")
            if self.completed_at is not None
            else None
        )
        if completed is not None and completed < started:
            raise ValueError("poll completion cannot precede its start")
        if (self.source_generated_at is None) != (self.timestamp_basis is None):
            raise ValueError("source timestamp and basis must be present together")
        if self.source_generated_at is not None:
            _require_utc(self.source_generated_at, "source_generated_at")

        counts = (self.fetched_events, self.published_events, self.deduplicated_events)
        if self.outcome == "in_progress":
            if completed is not None or self.failure_code is not None or self.stage == "complete":
                raise ValueError("in-progress polls cannot contain a completion or failure")
            if any(value is not None for value in counts) or self.transport_attempts != 0:
                raise ValueError("new polls cannot contain result counters")
        elif self.outcome == "succeeded":
            if (
                completed is None
                or self.stage != "complete"
                or self.failure_code is not None
                or self.source_generated_at is None
                or self.transport_attempts < 1
                or any(value is None for value in counts)
            ):
                raise ValueError("successful polls require complete bounded result metadata")
            assert self.fetched_events is not None
            assert self.published_events is not None
            assert self.deduplicated_events is not None
            if self.published_events + self.deduplicated_events != self.fetched_events:
                raise ValueError("published and deduplicated counts must equal fetched events")
        else:
            if completed is None or self.failure_code is None or self.stage == "complete":
                raise ValueError("failed polls require a completion, stage, and bounded failure")
            present_counts = tuple(value is not None for value in counts)
            if any(present_counts) and not all(present_counts):
                raise ValueError("partial result counters must be present together")
        if self.execution_enabled is not False:
            raise ValueError("source polling cannot enable execution")
        return self


class SourcePollState(StrictModel):
    """Latest transition plus the most recent successful result for one source."""

    source: SourceName
    policy: SourcePollPolicy
    current_attempt: SourcePollAttempt
    last_success: SourcePollAttempt | None = None
    consecutive_failures: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_state(self) -> SourcePollState:
        if self.policy.source != self.source or self.current_attempt.source != self.source:
            raise ValueError("poll state source must match its policy and current attempt")
        if self.last_success is not None:
            if self.last_success.source != self.source or self.last_success.outcome != "succeeded":
                raise ValueError("last-success state must contain a successful matching poll")
            if self.last_success.started_at > self.current_attempt.started_at:
                raise ValueError("last success cannot begin after the current attempt")
        if self.current_attempt.outcome == "succeeded":
            if self.consecutive_failures != 0:
                raise ValueError("successful current polls reset consecutive failures")
            if self.last_success != self.current_attempt:
                raise ValueError("successful current polls must also be the last success")
        elif self.current_attempt.outcome == "failed" and self.consecutive_failures < 1:
            raise ValueError("failed current polls require a positive consecutive-failure count")
        return self


class SourcePollTransition(StrictModel):
    """One credential-free transition recovered from the bounded poll history."""

    stream_id: str = Field(pattern=r"^[0-9]+-[0-9]+$")
    source: SourceName
    transition: SourcePollTransitionKind
    attempt: SourcePollAttempt

    @model_validator(mode="after")
    def validate_transition(self) -> SourcePollTransition:
        expected_outcome: dict[SourcePollTransitionKind, SourcePollOutcome] = {
            "started": "in_progress",
            "succeeded": "succeeded",
            "failed": "failed",
        }
        if self.source != self.attempt.source:
            raise ValueError("poll transition source must match its attempt")
        if self.attempt.outcome != expected_outcome[self.transition]:
            raise ValueError("poll transition must match its attempt outcome")
        return self


class SourcePollHistoryResponse(StrictModel):
    """Bounded newest-first source-poll transition page."""

    schema_version: Literal["1.0.0"] = SOURCE_POLL_SCHEMA_VERSION
    rule_version: Literal["source-poll-history-v1"] = SOURCE_POLL_HISTORY_RULE_VERSION
    generated_at: datetime
    count: int = Field(ge=0)
    items: tuple[SourcePollTransition, ...] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, pattern=r"^[0-9]+-[0-9]+$")
    has_more: bool
    order: Literal["newest_first"] = "newest_first"
    execution_enabled: Literal[False] = False
    caveat: str = SOURCE_POLL_HISTORY_CAVEAT

    @model_validator(mode="after")
    def validate_history(self) -> SourcePollHistoryResponse:
        _require_utc(self.generated_at, "generated_at")
        if self.count != len(self.items):
            raise ValueError("poll history count must match its items")
        expected_cursor = self.items[-1].stream_id if self.items else None
        if self.next_cursor != expected_cursor:
            raise ValueError("poll history cursor must identify the oldest returned transition")
        if self.has_more and not self.items:
            raise ValueError("empty poll history cannot report another page")
        positions = [
            tuple(int(part) for part in item.stream_id.split("-", maxsplit=1))
            for item in self.items
        ]
        if len(positions) != len(set(positions)) or positions != sorted(positions, reverse=True):
            raise ValueError("poll history transitions must be unique and newest first")
        if self.execution_enabled is not False or self.caveat != SOURCE_POLL_HISTORY_CAVEAT:
            raise ValueError("poll history must retain its observation-only boundary")
        return self


class SourceFreshnessItem(StrictModel):
    """Independent poll-heartbeat and upstream-timestamp assessment for one source."""

    source: SourceName
    interval_seconds: float = Field(gt=0, allow_inf_nan=False)
    poll_stale_after_seconds: float = Field(gt=0, allow_inf_nan=False)
    source_stale_after_seconds: float = Field(gt=0, allow_inf_nan=False)
    poll_status: SourcePollStatus
    source_data_status: SourceDataStatus
    last_outcome: SourcePollOutcome | None = None
    last_stage: SourcePollStage | None = None
    last_failure_code: SourcePollFailureCode | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_source_generated_at: datetime | None = None
    last_success_age_seconds: float | None = Field(default=None, allow_inf_nan=False)
    source_age_seconds: float | None = Field(default=None, allow_inf_nan=False)
    consecutive_failures: int = Field(ge=0)
    transport_attempts: int = Field(ge=0, le=20)
    timestamp_basis: SourceTimestampBasis | None = None
    passed: bool

    @model_validator(mode="after")
    def validate_freshness(self) -> SourceFreshnessItem:
        attempt_fields = (self.last_attempt_at, self.last_outcome, self.last_stage)
        if any(value is None for value in attempt_fields) != all(
            value is None for value in attempt_fields
        ):
            raise ValueError("last-attempt fields must be present together")
        if self.last_attempt_at is not None:
            _require_utc(self.last_attempt_at, "last_attempt_at")
        success_fields = (
            self.last_success_at,
            self.last_source_generated_at,
            self.last_success_age_seconds,
            self.source_age_seconds,
            self.timestamp_basis,
        )
        if any(value is None for value in success_fields) != all(
            value is None for value in success_fields
        ):
            raise ValueError("last-success freshness fields must be present together")
        if self.last_success_at is not None:
            _require_utc(self.last_success_at, "last_success_at")
            assert self.last_source_generated_at is not None
            _require_utc(self.last_source_generated_at, "last_source_generated_at")
        if (self.last_failure_code is not None) != (self.last_outcome == "failed"):
            raise ValueError("only a failed latest poll may expose a failure code")
        expected_pass = self.poll_status == "healthy" and self.source_data_status == "current"
        if self.passed != expected_pass:
            raise ValueError("freshness pass status must match poll and source-data status")
        return self


class SourceFreshnessResponse(StrictModel):
    """Canonical point-in-time source-freshness API response."""

    schema_version: Literal["1.0.0"] = SOURCE_POLL_SCHEMA_VERSION
    rule_version: Literal["source-poll-freshness-v1"] = SOURCE_POLL_RULE_VERSION
    generated_at: datetime
    items: tuple[SourceFreshnessItem, ...] = Field(min_length=1, max_length=4)
    passed: bool
    execution_enabled: Literal[False] = False
    caveat: str = SOURCE_POLL_CAVEAT

    @model_validator(mode="after")
    def validate_response(self) -> SourceFreshnessResponse:
        generated = _require_utc(self.generated_at, "generated_at")
        sources = tuple(item.source for item in self.items)
        if sources != tuple(sorted(set(sources))):
            raise ValueError("source freshness items must be unique and canonically ordered")
        for item in self.items:
            if item.last_success_at is not None:
                assert item.last_success_age_seconds is not None
                assert item.last_source_generated_at is not None
                assert item.source_age_seconds is not None
                success_age = (generated - item.last_success_at).total_seconds()
                source_age = (generated - item.last_source_generated_at).total_seconds()
                if abs(item.last_success_age_seconds - success_age) > 1e-6:
                    raise ValueError("last-success age must match response generation time")
                if abs(item.source_age_seconds - source_age) > 1e-6:
                    raise ValueError("source age must match response generation time")
        if self.passed != all(item.passed for item in self.items):
            raise ValueError("response pass status must match every source")
        if self.execution_enabled is not False or self.caveat != SOURCE_POLL_CAVEAT:
            raise ValueError("source freshness must retain its observation-only boundary")
        return self


def new_source_poll_attempt(source: SourceName, *, started_at: datetime) -> SourcePollAttempt:
    """Create a credential-free unique identity for a new poll cycle."""
    return SourcePollAttempt(
        attempt_id=f"source-poll-{uuid4().hex}",
        source=source,
        started_at=_require_utc(started_at, "started_at"),
        outcome="in_progress",
        stage="fetch",
    )


def evaluate_source_freshness(
    policies: tuple[SourcePollPolicy, ...],
    states: tuple[SourcePollState, ...],
    *,
    generated_at: datetime,
) -> SourceFreshnessResponse:
    """Evaluate poll heartbeat and source timestamps without inferring from event visibility."""
    timestamp = _require_utc(generated_at, "generated_at")
    policy_sources = tuple(policy.source for policy in policies)
    if policy_sources != tuple(sorted(set(policy_sources))):
        raise ValueError("source poll policies must be unique and canonically ordered")
    state_by_source = {state.source: state for state in states}
    if len(state_by_source) != len(states) or not set(state_by_source).issubset(policy_sources):
        raise ValueError("source poll states must be unique and match configured policies")

    items: list[SourceFreshnessItem] = []
    for policy in policies:
        state = state_by_source.get(policy.source)
        if state is None:
            items.append(
                SourceFreshnessItem(
                    source=policy.source,
                    interval_seconds=policy.interval_seconds,
                    poll_stale_after_seconds=policy.poll_stale_after_seconds,
                    source_stale_after_seconds=policy.source_stale_after_seconds,
                    poll_status="starting",
                    source_data_status="not_reported",
                    consecutive_failures=0,
                    transport_attempts=0,
                    passed=False,
                )
            )
            continue
        if state.policy != policy:
            raise ValueError("stored source poll policy does not match runtime configuration")

        current = state.current_attempt
        attempt_age = (timestamp - current.started_at).total_seconds()
        completion_age = (
            (timestamp - current.completed_at).total_seconds()
            if current.completed_at is not None
            else None
        )
        last_success = state.last_success
        if attempt_age < 0 or (completion_age is not None and completion_age < 0):
            poll_status: SourcePollStatus = "clock_skew"
        elif attempt_age > policy.poll_stale_after_seconds:
            poll_status = "stale"
        elif current.outcome == "failed":
            poll_status = "degraded"
        elif current.outcome == "in_progress" and last_success is None:
            poll_status = "starting"
        else:
            poll_status = "healthy"

        if last_success is None:
            source_status: SourceDataStatus = "not_reported"
            last_success_at = None
            source_generated_at = None
            success_age = None
            source_age = None
            timestamp_basis = None
        else:
            assert last_success.completed_at is not None
            assert last_success.source_generated_at is not None
            assert last_success.timestamp_basis is not None
            last_success_at = last_success.completed_at
            source_generated_at = last_success.source_generated_at
            success_age = (timestamp - last_success_at).total_seconds()
            source_age = (timestamp - source_generated_at).total_seconds()
            timestamp_basis = last_success.timestamp_basis
            if success_age < 0:
                poll_status = "clock_skew"
            if source_age < 0:
                source_status = "future_clock_skew"
            elif source_age > policy.source_stale_after_seconds:
                source_status = "stale"
            else:
                source_status = "current"

        items.append(
            SourceFreshnessItem(
                source=policy.source,
                interval_seconds=policy.interval_seconds,
                poll_stale_after_seconds=policy.poll_stale_after_seconds,
                source_stale_after_seconds=policy.source_stale_after_seconds,
                poll_status=poll_status,
                source_data_status=source_status,
                last_outcome=current.outcome,
                last_stage=current.stage,
                last_failure_code=current.failure_code,
                last_attempt_at=current.started_at,
                last_success_at=last_success_at,
                last_source_generated_at=source_generated_at,
                last_success_age_seconds=success_age,
                source_age_seconds=source_age,
                consecutive_failures=state.consecutive_failures,
                transport_attempts=current.transport_attempts,
                timestamp_basis=timestamp_basis,
                passed=poll_status == "healthy" and source_status == "current",
            )
        )
    result = tuple(items)
    return SourceFreshnessResponse(
        generated_at=timestamp,
        items=result,
        passed=all(item.passed for item in result),
    )

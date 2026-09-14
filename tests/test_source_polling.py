"""Source-poll state and freshness contract tests."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from atlas_pulse.projections.base import SourceName
from atlas_pulse.source_poll_store import (
    InMemorySourcePollStore,
    StaleSourcePollTransition,
    ValkeySourcePollStore,
)
from atlas_pulse.source_polling import (
    SourcePollAttempt,
    SourcePollPolicy,
    SourcePollState,
    evaluate_source_freshness,
    new_source_poll_attempt,
)

NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)


def _policy(source: SourceName = "usgs") -> SourcePollPolicy:
    return SourcePollPolicy(
        source=source,
        interval_seconds=60,
        poll_stale_after_seconds=180,
        source_stale_after_seconds=600,
    )


def _successful_attempt(
    *,
    source: SourceName = "usgs",
    started_at: datetime = NOW - timedelta(seconds=15),
    completed_at: datetime = NOW - timedelta(seconds=10),
    source_generated_at: datetime = NOW - timedelta(seconds=30),
) -> SourcePollAttempt:
    return SourcePollAttempt(
        attempt_id="source-poll-" + "a" * 32,
        source=source,
        started_at=started_at,
        completed_at=completed_at,
        outcome="succeeded",
        stage="complete",
        transport_attempts=2,
        source_generated_at=source_generated_at,
        timestamp_basis="source_metadata",
        fetched_events=3,
        published_events=2,
        deduplicated_events=1,
    )


def _failed_attempt(
    *, source: SourceName = "usgs", started_at: datetime = NOW - timedelta(seconds=5)
) -> SourcePollAttempt:
    return SourcePollAttempt(
        attempt_id="source-poll-" + "b" * 32,
        source=source,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=1),
        outcome="failed",
        stage="fetch",
        transport_attempts=3,
        failure_code="transport_exhausted",
    )


def test_successful_poll_evaluates_independent_heartbeat_and_source_age() -> None:
    policy = _policy()
    success = _successful_attempt()
    state = SourcePollState(
        source="usgs",
        policy=policy,
        current_attempt=success,
        last_success=success,
        consecutive_failures=0,
    )

    response = evaluate_source_freshness((policy,), (state,), generated_at=NOW)

    item = response.items[0]
    assert response.passed is True
    assert item.poll_status == "healthy"
    assert item.source_data_status == "current"
    assert item.last_success_age_seconds == 10
    assert item.source_age_seconds == 30
    assert item.transport_attempts == 2
    assert item.passed is True


def test_recent_failure_is_degraded_without_erasing_last_source_timestamp() -> None:
    policy = _policy()
    success = _successful_attempt(started_at=NOW - timedelta(minutes=2))
    failure = _failed_attempt()
    state = SourcePollState(
        source="usgs",
        policy=policy,
        current_attempt=failure,
        last_success=success,
        consecutive_failures=1,
    )

    item = evaluate_source_freshness((policy,), (state,), generated_at=NOW).items[0]

    assert item.poll_status == "degraded"
    assert item.source_data_status == "current"
    assert item.last_failure_code == "transport_exhausted"
    assert item.consecutive_failures == 1
    assert item.passed is False


def test_missing_stale_and_future_states_fail_closed() -> None:
    usgs_policy = _policy()
    nws_policy = SourcePollPolicy(
        source="nws",
        interval_seconds=120,
        poll_stale_after_seconds=360,
        source_stale_after_seconds=900,
    )
    stale = _successful_attempt(
        started_at=NOW - timedelta(hours=2),
        completed_at=NOW - timedelta(hours=2),
        source_generated_at=NOW - timedelta(hours=3),
    )
    state = SourcePollState(
        source="usgs",
        policy=usgs_policy,
        current_attempt=stale,
        last_success=stale,
        consecutive_failures=0,
    )

    response = evaluate_source_freshness(
        (nws_policy, usgs_policy),
        (state,),
        generated_at=NOW,
    )

    assert response.passed is False
    assert response.items[0].poll_status == "starting"
    assert response.items[0].source_data_status == "not_reported"
    assert response.items[1].poll_status == "stale"
    assert response.items[1].source_data_status == "stale"

    future = _successful_attempt(
        started_at=NOW + timedelta(seconds=1),
        completed_at=NOW + timedelta(seconds=2),
        source_generated_at=NOW + timedelta(seconds=3),
    )
    future_state = SourcePollState(
        source="usgs",
        policy=usgs_policy,
        current_attempt=future,
        last_success=future,
        consecutive_failures=0,
    )
    future_item = evaluate_source_freshness(
        (usgs_policy,), (future_state,), generated_at=NOW
    ).items[0]
    assert future_item.poll_status == "clock_skew"
    assert future_item.source_data_status == "future_clock_skew"


def test_future_completion_fails_poll_clock_even_when_start_and_source_are_past() -> None:
    policy = _policy()
    future_completion = _successful_attempt(
        started_at=NOW - timedelta(seconds=5),
        completed_at=NOW + timedelta(seconds=1),
        source_generated_at=NOW - timedelta(seconds=30),
    )
    state = SourcePollState(
        source="usgs",
        policy=policy,
        current_attempt=future_completion,
        last_success=future_completion,
        consecutive_failures=0,
    )

    item = evaluate_source_freshness((policy,), (state,), generated_at=NOW).items[0]

    assert item.poll_status == "clock_skew"
    assert item.source_data_status == "current"
    assert item.last_success_age_seconds == -1
    assert item.passed is False


def test_first_in_progress_poll_is_starting() -> None:
    policy = _policy()
    attempt = new_source_poll_attempt("usgs", started_at=NOW)
    state = SourcePollState(
        source="usgs",
        policy=policy,
        current_attempt=attempt,
        consecutive_failures=0,
    )

    item = evaluate_source_freshness((policy,), (state,), generated_at=NOW).items[0]

    assert attempt.attempt_id.startswith("source-poll-")
    assert item.poll_status == "starting"
    assert item.last_success_at is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("completed_at", None, "successful polls require"),
        ("failure_code", "transport_exhausted", "successful polls require"),
        ("published_events", 4, "must equal fetched"),
    ],
)
def test_success_contract_rejects_inconsistent_metadata(
    field: str, value: object, message: str
) -> None:
    payload = _successful_attempt().model_dump(mode="python")
    payload[field] = value
    with pytest.raises(ValidationError, match=message):
        SourcePollAttempt.model_validate(payload)


def test_policy_and_response_reject_schema_drift() -> None:
    with pytest.raises(ValidationError, match="must exceed"):
        SourcePollPolicy(
            source="usgs",
            interval_seconds=60,
            poll_stale_after_seconds=60,
            source_stale_after_seconds=600,
        )
    with pytest.raises(ValueError, match="canonically ordered"):
        evaluate_source_freshness((_policy("usgs"), _policy("nws")), (), generated_at=NOW)


def test_poll_state_rejects_inconsistent_terminal_aggregates() -> None:
    success = _successful_attempt()
    with pytest.raises(ValidationError, match="must also be the last success"):
        SourcePollState(
            source="usgs",
            policy=_policy(),
            current_attempt=success,
            last_success=None,
            consecutive_failures=0,
        )
    with pytest.raises(ValidationError, match="positive consecutive-failure"):
        SourcePollState(
            source="usgs",
            policy=_policy(),
            current_attempt=_failed_attempt(),
            last_success=None,
            consecutive_failures=0,
        )


async def test_in_memory_store_preserves_success_and_rejects_stale_transitions() -> None:
    store = InMemorySourcePollStore()
    policy = _policy()
    started = new_source_poll_attempt("usgs", started_at=NOW - timedelta(seconds=20))
    await store.record_started(started, policy)
    success = _successful_attempt(
        started_at=started.started_at,
        completed_at=NOW - timedelta(seconds=10),
    ).model_copy(update={"attempt_id": started.attempt_id})
    await store.record_completed(success)

    second = new_source_poll_attempt("usgs", started_at=NOW)
    await store.record_started(second, policy)
    failure = _failed_attempt(started_at=NOW).model_copy(update={"attempt_id": second.attempt_id})
    await store.record_completed(failure)
    state = (await store.load_states(("nws", "usgs")))[0]

    assert state.current_attempt == failure
    assert state.last_success == success
    assert state.consecutive_failures == 1
    with pytest.raises(StaleSourcePollTransition, match="stale or duplicated"):
        await store.record_completed(failure)
    with pytest.raises(StaleSourcePollTransition, match="stale or duplicated"):
        await store.record_started(second, policy)


class FakeValkey:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[bytes, bytes]] = {}
        self.closed = False

    async def eval(self, script: str, _numkeys: int, *args: str | bytes | int) -> object:
        state_key = str(args[0])
        fields = self.hashes.setdefault(state_key, {})
        if "stale_start" in script:
            started_at = str(args[5])
            existing = fields.get(b"current_started_at")
            if existing is not None and existing.decode() >= started_at:
                return [0, b"stale_start"]
            fields.update(
                {
                    b"source": str(args[2]).encode(),
                    b"policy_json": str(args[3]).encode(),
                    b"current_attempt_id": str(args[4]).encode(),
                    b"current_started_at": started_at.encode(),
                    b"current_outcome": b"in_progress",
                    b"current_attempt_json": str(args[6]).encode(),
                }
            )
            return [1, b"started"]
        attempt_id = str(args[2])
        if (
            fields.get(b"current_attempt_id") != attempt_id.encode()
            or fields.get(b"current_outcome") != b"in_progress"
        ):
            return [0, b"stale_completion"]
        outcome = str(args[3])
        attempt_json = str(args[4]).encode()
        fields[b"current_outcome"] = outcome.encode()
        fields[b"current_attempt_json"] = attempt_json
        if outcome == "succeeded":
            fields[b"last_success_json"] = attempt_json
            fields[b"consecutive_failures"] = b"0"
        else:
            failures = int(fields.get(b"consecutive_failures", b"0")) + 1
            fields[b"consecutive_failures"] = str(failures).encode()
        return [1, outcome.encode()]

    async def hgetall(self, name: str) -> Mapping[bytes, bytes]:
        return self.hashes.get(name, {})

    async def aclose(self) -> None:
        self.closed = True


async def test_valkey_store_round_trips_strict_state() -> None:
    client = FakeValkey()
    store = ValkeySourcePollStore(
        url="valkey://unused",
        history_stream="{atlas}:source-polls",
        history_max_length=100,
        client=client,
    )
    started = new_source_poll_attempt("usgs", started_at=NOW - timedelta(seconds=20))
    await store.record_started(started, _policy())
    success = _successful_attempt(started_at=started.started_at).model_copy(
        update={"attempt_id": started.attempt_id}
    )
    await store.record_completed(success)

    state = (await store.load_states(("usgs",)))[0]

    assert state.current_attempt == success
    assert state.last_success == success
    assert state.consecutive_failures == 0
    await store.close()
    assert client.closed is False


@pytest.mark.parametrize("reply", [None, [1], [2, b"invalid"]])
async def test_valkey_store_rejects_malformed_script_reply(reply: object) -> None:
    class MalformedValkey(FakeValkey):
        async def eval(self, script: str, _numkeys: int, *args: str | bytes | int) -> object:
            del script, args
            return reply

    store = ValkeySourcePollStore(
        url="valkey://unused",
        history_stream="{atlas}:source-polls",
        history_max_length=100,
        client=MalformedValkey(),
    )
    with pytest.raises((TypeError, ValueError)):
        await store.record_started(new_source_poll_attempt("usgs", started_at=NOW), _policy())


async def test_owned_valkey_store_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeValkey()
    monkeypatch.setattr(
        "atlas_pulse.source_poll_store.Valkey.from_url",
        lambda *_args, **_kwargs: client,
    )
    store = ValkeySourcePollStore(
        url="valkey://unused",
        history_stream="{atlas}:source-polls",
        history_max_length=100,
    )
    await store.close()
    assert client.closed is True


def test_valkey_store_rejects_nonpositive_history_bound() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        ValkeySourcePollStore(
            url="valkey://unused",
            history_stream="{atlas}:source-polls",
            history_max_length=0,
        )

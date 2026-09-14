"""Atomic persistence for source-poll heartbeat and freshness state."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from valkey.asyncio import Valkey

from atlas_pulse.projections.base import SourceName
from atlas_pulse.source_polling import (
    SourcePollAttempt,
    SourcePollPolicy,
    SourcePollState,
    SourcePollTransition,
    SourcePollTransitionKind,
)

_RECORD_START = """
local existing_started_at = redis.call('HGET', KEYS[1], 'current_started_at')
if existing_started_at and existing_started_at >= ARGV[4] then
  return {0, 'stale_start'}
end
redis.call(
  'HSET', KEYS[1],
  'source', ARGV[1],
  'policy_json', ARGV[2],
  'current_attempt_id', ARGV[3],
  'current_started_at', ARGV[4],
  'current_outcome', 'in_progress',
  'current_attempt_json', ARGV[5]
)
redis.call(
  'XADD', KEYS[2], 'MAXLEN', '~', ARGV[6], '*',
  'source', ARGV[1], 'transition', 'started', 'attempt', ARGV[5]
)
return {1, 'started'}
"""

_RECORD_COMPLETION = """
local current_id = redis.call('HGET', KEYS[1], 'current_attempt_id')
local current_outcome = redis.call('HGET', KEYS[1], 'current_outcome')
if not current_id or current_id ~= ARGV[1] or current_outcome ~= 'in_progress' then
  return {0, 'stale_completion'}
end
redis.call(
  'HSET', KEYS[1],
  'current_outcome', ARGV[2],
  'current_attempt_json', ARGV[3]
)
if ARGV[2] == 'succeeded' then
  redis.call('HSET', KEYS[1], 'last_success_json', ARGV[3], 'consecutive_failures', 0)
else
  redis.call('HINCRBY', KEYS[1], 'consecutive_failures', 1)
end
redis.call(
  'XADD', KEYS[2], 'MAXLEN', '~', ARGV[4], '*',
  'source', ARGV[5], 'transition', ARGV[2], 'attempt', ARGV[3]
)
return {1, ARGV[2]}
"""


class StaleSourcePollTransition(RuntimeError):
    """A superseded or duplicate poll transition was rejected atomically."""


class SourcePollStore(Protocol):
    """Minimal poll-state capability shared by ingestion and the read API."""

    async def record_started(
        self, attempt: SourcePollAttempt, policy: SourcePollPolicy
    ) -> None: ...

    async def record_completed(self, attempt: SourcePollAttempt) -> None: ...

    async def load_states(self, sources: tuple[SourceName, ...]) -> tuple[SourcePollState, ...]: ...

    async def load_recent_transitions(
        self, *, before: str | None, limit: int
    ) -> tuple[SourcePollTransition, ...]: ...

    async def close(self) -> None: ...


class InMemorySourcePollStore:
    """Lock-protected poll state used by unit tests and local composition."""

    def __init__(self) -> None:
        self._states: dict[SourceName, SourcePollState] = {}
        self._history: list[SourcePollTransition] = []
        self._history_sequence = 0
        self._lock = asyncio.Lock()

    def _append_transition(
        self, transition: SourcePollTransitionKind, attempt: SourcePollAttempt
    ) -> None:
        self._history_sequence += 1
        self._history.append(
            SourcePollTransition(
                stream_id=f"0-{self._history_sequence}",
                source=attempt.source,
                transition=transition,
                attempt=attempt,
            )
        )

    async def record_started(self, attempt: SourcePollAttempt, policy: SourcePollPolicy) -> None:
        if attempt.outcome != "in_progress" or attempt.source != policy.source:
            raise ValueError("poll start must be in progress and match its policy")
        async with self._lock:
            existing = self._states.get(attempt.source)
            if existing is not None and existing.current_attempt.started_at >= attempt.started_at:
                raise StaleSourcePollTransition("source poll start was stale or duplicated")
            self._states[attempt.source] = SourcePollState(
                source=attempt.source,
                policy=policy,
                current_attempt=attempt,
                last_success=existing.last_success if existing else None,
                consecutive_failures=existing.consecutive_failures if existing else 0,
            )
            self._append_transition("started", attempt)

    async def record_completed(self, attempt: SourcePollAttempt) -> None:
        if attempt.outcome == "in_progress" or attempt.completed_at is None:
            raise ValueError("poll completion must contain a terminal attempt")
        async with self._lock:
            existing = self._states.get(attempt.source)
            if (
                existing is None
                or existing.current_attempt.attempt_id != attempt.attempt_id
                or existing.current_attempt.outcome != "in_progress"
            ):
                raise StaleSourcePollTransition("source poll completion was stale or duplicated")
            succeeded = attempt.outcome == "succeeded"
            self._states[attempt.source] = SourcePollState(
                source=attempt.source,
                policy=existing.policy,
                current_attempt=attempt,
                last_success=attempt if succeeded else existing.last_success,
                consecutive_failures=0 if succeeded else existing.consecutive_failures + 1,
            )
            self._append_transition("succeeded" if succeeded else "failed", attempt)

    async def load_states(self, sources: tuple[SourceName, ...]) -> tuple[SourcePollState, ...]:
        async with self._lock:
            return tuple(self._states[source] for source in sources if source in self._states)

    async def load_recent_transitions(
        self, *, before: str | None, limit: int
    ) -> tuple[SourcePollTransition, ...]:
        if limit < 1:
            raise ValueError("source poll history limit must be positive")
        before_position = _stream_position(before) if before is not None else None
        async with self._lock:
            newest_first = reversed(self._history)
            visible = (
                transition
                for transition in newest_first
                if before_position is None
                or _stream_position(transition.stream_id) < before_position
            )
            result: list[SourcePollTransition] = []
            for transition in visible:
                result.append(transition)
                if len(result) == limit:
                    break
            return tuple(result)

    async def close(self) -> None:
        return None


class AsyncValkeyClient(Protocol):
    """Narrow async client surface required by the poll-state adapter."""

    async def eval(
        self, script: str, numkeys: int, *keys_and_args: str | bytes | int
    ) -> object: ...

    async def hgetall(self, name: str) -> object: ...

    async def xrevrange(
        self,
        name: str,
        max: str = "+",
        min: str = "-",
        count: int | None = None,
    ) -> object: ...

    async def aclose(self) -> None: ...


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise TypeError(f"expected bytes or str, received {type(value).__name__}")


def _script_accepted(value: object) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError("Valkey source-poll script returned an invalid response")
    if len(value) != 2:
        raise ValueError("Valkey source-poll script returned an unexpected response length")
    accepted = value[0]
    if isinstance(accepted, bytes | str):
        accepted = int(_as_text(accepted))
    if accepted not in {0, 1}:
        raise ValueError("Valkey source-poll script returned an invalid transition result")
    return bool(accepted)


def _mapping_value(fields: Mapping[object, object], key: str) -> object | None:
    return fields.get(key, fields.get(key.encode()))


def _stream_position(value: str) -> tuple[int, int]:
    parts = value.split("-", maxsplit=1)
    if len(parts) != 2 or not all(part.isascii() and part.isdigit() for part in parts):
        raise ValueError("source poll history cursor must be a Valkey stream ID")
    return int(parts[0]), int(parts[1])


class ValkeySourcePollStore:
    """Valkey-backed latest state plus a bounded immutable transition stream."""

    def __init__(
        self,
        *,
        url: str,
        history_stream: str,
        history_max_length: int,
        client: AsyncValkeyClient | None = None,
    ) -> None:
        if history_max_length < 1:
            raise ValueError("source poll history length must be positive")
        self._history_stream = history_stream
        self._history_max_length = history_max_length
        self._owns_client = client is None
        self._client = client or cast(
            AsyncValkeyClient,
            Valkey.from_url(url, decode_responses=False),
        )

    @staticmethod
    def _state_key(source: SourceName) -> str:
        return f"{{atlas}}:source-poll:{source}"

    async def record_started(self, attempt: SourcePollAttempt, policy: SourcePollPolicy) -> None:
        if attempt.outcome != "in_progress" or attempt.source != policy.source:
            raise ValueError("poll start must be in progress and match its policy")
        result = await self._client.eval(
            _RECORD_START,
            2,
            self._state_key(attempt.source),
            self._history_stream,
            attempt.source,
            policy.model_dump_json(),
            attempt.attempt_id,
            attempt.started_at.isoformat(),
            attempt.model_dump_json(),
            self._history_max_length,
        )
        if not _script_accepted(result):
            raise StaleSourcePollTransition("source poll start was stale or duplicated")

    async def record_completed(self, attempt: SourcePollAttempt) -> None:
        if attempt.outcome == "in_progress" or attempt.completed_at is None:
            raise ValueError("poll completion must contain a terminal attempt")
        result = await self._client.eval(
            _RECORD_COMPLETION,
            2,
            self._state_key(attempt.source),
            self._history_stream,
            attempt.attempt_id,
            attempt.outcome,
            attempt.model_dump_json(),
            self._history_max_length,
            attempt.source,
        )
        if not _script_accepted(result):
            raise StaleSourcePollTransition("source poll completion was stale or duplicated")

    async def load_states(self, sources: tuple[SourceName, ...]) -> tuple[SourcePollState, ...]:
        states: list[SourcePollState] = []
        for source in sources:
            raw = await self._client.hgetall(self._state_key(source))
            if not isinstance(raw, Mapping):
                raise TypeError("Valkey source-poll hash returned an invalid response")
            if not raw:
                continue
            stored_source = _mapping_value(raw, "source")
            policy_json = _mapping_value(raw, "policy_json")
            current_json = _mapping_value(raw, "current_attempt_json")
            success_json = _mapping_value(raw, "last_success_json")
            failures = _mapping_value(raw, "consecutive_failures")
            if stored_source is None or policy_json is None or current_json is None:
                raise ValueError("Valkey source-poll state is incomplete")
            if _as_text(stored_source) != source:
                raise ValueError("Valkey source-poll state has the wrong source")
            policy = SourcePollPolicy.model_validate_json(_as_text(policy_json))
            current = SourcePollAttempt.model_validate_json(_as_text(current_json))
            success = (
                SourcePollAttempt.model_validate_json(_as_text(success_json))
                if success_json is not None
                else None
            )
            states.append(
                SourcePollState(
                    source=source,
                    policy=policy,
                    current_attempt=current,
                    last_success=success,
                    consecutive_failures=(int(_as_text(failures)) if failures is not None else 0),
                )
            )
        return tuple(states)

    async def load_recent_transitions(
        self, *, before: str | None, limit: int
    ) -> tuple[SourcePollTransition, ...]:
        if limit < 1:
            raise ValueError("source poll history limit must be positive")
        if before is not None:
            _stream_position(before)
        maximum = f"({before}" if before is not None else "+"
        raw_messages = await self._client.xrevrange(
            self._history_stream,
            max=maximum,
            count=limit,
        )
        if not isinstance(raw_messages, Sequence) or isinstance(raw_messages, (str, bytes)):
            raise TypeError("Valkey source-poll history returned an invalid response")

        transitions: list[SourcePollTransition] = []
        for raw_message in raw_messages:
            if not isinstance(raw_message, Sequence) or len(raw_message) != 2:
                raise TypeError("Valkey source-poll history entry has an invalid shape")
            stream_id, raw_fields = raw_message
            if not isinstance(raw_fields, Mapping):
                raise TypeError("Valkey source-poll history fields have an invalid shape")
            field_names = {_as_text(key) for key in raw_fields}
            if field_names != {"source", "transition", "attempt"}:
                raise ValueError("Valkey source-poll history fields are incomplete or extended")
            source = _mapping_value(raw_fields, "source")
            transition = _mapping_value(raw_fields, "transition")
            attempt = _mapping_value(raw_fields, "attempt")
            assert source is not None and transition is not None and attempt is not None
            transitions.append(
                SourcePollTransition(
                    stream_id=_as_text(stream_id),
                    source=cast(SourceName, _as_text(source)),
                    transition=cast(SourcePollTransitionKind, _as_text(transition)),
                    attempt=SourcePollAttempt.model_validate_json(_as_text(attempt)),
                )
            )
        return tuple(transitions)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

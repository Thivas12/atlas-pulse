"""Raw snapshot immutability tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlas_pulse.ingestion.snapshot import RawSnapshotStore


def test_snapshot_is_content_addressed_and_never_overwritten(tmp_path: Path) -> None:
    store = RawSnapshotStore(tmp_path)
    fetched_at = datetime(2024, 7, 10, 12, tzinfo=UTC)

    first = store.write(source="usgs", fetched_at=fetched_at, raw=b'{"ok":true}')
    second = store.write(source="usgs", fetched_at=fetched_at, raw=b'{"ok":true}')

    assert first.path == second.path
    assert first.created is True
    assert second.created is False
    assert first.path.read_bytes() == b'{"ok":true}'
    assert first.size_bytes == 11
    assert len(first.sha256) == 64


def test_snapshot_detects_impossible_hash_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RawSnapshotStore(tmp_path)
    fetched_at = datetime(2024, 7, 10, 12, tzinfo=UTC)
    first = store.write(source="usgs", fetched_at=fetched_at, raw=b"first")

    def same_hash(_raw: bytes) -> _SameHash:
        return _SameHash()

    monkeypatch.setattr("atlas_pulse.ingestion.snapshot.hashlib.sha256", same_hash)

    with pytest.raises(RuntimeError, match="hash collision"):
        store.write(source="usgs", fetched_at=fetched_at, raw=b"second")

    assert first.path.read_bytes() == b"first"


class _SameHash:
    def hexdigest(self) -> str:
        return "a7937b64b8caa58f03721bb6bacf5c78cb235febe0e70b1b84cd99541461a08e"

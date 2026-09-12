"""Content-addressed immutable storage for original source responses."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    """Location and identity of one raw source snapshot."""

    path: Path
    sha256: str
    created: bool
    size_bytes: int


class RawSnapshotStore:
    """Write each unique source response once without destructive updates."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(
        self,
        *,
        source: str,
        fetched_at: datetime,
        raw: bytes,
        extension: str = "json",
    ) -> SnapshotResult:
        """Persist bytes under their SHA-256, safely handling concurrent writers."""
        digest = hashlib.sha256(raw).hexdigest()
        day = fetched_at.strftime("%Y/%m/%d")
        path = self._root / source / day / f"{digest}.{extension.lstrip('.')}"
        path.parent.mkdir(parents=True, exist_ok=True)

        created = False
        try:
            with path.open("xb") as snapshot:
                snapshot.write(raw)
            created = True
        except FileExistsError:
            if path.read_bytes() != raw:
                raise RuntimeError(f"snapshot hash collision at {path}") from None

        return SnapshotResult(
            path=path,
            sha256=digest,
            created=created,
            size_bytes=len(raw),
        )

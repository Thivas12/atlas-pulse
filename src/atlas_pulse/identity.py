"""Shared canonical JSON identities for content-addressed AtlasPulse artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime


def _encode_extra(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Return sorted, compact, UTF-8 canonical JSON bytes."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_encode_extra,
    ).encode()


def canonical_json_sha256(value: object) -> str:
    """Return SHA-256 over sorted, compact, UTF-8 canonical JSON."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

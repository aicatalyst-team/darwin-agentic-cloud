"""Deterministic hashing for DAC attestations.

Canonical JSON (sorted keys, no whitespace, UTF-8) + SHA-256 so the same
logical content always produces the same hash across implementations.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> bytes:
    """Serialize obj to canonical JSON bytes (sorted keys, no whitespace, UTF-8)."""
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Return the hex-encoded SHA-256 of data."""
    return hashlib.sha256(data).hexdigest()


def content_hash(obj: Any) -> str:
    """Hex SHA-256 of obj serialized as canonical JSON."""
    return sha256_hex(canonical_json(obj))

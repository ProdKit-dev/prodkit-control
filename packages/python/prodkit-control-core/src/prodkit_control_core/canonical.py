"""Deterministic canonicalization and digest helpers.

The canonical representation is intentionally simple: UTF-8 JSON with sorted keys, compact
separators, UTC datetimes, UUID strings, decimal strings, and no NaN values. This is not a claim
of RFC 8785 compatibility; schema versions identify the exact ProdKit canonicalization rules.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel

CANONICALIZATION_VERSION = "prodkit-json-v1"


def _normalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="python", exclude_none=False))
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, set):
        return sorted((_normalize(item) for item in value), key=lambda item: repr(item))
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (UUID, Path)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return value.hex()
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a supported value."""

    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(value: bytes | str | Any) -> str:
    """Return a lowercase SHA-256 digest for bytes, text, or canonicalizable data."""

    payload = (
        value
        if isinstance(value, bytes)
        else value.encode()
        if isinstance(value, str)
        else canonical_json_bytes(value)
    )
    return hashlib.sha256(payload).hexdigest()

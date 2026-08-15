from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from prodkit_control_core import canonical_json_bytes, sha256_hex


def test_canonical_json_is_stable() -> None:
    left = {
        "b": Decimal("1.20"),
        "a": UUID("00000000-0000-0000-0000-000000000001"),
        "time": datetime(2026, 8, 6, 20, 0, tzinfo=UTC),
    }
    right = {"time": left["time"], "a": left["a"], "b": left["b"]}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert sha256_hex(left) == sha256_hex(right)

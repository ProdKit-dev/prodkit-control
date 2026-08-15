from __future__ import annotations

import secrets
from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)

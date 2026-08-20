from __future__ import annotations

import asyncio
from uuid import UUID

from prodkit_control_core import RunRecord


class InMemoryRunStore:
    """Process-local run store for tests and development profiles."""

    def __init__(self) -> None:
        self._runs: dict[UUID, RunRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, run: RunRecord) -> None:
        async with self._lock:
            if run.run_id in self._runs:
                raise ValueError(f"run {run.run_id} already exists")
            self._runs[run.run_id] = run

    async def replace(self, run: RunRecord) -> None:
        async with self._lock:
            current = self._runs.get(run.run_id)
            if current is None:
                raise KeyError(run.run_id)
            if current.tenant_id != run.tenant_id or current.started_at != run.started_at:
                raise ValueError("run identity is immutable")
            self._runs[run.run_id] = run

    async def get(self, run_id: UUID) -> RunRecord | None:
        async with self._lock:
            return self._runs.get(run_id)

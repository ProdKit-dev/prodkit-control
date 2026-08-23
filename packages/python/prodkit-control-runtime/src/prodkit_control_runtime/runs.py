from __future__ import annotations

import asyncio
from uuid import UUID

from prodkit_control_core import RunRecord


class InMemoryRunStore:
    """Process-local tenant-partitioned run store for tests and standalone deployments."""

    def __init__(self) -> None:
        self._runs: dict[tuple[str, UUID], RunRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, run: RunRecord) -> None:
        async with self._lock:
            identity = (run.tenant_id, run.run_id)
            if identity in self._runs:
                raise ValueError(f"run {run.run_id} already exists")
            if any(run_id == run.run_id for _, run_id in self._runs):
                raise ValueError(f"run {run.run_id} is already owned by another tenant")
            self._runs[identity] = run

    async def replace(self, run: RunRecord) -> None:
        async with self._lock:
            identity = (run.tenant_id, run.run_id)
            current = self._runs.get(identity)
            if current is None:
                raise KeyError(run.run_id)
            if current.started_at != run.started_at:
                raise ValueError("run identity is immutable")
            self._runs[identity] = run

    async def get(self, *, tenant_id: str, run_id: UUID) -> RunRecord | None:
        async with self._lock:
            return self._runs.get((tenant_id, run_id))

from __future__ import annotations

import asyncio
from uuid import UUID

from prodkit_control_core import ExecutionAttemptRecord


class InMemoryExecutionAttemptStore:
    """Tenant-partitioned execution-attempt store for standalone profiles."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, UUID], ExecutionAttemptRecord] = {}
        self._latest_by_action: dict[tuple[str, UUID], UUID] = {}
        self._lock = asyncio.Lock()

    async def create(self, attempt: ExecutionAttemptRecord) -> None:
        async with self._lock:
            identity = (attempt.tenant_id, attempt.attempt_id)
            if identity in self._records:
                raise ValueError(f"execution attempt {attempt.attempt_id} already exists")
            if any(attempt_id == attempt.attempt_id for _, attempt_id in self._records):
                raise ValueError("execution attempt id is already owned by another tenant")
            self._records[identity] = attempt
            self._latest_by_action[(attempt.tenant_id, attempt.action_id)] = attempt.attempt_id

    async def replace(self, attempt: ExecutionAttemptRecord) -> None:
        async with self._lock:
            identity = (attempt.tenant_id, attempt.attempt_id)
            current = self._records.get(identity)
            if current is None:
                raise KeyError(f"execution attempt {attempt.attempt_id} does not exist")
            if current.action_id != attempt.action_id or current.run_id != attempt.run_id:
                raise ValueError("execution-attempt identity is immutable")
            self._records[identity] = attempt
            self._latest_by_action[(attempt.tenant_id, attempt.action_id)] = attempt.attempt_id

    async def get(
        self, *, tenant_id: str, attempt_id: UUID
    ) -> ExecutionAttemptRecord | None:
        async with self._lock:
            return self._records.get((tenant_id, attempt_id))

    async def latest_for_action(
        self, *, tenant_id: str, action_id: UUID
    ) -> ExecutionAttemptRecord | None:
        async with self._lock:
            attempt_id = self._latest_by_action.get((tenant_id, action_id))
            return self._records.get((tenant_id, attempt_id)) if attempt_id is not None else None

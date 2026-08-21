from __future__ import annotations

import asyncio
from uuid import UUID

from prodkit_control_core import ExecutionAttemptRecord


class InMemoryExecutionAttemptStore:
    """Process-local execution-attempt store used only by tests and development profiles."""

    def __init__(self) -> None:
        self._records: dict[UUID, ExecutionAttemptRecord] = {}
        self._latest_by_action: dict[UUID, UUID] = {}
        self._lock = asyncio.Lock()

    async def create(self, attempt: ExecutionAttemptRecord) -> None:
        async with self._lock:
            if attempt.attempt_id in self._records:
                raise ValueError(f"execution attempt {attempt.attempt_id} already exists")
            self._records[attempt.attempt_id] = attempt
            self._latest_by_action[attempt.action_id] = attempt.attempt_id

    async def replace(self, attempt: ExecutionAttemptRecord) -> None:
        async with self._lock:
            if attempt.attempt_id not in self._records:
                raise KeyError(f"execution attempt {attempt.attempt_id} does not exist")
            current = self._records[attempt.attempt_id]
            if current.action_id != attempt.action_id or current.tenant_id != attempt.tenant_id:
                raise ValueError("execution-attempt identity is immutable")
            self._records[attempt.attempt_id] = attempt
            self._latest_by_action[attempt.action_id] = attempt.attempt_id

    async def get(self, attempt_id: UUID) -> ExecutionAttemptRecord | None:
        async with self._lock:
            return self._records.get(attempt_id)

    async def latest_for_action(self, action_id: UUID) -> ExecutionAttemptRecord | None:
        async with self._lock:
            attempt_id = self._latest_by_action.get(action_id)
            return self._records.get(attempt_id) if attempt_id is not None else None

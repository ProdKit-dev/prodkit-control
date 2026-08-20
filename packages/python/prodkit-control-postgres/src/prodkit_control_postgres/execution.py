from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodkit_control_core import (
    DuplicateActionError,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    ExecutionResult,
)

from .models import ExecutionAttemptRow, IdempotencyRow


class PostgresIdempotencyStore:
    """Atomic tenant-scoped idempotency ownership backed by PostgreSQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def claim(self, *, tenant_id: str, key: str, action_digest: str) -> bool:
        async with self._sessions.begin() as session:
            statement = (
                pg_insert(IdempotencyRow)
                .values(
                    tenant_id=tenant_id,
                    key=key,
                    action_digest=action_digest,
                    state="claimed",
                    claimed_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "key"])
                .returning(IdempotencyRow.action_digest)
            )
            inserted = (await session.execute(statement)).scalar_one_or_none()
            if inserted is not None:
                return True
            current = await session.get(IdempotencyRow, (tenant_id, key), with_for_update=True)
            if current is None:
                raise RuntimeError("idempotency claim disappeared during transaction")
            if current.action_digest != action_digest:
                raise DuplicateActionError(
                    f"idempotency key {key!r} already belongs to another action digest"
                )
            return False

    async def complete(self, *, tenant_id: str, key: str, result: ExecutionResult) -> None:
        async with self._sessions.begin() as session:
            current = await session.get(IdempotencyRow, (tenant_id, key), with_for_update=True)
            if current is None:
                raise KeyError(f"idempotency key {key!r} was not claimed")
            if current.state == "completed":
                existing = (
                    ExecutionResult.model_validate(current.result) if current.result is not None else None
                )
                if existing != result:
                    raise DuplicateActionError("completed idempotency result is immutable")
                return
            current.state = "completed"
            current.completed_at = datetime.now(UTC)
            current.result = result.model_dump(mode="json")

    async def result(self, *, tenant_id: str, key: str) -> ExecutionResult | None:
        async with self._sessions() as session:
            current = await session.get(IdempotencyRow, (tenant_id, key))
            if current is None or current.result is None:
                return None
            return ExecutionResult.model_validate(current.result)


class PostgresExecutionAttemptStore:
    """Transactional execution-attempt journal with explicit legal state transitions."""

    _TRANSITIONS: dict[ExecutionAttemptState, frozenset[ExecutionAttemptState]] = {
        ExecutionAttemptState.CLAIMED: frozenset({ExecutionAttemptState.STARTED}),
        ExecutionAttemptState.STARTED: frozenset(
            {
                ExecutionAttemptState.SUCCEEDED,
                ExecutionAttemptState.FAILED,
                ExecutionAttemptState.UNCERTAIN,
            }
        ),
        ExecutionAttemptState.SUCCEEDED: frozenset(),
        ExecutionAttemptState.FAILED: frozenset(),
        ExecutionAttemptState.UNCERTAIN: frozenset(),
    }

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(self, attempt: ExecutionAttemptRecord) -> None:
        async with self._sessions.begin() as session:
            session.add(self._row(attempt))

    async def replace(self, attempt: ExecutionAttemptRecord) -> None:
        async with self._sessions.begin() as session:
            current = await session.get(
                ExecutionAttemptRow,
                attempt.attempt_id,
                with_for_update=True,
            )
            if current is None:
                raise KeyError(f"execution attempt {attempt.attempt_id} does not exist")
            if (
                current.action_id != attempt.action_id
                or current.run_id != attempt.run_id
                or current.tenant_id != attempt.tenant_id
                or current.idempotency_key != attempt.idempotency_key
                or current.action_digest != attempt.action_digest
                or current.executor_name != attempt.executor_name
                or current.executor_version != attempt.executor_version
                or current.executor_identity != attempt.executor_identity
            ):
                raise ValueError("execution-attempt identity is immutable")
            current_state = ExecutionAttemptState(current.state)
            if attempt.state not in self._TRANSITIONS[current_state]:
                raise ValueError(
                    f"illegal execution-attempt transition {current_state.value} -> {attempt.state.value}"
                )
            current.state = attempt.state.value
            current.started_at = attempt.started_at
            current.finished_at = attempt.finished_at
            current.document = attempt.model_dump(mode="json")

    async def get(self, attempt_id: UUID) -> ExecutionAttemptRecord | None:
        async with self._sessions() as session:
            row = await session.get(ExecutionAttemptRow, attempt_id)
            return ExecutionAttemptRecord.model_validate(row.document) if row is not None else None

    async def latest_for_action(self, action_id: UUID) -> ExecutionAttemptRecord | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ExecutionAttemptRow)
                .where(ExecutionAttemptRow.action_id == action_id)
                .order_by(ExecutionAttemptRow.claimed_at.desc())
                .limit(1)
            )
            return ExecutionAttemptRecord.model_validate(row.document) if row is not None else None

    @staticmethod
    def _row(attempt: ExecutionAttemptRecord) -> ExecutionAttemptRow:
        return ExecutionAttemptRow(
            attempt_id=attempt.attempt_id,
            action_id=attempt.action_id,
            run_id=attempt.run_id,
            tenant_id=attempt.tenant_id,
            idempotency_key=attempt.idempotency_key,
            action_digest=attempt.action_digest,
            executor_name=attempt.executor_name,
            executor_version=attempt.executor_version,
            executor_identity=attempt.executor_identity,
            state=attempt.state.value,
            claimed_at=attempt.claimed_at,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            document=attempt.model_dump(mode="json"),
        )

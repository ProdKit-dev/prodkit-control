from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import asyncpg
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from prodkit_control_core import (
    DuplicateActionError,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    ExecutionResult,
)
from prodkit_control_postgres import (
    PostgresExecutionAttemptStore,
    PostgresIdempotencyStore,
    assert_schema_compatible,
)


def _connection_values() -> tuple[str, int, str, str, str]:
    return (
        os.environ["PRODKIT_POSTGRES_HOST"],
        int(os.environ["PRODKIT_POSTGRES_PORT"]),
        os.environ["PRODKIT_POSTGRES_DATABASE"],
        os.environ["PRODKIT_POSTGRES_USER"],
        os.environ["PRODKIT_POSTGRES_PASSWORD"],
    )


async def _apply_migrations() -> None:
    host, port, database, user, password = _connection_values()
    connection = await asyncpg.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
    )
    try:
        for migration in sorted(
            Path("packages/python/prodkit-control-postgres/migrations").glob("*.sql")
        ):
            await connection.execute(migration.read_text(encoding="utf-8"))
        version = await connection.fetchval(
            "SELECT version FROM prodkit_schema_metadata WHERE singleton = TRUE"
        )
        if version != 3:
            raise AssertionError(f"expected schema version 3, got {version!r}")
    finally:
        await connection.close()


async def _exercise_durable_stores() -> None:
    host, port, database, user, password = _connection_values()
    engine = create_async_engine(
        f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}",
        pool_pre_ping=True,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await assert_schema_compatible(sessions)

        tenant_id = "ci-tenant-a"
        idempotency_key = f"ci-{uuid4()}"
        action_digest = "a" * 64
        action_id = uuid4()
        run_id = uuid4()
        attempt_id = uuid4()
        now = datetime.now(UTC)

        idempotency = PostgresIdempotencyStore(sessions)
        attempts = PostgresExecutionAttemptStore(sessions)

        assert await idempotency.claim(
            tenant_id=tenant_id,
            key=idempotency_key,
            action_digest=action_digest,
        )
        assert not await idempotency.claim(
            tenant_id=tenant_id,
            key=idempotency_key,
            action_digest=action_digest,
        )
        try:
            await idempotency.claim(
                tenant_id=tenant_id,
                key=idempotency_key,
                action_digest="b" * 64,
            )
        except DuplicateActionError:
            pass
        else:
            raise AssertionError("reusing an idempotency key for another action digest must fail")

        assert await idempotency.claim(
            tenant_id="ci-tenant-b",
            key=idempotency_key,
            action_digest="b" * 64,
        ), "idempotency ownership must be tenant scoped"

        claimed = ExecutionAttemptRecord(
            attempt_id=attempt_id,
            action_id=action_id,
            run_id=run_id,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            action_digest=action_digest,
            executor_name="ci-executor",
            executor_version="1.0.0",
            executor_identity="spiffe://prodkit.test/executor/ci",
            state=ExecutionAttemptState.CLAIMED,
            claimed_at=now,
        )
        await attempts.create(claimed)
        started = claimed.model_copy(
            update={"state": ExecutionAttemptState.STARTED, "started_at": now}
        )
        await attempts.replace(started)
        uncertain = started.model_copy(
            update={
                "state": ExecutionAttemptState.UNCERTAIN,
                "finished_at": now,
                "uncertainty_reason": "synthetic crash-after-start proof",
                "error_type": "SyntheticCrash",
                "error_message": "executor outcome intentionally unknown",
            }
        )
        await attempts.replace(uncertain)
        recovered = await attempts.get(attempt_id)
        assert recovered == uncertain
        assert (await attempts.latest_for_action(action_id)) == uncertain

        result = ExecutionResult(
            action_id=action_id,
            execution_attempt_id=uuid4(),
            executor_name="ci-executor",
            executor_version="1.0.0",
            executor_identity="spiffe://prodkit.test/executor/ci",
            started_at=now,
            completed_at=now,
            succeeded=True,
            exit_code=0,
            result={"proof": "durable-idempotency"},
        )
        await idempotency.complete(
            tenant_id=tenant_id,
            key=idempotency_key,
            result=result,
        )
        assert await idempotency.result(tenant_id=tenant_id, key=idempotency_key) == result
        assert not await idempotency.claim(
            tenant_id=tenant_id,
            key=idempotency_key,
            action_digest=action_digest,
        )
    finally:
        await engine.dispose()


async def main() -> None:
    await _apply_migrations()
    await _exercise_durable_stores()


if __name__ == "__main__":
    asyncio.run(main())

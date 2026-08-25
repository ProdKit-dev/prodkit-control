from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

import asyncpg

from prodkit_control_core import (
    ActionSpec,
    CredentialLease,
    CredentialMaterialResolver,
    EffectClass,
    ExecutionResult,
    StateObservation,
    sha256_hex,
)


class DatabaseConnection(Protocol):
    async def fetch(self, query: str, *args: object, timeout: float | None = None) -> Sequence[Mapping[str, Any]]: ...

    async def execute(self, query: str, *args: object, timeout: float | None = None) -> str: ...

    async def close(self) -> None: ...


ConnectFactory = Callable[..., Awaitable[DatabaseConnection]]


@dataclass(frozen=True)
class DatabaseExecutorConfig:
    allowed_databases: frozenset[str]
    allowed_statement_sha256: frozenset[str]
    timeout_seconds: float = 10.0
    max_rows: int = 1_000
    executor_identity: str = "spiffe://prodkit.local/executor/database"


class ConstrainedDatabaseExecutor:
    """Execute allowlisted, parameterized PostgreSQL statements with leased credentials."""

    name = "database"
    version = "1.0.0"

    def __init__(
        self,
        config: DatabaseExecutorConfig,
        *,
        credential_resolver: CredentialMaterialResolver,
        connect: ConnectFactory | None = None,
    ) -> None:
        if not config.allowed_databases:
            raise ValueError("database executor requires an explicit database allowlist")
        if not config.allowed_statement_sha256:
            raise ValueError("database executor requires explicit statement digests")
        if config.timeout_seconds <= 0:
            raise ValueError("database timeout must be positive")
        if config.max_rows < 1:
            raise ValueError("database max_rows must be positive")
        self._config = config
        self._resolver = credential_resolver
        self._connect: ConnectFactory = connect or asyncpg.connect
        self.identity = config.executor_identity

    async def validate(self, action: ActionSpec) -> None:
        if action.operation not in {"query", "execute"}:
            raise PermissionError(f"database operation {action.operation!r} is not allowed")
        if action.target.resource_id not in self._config.allowed_databases:
            raise PermissionError("database target is not allowlisted")
        statement = self._statement(action)
        if sha256_hex(statement) not in self._config.allowed_statement_sha256:
            raise PermissionError("database statement is not allowlisted")
        self._parameters(action)
        if action.operation == "query" and action.effect_class is not EffectClass.READ:
            raise ValueError("database query actions must use effect_class=read")
        if action.operation == "execute" and action.effect_class is EffectClass.READ:
            raise ValueError("database execute actions cannot use effect_class=read")

    async def execute(self, action: ActionSpec) -> ExecutionResult:
        del action
        raise PermissionError("database execution requires a short-lived credential lease")

    async def execute_attempt(self, action: ActionSpec, *, attempt_id: UUID) -> ExecutionResult:
        del action, attempt_id
        raise PermissionError("database execution requires a short-lived credential lease")

    async def execute_attempt_with_lease(
        self,
        action: ActionSpec,
        *,
        attempt_id: UUID,
        credential_lease: CredentialLease,
    ) -> ExecutionResult:
        await self.validate(action)
        self._validate_lease(action, credential_lease)
        material = await self._resolver.resolve(credential_lease)
        dsn = material.get("dsn")
        if not dsn:
            raise PermissionError("database credential lease did not resolve to a DSN")
        started = datetime.now(UTC)
        connection = await self._connect(dsn=dsn, timeout=self._config.timeout_seconds)
        try:
            statement = self._statement(action)
            parameters = self._parameters(action)
            if action.operation == "query":
                rows = await connection.fetch(
                    statement,
                    *parameters,
                    timeout=self._config.timeout_seconds,
                )
                if len(rows) > self._config.max_rows:
                    raise ValueError("database result exceeds configured row limit")
                normalized_rows = [dict(row) for row in rows]
                result_payload: dict[str, Any] = {
                    "row_count": len(normalized_rows),
                    "rows_sha256": sha256_hex(normalized_rows),
                }
                provider_operation_id = None
            else:
                command = await connection.execute(
                    statement,
                    *parameters,
                    timeout=self._config.timeout_seconds,
                )
                result_payload = {"command": command}
                provider_operation_id = command
        finally:
            await connection.close()
        completed = datetime.now(UTC)
        return ExecutionResult(
            action_id=action.action_id,
            execution_attempt_id=attempt_id,
            executor_name=self.name,
            executor_version=self.version,
            executor_identity=self.identity,
            started_at=started,
            completed_at=completed,
            succeeded=True,
            provider_operation_id=provider_operation_id,
            result=result_payload,
        )

    async def observe(self, action: ActionSpec, result: ExecutionResult) -> StateObservation:
        state = {
            "database": action.target.resource_id,
            "operation": action.operation,
            "result": result.result,
            "provider_operation_id": result.provider_operation_id,
        }
        return StateObservation(
            observation_id=uuid4(),
            action_id=action.action_id,
            source=self.name,
            observed_at=datetime.now(UTC),
            state_digest=sha256_hex(state),
            state=state,
        )

    @staticmethod
    def _statement(action: ActionSpec) -> str:
        statement = action.arguments.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError("database action requires non-empty arguments.statement")
        normalized = statement.strip()
        if "\x00" in normalized:
            raise ValueError("database statement contains an invalid NUL byte")
        return normalized

    @staticmethod
    def _parameters(action: ActionSpec) -> tuple[object, ...]:
        raw = action.arguments.get("parameters", ())
        if not isinstance(raw, (list, tuple)):
            raise ValueError("database arguments.parameters must be a list or tuple")
        return tuple(raw)

    def _validate_lease(self, action: ActionSpec, lease: CredentialLease) -> None:
        if (
            lease.action_id != action.action_id
            or lease.tenant_id != action.tenant_id
            or lease.executor_identity != self.identity
            or datetime.now(UTC) >= lease.expires_at
        ):
            raise PermissionError("credential lease is not valid for this database action")

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from prodkit_control_core import (
    ActionSpec,
    CredentialLease,
    CredentialMaterialResolver,
    ExecutionResult,
    StateObservation,
    sha256_hex,
)


@dataclass(frozen=True)
class DeploymentReceipt:
    operation_id: str
    state: Mapping[str, Any]
    retryable: bool = False


class DeploymentTransport(Protocol):
    async def perform(
        self,
        *,
        operation: str,
        environment: str,
        resource_id: str,
        arguments: Mapping[str, Any],
        credential: Mapping[str, str],
        idempotency_key: str,
    ) -> DeploymentReceipt: ...


@dataclass(frozen=True)
class DeploymentExecutorConfig:
    allowed_environments: frozenset[str]
    allowed_resources: frozenset[str]
    allowed_operations: frozenset[str] = frozenset({"deploy", "promote", "rollback", "cancel"})
    executor_identity: str = "spiffe://prodkit.local/executor/deployment"


class ConstrainedDeploymentExecutor:
    """Provider-neutral deployment executor with exact target and artifact constraints."""

    name = "deployment"
    version = "1.0.0"

    def __init__(
        self,
        config: DeploymentExecutorConfig,
        *,
        credential_resolver: CredentialMaterialResolver,
        transport: DeploymentTransport,
    ) -> None:
        if not config.allowed_environments:
            raise ValueError("deployment executor requires an environment allowlist")
        if not config.allowed_resources:
            raise ValueError("deployment executor requires a resource allowlist")
        if not config.allowed_operations:
            raise ValueError("deployment executor requires an operation allowlist")
        self._config = config
        self._resolver = credential_resolver
        self._transport = transport
        self.identity = config.executor_identity

    async def validate(self, action: ActionSpec) -> None:
        if action.operation not in self._config.allowed_operations:
            raise PermissionError(f"deployment operation {action.operation!r} is not allowed")
        if action.target.environment not in self._config.allowed_environments:
            raise PermissionError("deployment environment is not allowlisted")
        if action.target.resource_id not in self._config.allowed_resources:
            raise PermissionError("deployment resource is not allowlisted")
        if action.operation in {"deploy", "promote"}:
            self._artifact_digest(action)
        if action.operation == "rollback":
            release_id = action.arguments.get("release_id")
            if not isinstance(release_id, str) or not release_id.strip():
                raise ValueError("rollback requires non-empty arguments.release_id")
        if action.operation == "cancel":
            operation_id = action.arguments.get("operation_id")
            if not isinstance(operation_id, str) or not operation_id.strip():
                raise ValueError("cancel requires non-empty arguments.operation_id")

    async def execute(self, action: ActionSpec) -> ExecutionResult:
        del action
        raise PermissionError("deployment execution requires a short-lived credential lease")

    async def execute_attempt(self, action: ActionSpec, *, attempt_id: UUID) -> ExecutionResult:
        del action, attempt_id
        raise PermissionError("deployment execution requires a short-lived credential lease")

    async def execute_attempt_with_lease(
        self,
        action: ActionSpec,
        *,
        attempt_id: UUID,
        credential_lease: CredentialLease,
    ) -> ExecutionResult:
        await self.validate(action)
        self._validate_lease(action, credential_lease)
        credential = await self._resolver.resolve(credential_lease)
        if not credential:
            raise PermissionError("deployment credential lease resolved to no material")
        started = datetime.now(UTC)
        receipt = await self._transport.perform(
            operation=action.operation,
            environment=action.target.environment,
            resource_id=action.target.resource_id,
            arguments=dict(action.arguments),
            credential=credential,
            idempotency_key=action.idempotency_key,
        )
        completed = datetime.now(UTC)
        state = dict(receipt.state)
        return ExecutionResult(
            action_id=action.action_id,
            execution_attempt_id=attempt_id,
            executor_name=self.name,
            executor_version=self.version,
            executor_identity=self.identity,
            started_at=started,
            completed_at=completed,
            succeeded=True,
            provider_operation_id=receipt.operation_id,
            result={"state_sha256": sha256_hex(state), "state": state},
            retryable=receipt.retryable,
        )

    async def observe(self, action: ActionSpec, result: ExecutionResult) -> StateObservation:
        state = {
            "environment": action.target.environment,
            "resource_id": action.target.resource_id,
            "operation": action.operation,
            "provider_operation_id": result.provider_operation_id,
            "result": result.result,
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
    def _artifact_digest(action: ActionSpec) -> str:
        digest = action.arguments.get("artifact_digest")
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("deployment requires sha256:<64 lowercase hex> arguments.artifact_digest")
        return digest

    def _validate_lease(self, action: ActionSpec, lease: CredentialLease) -> None:
        if (
            lease.action_id != action.action_id
            or lease.tenant_id != action.tenant_id
            or lease.executor_identity != self.identity
            or datetime.now(UTC) >= lease.expires_at
        ):
            raise PermissionError("credential lease is not valid for this deployment action")

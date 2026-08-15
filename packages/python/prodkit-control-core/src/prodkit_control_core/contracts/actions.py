from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from prodkit_control_core.canonical import sha256_hex

from .artifacts import ArtifactRef
from .base import ContractModel, NonBlankStr, Sha256


class RiskClass(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EffectClass(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    PRIVILEGED = "privileged"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    POLICY_DENIED = "policy_denied"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_DENIED = "approval_denied"
    AUTHORIZED = "authorized"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_SUCCEEDED = "execution_succeeded"
    EXECUTION_FAILED = "execution_failed"
    EFFECT_VERIFIED = "effect_verified"
    EFFECT_MISMATCHED = "effect_mismatched"
    RECONCILED = "reconciled"


class ActionTarget(ContractModel):
    system: NonBlankStr
    environment: NonBlankStr
    resource_type: NonBlankStr
    resource_id: NonBlankStr
    region: NonBlankStr | None = None
    expected_pre_state_digest: Sha256 | None = None


class ActionSpec(ContractModel):
    schema_name: str = "prodkit.action-spec"
    schema_version: str = "1.0.0"
    action_id: UUID
    run_id: UUID
    tenant_id: NonBlankStr
    executor: NonBlankStr
    operation: NonBlankStr
    effect_class: EffectClass
    risk_class: RiskClass
    target: ActionTarget
    arguments: dict[str, Any] = Field(default_factory=dict)
    argument_artifact: ArtifactRef | None = None
    idempotency_key: NonBlankStr
    proposed_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    work_pack_id: NonBlankStr | None = None
    repository_operation_id: NonBlankStr | None = None
    policy_context: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    expected_effect: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_expiry(self) -> ActionSpec:
        if self.expires_at is not None and self.expires_at <= self.proposed_at:
            raise ValueError("expires_at must be later than proposed_at")
        return self

    def digest_material(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"argument_artifact"})

    @property
    def digest(self) -> str:
        return sha256_hex(self.digest_material())


class ExecutionResult(ContractModel):
    action_id: UUID
    execution_attempt_id: UUID
    executor_name: NonBlankStr
    executor_version: NonBlankStr
    executor_identity: NonBlankStr
    started_at: AwareDatetime
    completed_at: AwareDatetime
    succeeded: bool
    exit_code: int | None = None
    provider_operation_id: NonBlankStr | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    result_artifact: ArtifactRef | None = None
    retryable: bool = False
    error_type: NonBlankStr | None = None
    error_message: NonBlankStr | None = None

    @model_validator(mode="after")
    def validate_result(self) -> ExecutionResult:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.succeeded and (self.error_type or self.error_message):
            raise ValueError("successful results cannot carry an error")
        if not self.succeeded and not self.error_type:
            raise ValueError("failed results require error_type")
        return self

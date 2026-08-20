from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, model_validator

from .base import ContractModel, NonBlankStr, Sha256


class ExecutionAttemptState(StrEnum):
    CLAIMED = "claimed"
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class ExecutionAttemptRecord(ContractModel):
    """Durable lifecycle record for one externally intended execution attempt."""

    schema_name: str = "prodkit.execution-attempt"
    schema_version: str = "1.0.0"
    attempt_id: UUID
    action_id: UUID
    run_id: UUID
    tenant_id: NonBlankStr
    idempotency_key: NonBlankStr
    action_digest: Sha256
    executor_name: NonBlankStr
    executor_version: NonBlankStr
    executor_identity: NonBlankStr
    state: ExecutionAttemptState
    claimed_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    result_digest: Sha256 | None = None
    provider_operation_id: NonBlankStr | None = None
    error_type: NonBlankStr | None = None
    error_message: str | None = None
    uncertainty_reason: str | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> ExecutionAttemptRecord:
        if self.started_at is not None and self.started_at < self.claimed_at:
            raise ValueError("started_at cannot precede claimed_at")
        floor = self.started_at or self.claimed_at
        if self.finished_at is not None and self.finished_at < floor:
            raise ValueError("finished_at cannot precede the attempt start")
        if self.state is ExecutionAttemptState.CLAIMED:
            if self.started_at is not None or self.finished_at is not None:
                raise ValueError("claimed attempts cannot have execution timestamps")
        elif self.state is ExecutionAttemptState.STARTED:
            if self.started_at is None or self.finished_at is not None:
                raise ValueError("started attempts require started_at and no finished_at")
        else:
            if self.started_at is None or self.finished_at is None:
                raise ValueError("terminal attempts require started_at and finished_at")
        if self.state is ExecutionAttemptState.SUCCEEDED and self.result_digest is None:
            raise ValueError("successful attempts require result_digest")
        if self.state is ExecutionAttemptState.UNCERTAIN and not self.uncertainty_reason:
            raise ValueError("uncertain attempts require uncertainty_reason")
        return self


class CredentialLease(ContractModel):
    """Non-secret metadata for a short-lived credential issued to a workload identity."""

    schema_name: str = "prodkit.credential-lease"
    schema_version: str = "1.0.0"
    lease_id: UUID
    tenant_id: NonBlankStr
    action_id: UUID
    executor_identity: NonBlankStr
    audience: NonBlankStr
    scopes: tuple[NonBlankStr, ...] = ()
    credential_reference: NonBlankStr
    issued_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_expiry(self) -> CredentialLease:
        if self.expires_at <= self.issued_at:
            raise ValueError("credential lease must expire after issuance")
        return self

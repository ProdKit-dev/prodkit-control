from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, model_validator

from .base import ContractModel, NonBlankStr, Sha256
from .identity import ActorRef


class ApprovalOutcome(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ApprovalDecision(ContractModel):
    schema_name: str = "prodkit.approval-decision"
    schema_version: str = "1.0.0"
    approval_id: UUID
    action_id: UUID
    action_digest: Sha256
    target_digest: Sha256
    tenant_id: NonBlankStr
    environment: NonBlankStr
    policy_decision_id: UUID
    policy_revision: NonBlankStr
    approver: ActorRef
    approver_role: NonBlankStr
    decided_at: AwareDatetime
    outcome: ApprovalOutcome
    expires_at: AwareDatetime
    reason: NonBlankStr

    @model_validator(mode="after")
    def validate_expiry(self) -> ApprovalDecision:
        if self.expires_at <= self.decided_at:
            raise ValueError("approval expiry must be later than decision time")
        return self

    def authorizes(
        self,
        *,
        action_digest: str,
        target_digest: str,
        policy_decision_id: UUID,
        policy_revision: str,
        tenant_id: str,
        environment: str,
        at: AwareDatetime,
    ) -> bool:
        return (
            self.outcome is ApprovalOutcome.APPROVED
            and self.action_digest == action_digest
            and self.target_digest == target_digest
            and self.policy_decision_id == policy_decision_id
            and self.policy_revision == policy_revision
            and self.tenant_id == tenant_id
            and self.environment == environment
            and at < self.expires_at
        )

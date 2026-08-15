from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field

from .base import ContractModel, NonBlankStr, Sha256


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class PolicyDecision(ContractModel):
    schema_name: str = "prodkit.policy-decision"
    schema_version: str = "1.0.0"
    decision_id: UUID
    action_id: UUID
    action_digest: Sha256
    tenant_id: NonBlankStr
    policy_engine: NonBlankStr
    policy_bundle: NonBlankStr
    policy_revision: NonBlankStr
    evaluated_at: AwareDatetime
    outcome: PolicyOutcome
    reason_codes: tuple[NonBlankStr, ...] = ()
    constraints: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    required_approval_roles: tuple[NonBlankStr, ...] = ()
    expires_at: AwareDatetime | None = None
    raw_decision_artifact_sha256: Sha256 | None = None

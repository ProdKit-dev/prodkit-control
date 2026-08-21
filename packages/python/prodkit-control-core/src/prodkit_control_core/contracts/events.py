from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .artifacts import ArtifactRef
from .base import ContractModel, NonBlankStr, Sha256, SpanId, TraceId
from .identity import ActorRef
from .lineage import LineageNodeRef

EVENT_SCHEMA_VERSION = "1.0.0"


class EventType(StrEnum):
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    MODEL_REQUESTED = "model.requested"
    MODEL_RESPONDED = "model.responded"
    CONTEXT_ACCESSED = "context.accessed"
    ACTION_PROPOSED = "action.proposed"
    POLICY_EVALUATED = "policy.evaluated"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    CREDENTIAL_LEASE_ISSUED = "credential.lease_issued"
    CREDENTIAL_LEASE_REVOKED = "credential.lease_revoked"
    CREDENTIAL_LEASE_REVOCATION_FAILED = "credential.lease_revocation_failed"
    EXECUTION_STARTED = "execution.started"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_UNCERTAIN = "execution.uncertain"
    STATE_OBSERVED = "state.observed"
    VERIFICATION_COMPLETED = "verification.completed"
    RECONCILIATION_COMPLETED = "reconciliation.completed"
    ARTIFACT_RECORDED = "artifact.recorded"
    LINEAGE_NODE_RECORDED = "lineage.node_recorded"
    LINEAGE_RELATION_RECORDED = "lineage.relation_recorded"
    LINEAGE_ASSESSED = "lineage.assessed"
    ROLLBACK_STARTED = "rollback.started"
    ROLLBACK_COMPLETED = "rollback.completed"
    CORRECTION_RECORDED = "correction.recorded"


class EventIntegrity(ContractModel):
    algorithm: str = "sha256"
    canonicalization: str = "prodkit-json-v1"
    previous_event_hash: Sha256 | None = None
    event_hash: Sha256
    signature: NonBlankStr | None = None
    signing_key_id: NonBlankStr | None = None


class ControlEventDraft(ContractModel):
    schema_name: str = "prodkit.control-event"
    schema_version: str = EVENT_SCHEMA_VERSION
    event_id: UUID
    run_id: UUID
    tenant_id: NonBlankStr
    event_type: EventType
    occurred_at: AwareDatetime
    recorded_at: AwareDatetime
    actor: ActorRef
    trace_id: TraceId
    span_id: SpanId
    parent_event_id: UUID | None = None
    causation_event_id: UUID | None = None
    correlation_id: NonBlankStr | None = None
    action_id: UUID | None = None
    lineage: tuple[LineageNodeRef, ...] = ()
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[ArtifactRef, ...] = ()

    @model_validator(mode="after")
    def validate_actor_tenant(self) -> ControlEventDraft:
        if self.actor.tenant_id != self.tenant_id:
            raise ValueError("event actor tenant must match event tenant")
        return self


class ControlEvent(ControlEventDraft):
    sequence: int = Field(ge=1)
    integrity: EventIntegrity

    def hash_material(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"integrity"})

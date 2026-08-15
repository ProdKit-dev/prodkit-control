from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field

from .base import ContractModel, NonBlankStr, Sha256
from .identity import ActorRef
from .lineage import LineageNodeRef


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INCOMPLETE = "incomplete"


class RunRecord(ContractModel):
    schema_name: str = "prodkit.control-run"
    schema_version: str = "1.0.0"
    run_id: UUID
    tenant_id: NonBlankStr
    status: RunStatus
    initiated_by: ActorRef
    environment: NonBlankStr
    purpose: NonBlankStr
    trace_id: NonBlankStr
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    source_intent_digest: Sha256 | None = None
    specification_revision: LineageNodeRef | None = None
    lineage_graph_digest: Sha256 | None = None
    workflow_id: NonBlankStr | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

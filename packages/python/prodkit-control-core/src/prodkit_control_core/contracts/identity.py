from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from .base import ContractModel, NonBlankStr


class ActorKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SERVICE = "service"
    WORKFLOW = "workflow"
    EXECUTOR = "executor"


class ActorRef(ContractModel):
    kind: ActorKind
    id: NonBlankStr
    display_name: NonBlankStr | None = None
    tenant_id: NonBlankStr
    workload_identity: NonBlankStr | None = None
    attributes: dict[str, str] = Field(default_factory=dict)

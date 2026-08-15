from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, Field

from .artifacts import ArtifactRef
from .base import ContractModel, NonBlankStr


class ToolProposal(ContractModel):
    tool_call_id: NonBlankStr
    tool_name: NonBlankStr
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_arguments_artifact: ArtifactRef | None = None


class CanonicalModelRequest(ContractModel):
    request_id: UUID
    run_id: UUID
    tenant_id: NonBlankStr
    provider_name: NonBlankStr
    model_name: NonBlankStr
    agent_id: NonBlankStr
    prompt_version: NonBlankStr
    schema_version: NonBlankStr
    requested_at: AwareDatetime
    messages: tuple[dict[str, Any], ...]
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_artifact: ArtifactRef | None = None
    trace_id: NonBlankStr


class CanonicalModelResponse(ContractModel):
    request_id: UUID
    provider_name: NonBlankStr
    requested_model_name: NonBlankStr
    returned_model_name: NonBlankStr | None = None
    provider_run_id: NonBlankStr | None = None
    completed_at: AwareDatetime
    stop_reason: NonBlankStr | None = None
    output_text: str | None = None
    tool_proposals: tuple[ToolProposal, ...] = ()
    usage: dict[str, int] = Field(default_factory=dict)
    output_artifact: ArtifactRef | None = None
    raw_response_artifact: ArtifactRef | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from prodkit_control_core import CanonicalModelRequest, CanonicalModelResponse, ToolProposal


@dataclass(frozen=True)
class PydanticAIResult:
    output_text: str | None = None
    tool_proposals: tuple[ToolProposal, ...] = ()
    usage: Mapping[str, int] | None = None
    returned_model_name: str | None = None
    provider_run_id: str | None = None
    stop_reason: str | None = None


PydanticAIRunner = Callable[[CanonicalModelRequest], Awaitable[PydanticAIResult]]


class PydanticAIProvider:
    """Typed bridge from a host Pydantic AI runner to canonical model contracts."""

    name = "pydantic-ai"

    def __init__(self, runner: PydanticAIRunner) -> None:
        self._runner = runner

    async def invoke(self, request: CanonicalModelRequest) -> CanonicalModelResponse:
        if request.provider_name != self.name:
            raise ValueError("PydanticAIProvider requires provider_name='pydantic-ai'")
        result = await self._runner(request)
        usage = dict(result.usage or {})
        if any(not isinstance(value, int) or value < 0 for value in usage.values()):
            raise ValueError("Pydantic AI usage values must be non-negative integers")
        return CanonicalModelResponse(
            request_id=request.request_id,
            provider_name=self.name,
            requested_model_name=request.model_name,
            returned_model_name=result.returned_model_name or request.model_name,
            provider_run_id=result.provider_run_id,
            completed_at=datetime.now(UTC),
            stop_reason=result.stop_reason,
            output_text=result.output_text,
            tool_proposals=result.tool_proposals,
            usage=usage,
        )

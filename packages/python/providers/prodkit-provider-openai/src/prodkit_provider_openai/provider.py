from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from prodkit_control_core import CanonicalModelRequest, CanonicalModelResponse, ToolProposal

OpenAITransport = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


class OpenAIProvider:
    """Normalize OpenAI Chat Completions wire responses into canonical provider contracts."""

    name = "openai"

    def __init__(self, transport: OpenAITransport) -> None:
        self._transport = transport

    async def invoke(self, request: CanonicalModelRequest) -> CanonicalModelResponse:
        if request.provider_name != self.name:
            raise ValueError("OpenAIProvider requires provider_name='openai'")
        parameters = dict(request.parameters)
        if "model" in parameters or "messages" in parameters:
            raise ValueError("provider parameters cannot override canonical model or messages")
        payload: dict[str, Any] = {
            "model": request.model_name,
            "messages": [dict(message) for message in request.messages],
            **parameters,
        }
        raw = await self._transport("/v1/chat/completions", payload)
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ValueError("OpenAI response is missing choices[0]")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise ValueError("OpenAI response is missing choices[0].message")
        output_text = self._content_text(message.get("content"))
        tool_proposals = self._tool_proposals(message.get("tool_calls"))
        usage = self._usage(raw.get("usage"))
        returned_model = raw.get("model")
        provider_run_id = raw.get("id")
        fingerprint = raw.get("system_fingerprint")
        metadata: dict[str, str | int | float | bool | None] = {}
        if isinstance(fingerprint, str):
            metadata["system_fingerprint"] = fingerprint
        return CanonicalModelResponse(
            request_id=request.request_id,
            provider_name=self.name,
            requested_model_name=request.model_name,
            returned_model_name=returned_model if isinstance(returned_model, str) else None,
            provider_run_id=provider_run_id if isinstance(provider_run_id, str) else None,
            completed_at=datetime.now(UTC),
            stop_reason=str(choice["finish_reason"])
            if choice.get("finish_reason") is not None
            else None,
            output_text=output_text,
            tool_proposals=tool_proposals,
            usage=usage,
            metadata=metadata,
        )

    @staticmethod
    def _content_text(content: object) -> str | None:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, Mapping):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts) or None
        return None

    @staticmethod
    def _tool_proposals(value: object) -> tuple[ToolProposal, ...]:
        if not isinstance(value, list):
            return ()
        proposals: list[ToolProposal] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            function = item.get("function")
            if not isinstance(function, Mapping):
                continue
            tool_call_id = item.get("id")
            name = function.get("name")
            raw_arguments = function.get("arguments", "{}")
            if (
                not isinstance(tool_call_id, str)
                or not tool_call_id
                or not isinstance(name, str)
                or not name
            ):
                raise ValueError("OpenAI tool call is missing id or function name")
            if isinstance(raw_arguments, str):
                parsed = json.loads(raw_arguments or "{}")
            else:
                parsed = raw_arguments
            if not isinstance(parsed, dict):
                raise ValueError("OpenAI tool arguments must decode to an object")
            proposals.append(
                ToolProposal(tool_call_id=tool_call_id, tool_name=name, arguments=parsed)
            )
        return tuple(proposals)

    @staticmethod
    def _usage(value: object) -> dict[str, int]:
        if not isinstance(value, Mapping):
            return {}
        mapping = {
            "input_tokens": value.get("prompt_tokens"),
            "output_tokens": value.get("completion_tokens"),
            "total_tokens": value.get("total_tokens"),
        }
        return {key: item for key, item in mapping.items() if isinstance(item, int) and item >= 0}

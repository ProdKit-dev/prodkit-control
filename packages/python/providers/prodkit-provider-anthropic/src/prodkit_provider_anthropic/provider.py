from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from prodkit_control_core import CanonicalModelRequest, CanonicalModelResponse, ToolProposal

AnthropicTransport = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


class AnthropicProvider:
    """Normalize Anthropic Messages API responses into canonical provider contracts."""

    name = "anthropic"

    def __init__(self, transport: AnthropicTransport) -> None:
        self._transport = transport

    async def invoke(self, request: CanonicalModelRequest) -> CanonicalModelResponse:
        if request.provider_name != self.name:
            raise ValueError("AnthropicProvider requires provider_name='anthropic'")
        parameters = dict(request.parameters)
        for reserved in ("model", "messages", "system"):
            if reserved in parameters:
                raise ValueError(f"provider parameters cannot override canonical {reserved}")
        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            role = message.get("role")
            content = message.get("content")
            if role == "system":
                if isinstance(content, str):
                    system_parts.append(content)
                continue
            if role not in {"user", "assistant"}:
                raise ValueError(f"unsupported Anthropic message role: {role!r}")
            messages.append({"role": role, "content": content})
        payload: dict[str, Any] = {
            "model": request.model_name,
            "messages": messages,
            "max_tokens": int(parameters.pop("max_tokens", 1024)),
            **parameters,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        raw = await self._transport("/v1/messages", payload)
        content = raw.get("content")
        if not isinstance(content, list):
            raise ValueError("Anthropic response is missing content")
        text_parts: list[str] = []
        proposals: list[ToolProposal] = []
        for item in content:
            if not isinstance(item, Mapping):
                continue
            kind = item.get("type")
            if kind == "text" and isinstance(item.get("text"), str):
                text_parts.append(str(item["text"]))
            elif kind == "tool_use":
                call_id = item.get("id")
                name = item.get("name")
                arguments = item.get("input", {})
                if (
                    not isinstance(call_id, str)
                    or not call_id
                    or not isinstance(name, str)
                    or not name
                ):
                    raise ValueError("Anthropic tool_use block is missing id or name")
                if not isinstance(arguments, dict):
                    raise ValueError("Anthropic tool_use input must be an object")
                proposals.append(
                    ToolProposal(tool_call_id=call_id, tool_name=name, arguments=arguments)
                )
        usage_raw = raw.get("usage")
        usage: dict[str, int] = {}
        if isinstance(usage_raw, Mapping):
            for canonical, provider_key in (
                ("input_tokens", "input_tokens"),
                ("output_tokens", "output_tokens"),
            ):
                value = usage_raw.get(provider_key)
                if isinstance(value, int) and value >= 0:
                    usage[canonical] = value
            if "input_tokens" in usage and "output_tokens" in usage:
                usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        returned_model = raw.get("model")
        provider_run_id = raw.get("id")
        stop_reason = raw.get("stop_reason")
        return CanonicalModelResponse(
            request_id=request.request_id,
            provider_name=self.name,
            requested_model_name=request.model_name,
            returned_model_name=returned_model if isinstance(returned_model, str) else None,
            provider_run_id=provider_run_id if isinstance(provider_run_id, str) else None,
            completed_at=datetime.now(UTC),
            stop_reason=stop_reason if isinstance(stop_reason, str) and stop_reason else None,
            output_text="".join(text_parts) or None,
            tool_proposals=tuple(proposals),
            usage=usage,
        )
